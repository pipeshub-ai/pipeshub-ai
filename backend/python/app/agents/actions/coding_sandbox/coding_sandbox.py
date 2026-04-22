"""CodingSandbox toolset -- execute Python and TypeScript code in an isolated sandbox.

Exposes ``execute_python`` and ``execute_typescript`` tools to the agent.
Produced artifacts (files written to the output directory) are uploaded to
blob storage and surfaced as ``::artifact`` markers in the response.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import upload_artifacts_to_blob
from app.sandbox.manager import get_executor
from app.sandbox.models import ExecutionResult, SandboxLanguage
from app.sandbox.package_policy import PackageNotAllowedError, get_allowlist
from app.sandbox.redact import redact_sandbox_paths
from app.utils.conversation_tasks import register_task

logger = logging.getLogger(__name__)

# Pydantic schemas for tool inputs

class ExecutePythonInput(BaseModel):
    code: str = Field(
        ...,
        description=(
            "Python code to execute. "
            "IMPORTANT: The sandbox has NO internet access -- do not use urllib, requests, httpx, or any network calls. "
            "All data must be passed inline in the code or generated within the code itself. "
            "Write output files to the path in the OUTPUT_DIR environment variable. "
            "Example: open(os.path.join(os.environ['OUTPUT_DIR'], 'chart.png'), 'wb')"
        ),
    )
    requirements: list[str] = Field(
        default_factory=list,
        description=(
            "pip packages to install before execution (e.g. ['pandas', 'matplotlib']). "
            "Only packages on the sandbox allowlist are accepted; any other package will be rejected. "
            "Allowlist includes: pandas, numpy, scipy, scikit-learn, matplotlib, seaborn, "
            "pillow, openpyxl, python-docx, python-pptx, reportlab, pypdf, pdfplumber, plotly, "
            "kaleido, fpdf2, cairosvg, beautifulsoup4, lxml, jinja2, and a curated set more."
        ),
    )


class ExecuteTypeScriptInput(BaseModel):
    code: str = Field(
        ...,
        description=(
            "TypeScript code to execute. "
            "IMPORTANT: The sandbox has NO internet access -- do not use fetch, axios, http, or any network calls. "
            "All data must be passed inline in the code or generated within the code itself. "
            "Write output files to the path in the OUTPUT_DIR environment variable. "
            "Example: fs.writeFileSync(path.join(process.env.OUTPUT_DIR!, 'report.html'), html)"
        ),
    )
    packages: list[str] = Field(
        default_factory=list,
        description=(
            "npm packages to install before execution (e.g. ['chart.js', 'docx']). "
            "Only packages on the sandbox allowlist are accepted; any other package will be rejected. "
            "Allowlist: fs-extra, sharp, @types/node, csv-stringify, json2csv, chart.js, "
            "docx, pdfkit, jsdom, xlsx, papaparse."
        ),
    )


# -------------------------------------------------------------------
# Toolset registration
# -------------------------------------------------------------------

@ToolsetBuilder("Coding Sandbox")\
    .in_group("Internal Tools")\
    .with_description("Execute Python and TypeScript code in a sandboxed environment to generate artifacts like charts, documents, images, and data files")\
    .with_category(ToolsetCategory.CODE_EXECUTION)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/coding_sandbox.svg"))\
    .build_decorator()
class CodingSandbox:
    """Code execution sandbox exposed to agents."""

    def __init__(self, state: ChatState) -> None:
        self.chat_state = state

    def _result(self, success: bool, payload: dict[str, Any]) -> tuple[bool, str]:
        return success, json.dumps(payload, default=str)

    def _audit_log(
        self,
        tool_name: str,
        language: SandboxLanguage,
        packages: list[str] | None,
        code: str,
    ) -> None:
        """Structured audit line for every code-execution tool invocation."""
        logger.info(
            "[%s] AUDIT | org_id=%s user_id=%s conversation_id=%s language=%s "
            "packages=%s code_length=%d",
            tool_name,
            self.chat_state.get("org_id"),
            self.chat_state.get("user_id"),
            self.chat_state.get("conversation_id"),
            language.value,
            packages or [],
            len(code),
        )
        logger.debug("[%s] CODE:\n%s", tool_name, code)

    def _package_rejection(self, exc: PackageNotAllowedError) -> tuple[bool, str]:
        """Convert a PackageNotAllowedError into a structured tool error.

        The LLM can read ``error_code`` and ``allowed_packages`` to retry the
        call with a valid package name.
        """
        logger.warning(
            "Sandbox package rejected: org_id=%s user_id=%s conversation_id=%s "
            "language=%s package=%s",
            self.chat_state.get("org_id"),
            self.chat_state.get("user_id"),
            self.chat_state.get("conversation_id"),
            exc.language.value,
            exc.package,
        )
        return self._result(False, {
            "success": False,
            "error_code": "package_not_allowed",
            "message": (
                f"Package {exc.package!r} is not on the {exc.language.value} sandbox "
                f"allowlist and will not be installed."
            ),
            "rejected_package": exc.package,
            "language": exc.language.value,
            "allowed_packages": list(get_allowlist(exc.language)),
        })

    # ------------------------------------------------------------------
    # Artifact upload helper
    # ------------------------------------------------------------------

    async def _upload_artifacts(
        self, exec_result: ExecutionResult, *, source_tool: str = "coding_sandbox.execute_python",
    ) -> list[dict[str, Any]]:
        """Upload produced artifacts to blob storage and create ArtifactRecords.

        Thin wrapper around :func:`app.sandbox.artifact_upload.upload_artifacts_to_blob`
        that resolves context (conversation / org / user / stores) from
        ``chat_state`` and returns the list of upload-info dicts.
        """
        if not exec_result.artifacts:
            return []

        conversation_id = self.chat_state.get("conversation_id")
        org_id = self.chat_state.get("org_id")
        user_id = self.chat_state.get("user_id")
        blob_store = self.chat_state.get("blob_store")
        graph_provider = self.chat_state.get("graph_provider")

        if blob_store is None:
            try:
                from app.modules.transformers.blob_storage import BlobStorage
                blob_store = BlobStorage(
                    logger=logger,
                    config_service=self.chat_state.get("config_service"),
                    graph_provider=graph_provider,
                )
            except (ImportError, OSError, RuntimeError, ValueError) as e:
                logger.warning("Could not init BlobStorage for artifact upload: %s", e)
                return []

        if not (conversation_id and org_id and blob_store):
            logger.warning(
                "Artifact upload skipped: conversation_id=%s org_id=%s blob_store=%s",
                conversation_id, org_id, "yes" if blob_store else "no",
            )
            return []

        from app.config.constants.arangodb import Connectors

        return await upload_artifacts_to_blob(
            exec_result.artifacts,
            blob_store=blob_store,
            org_id=org_id,
            conversation_id=conversation_id,
            user_id=user_id,
            graph_provider=graph_provider,
            connector_name=Connectors.CODING_SANDBOX,
            source_tool=source_tool,
        )

    def _schedule_artifact_upload(
        self, exec_result: ExecutionResult, *, source_tool: str = "coding_sandbox.execute_python",
    ) -> None:
        """Schedule artifact upload as a background conversation task."""
        conversation_id = self.chat_state.get("conversation_id")
        if not conversation_id or not exec_result.artifacts:
            return

        async def _upload() -> Optional[dict[str, Any]]:
            try:
                results = await self._upload_artifacts(exec_result, source_tool=source_tool)
                if results:
                    return {"type": "artifacts", "artifacts": results}
            except (OSError, ConnectionError, RuntimeError, ValueError):
                logger.exception("Background artifact upload failed")
            return None

        task = asyncio.create_task(_upload())
        register_task(conversation_id, task)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="coding_sandbox",
        tool_name="execute_python",
        args_schema=ExecutePythonInput,
        llm_description=(
            "Execute Python code in an isolated sandbox with NO internet access. "
            "The sandbox cannot make any network requests (no urllib, requests, httpx, API calls, web scraping, etc.). "
            "All data needed for computation must be embedded directly in the code as literals or variables. "
            "Use this to generate files such as charts (matplotlib/plotly), documents (python-docx/python-pptx), "
            "images (Pillow), CSV/Excel (pandas/openpyxl), PDF (reportlab/fpdf), or perform data processing. "
            "Write output files to os.path.join(os.environ['OUTPUT_DIR'], 'filename'). "
            "Common libraries are pre-installed: pandas, matplotlib, seaborn, plotly, openpyxl, "
            "python-docx, python-pptx, Pillow, reportlab, fpdf2, cairosvg. "
            "IMPORTANT: Do NOT print or echo the output file's absolute path (e.g. do not `print(out_path)` "
            "or `print(f'Saved at: {out_path}')`). Produced files are attached to the response automatically "
            "as artifacts; the UI renders download cards for them. Any file path printed to stdout is an "
            "internal sandbox path and must NEVER appear in your answer to the user."
        ),
        category=ToolCategory.CODE_EXECUTION,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User wants to generate a chart, graph, or visualization",
            "User wants to create a document (DOCX, PPTX, PDF)",
            "User wants to process data and produce output files",
            "User wants to generate an image or transform images",
            "User wants to create a CSV or Excel file from data",
            "User needs to run a computation that produces file output",
        ],
        when_not_to_use=[
            "User just wants a text answer or explanation",
            "User wants to query an existing database (use database tools instead)",
            "User wants to search knowledge base (use retrieval tools)",
            "The task requires downloading data from the internet or calling external APIs (the sandbox has no network access)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a bar chart of quarterly revenue",
            "Generate a DOCX report summarizing the data",
            "Make a pie chart showing market share",
            "Create an Excel spreadsheet with the results",
            "Generate a PDF invoice",
        ],
    )
    async def execute_python(self, code: str, requirements: list[str] | None = None) -> tuple[bool, str]:
        """Execute Python code in the sandbox and return results + artifacts."""
        self._audit_log("execute_python", SandboxLanguage.PYTHON, requirements, code)
        # Validate packages at the tool boundary so we can surface a rich,
        # retry-friendly error to the LLM. Executors ALSO re-validate as
        # defence in depth.
        try:
            from app.sandbox.models import validate_packages
            validate_packages(requirements, language=SandboxLanguage.PYTHON)
        except PackageNotAllowedError as exc:
            return self._package_rejection(exc)
        except ValueError as exc:
            return self._result(False, {
                "success": False,
                "error_code": "invalid_package_name",
                "message": str(exc),
            })

        try:
            executor = get_executor()
            logger.debug("[execute_python] executor=%s", type(executor).__name__)
            result = await executor.execute(
                code=code,
                language=SandboxLanguage.PYTHON,
                packages=requirements or [],
            )
            logger.info(
                "[execute_python] OUTPUT | success=%s exit_code=%s time_ms=%s artifacts=%d "
                "stdout_len=%d stderr_len=%d error=%s",
                result.success, result.exit_code, result.execution_time_ms,
                len(result.artifacts), len(result.stdout), len(result.stderr),
                result.error or "None",
            )
            if result.stdout:
                logger.debug("[execute_python] STDOUT:\n%s", _truncate(result.stdout, 2000))
            if result.stderr:
                log_fn = logger.warning if not result.success else logger.debug
                log_fn("[execute_python] STDERR:\n%s", _truncate(result.stderr, 2000))

            self._schedule_artifact_upload(result)

            artifact_info = [
                {"fileName": a.file_name, "mimeType": a.mime_type, "sizeBytes": a.size_bytes}
                for a in result.artifacts
            ]

            return self._result(result.success, {
                "success": result.success,
                "message": "Code executed successfully" if result.success else "Code execution failed",
                "stdout": redact_sandbox_paths(_truncate(result.stdout, 8000)),
                "stderr": redact_sandbox_paths(_truncate(result.stderr, 4000)),
                "exit_code": result.exit_code,
                "execution_time_ms": result.execution_time_ms,
                "artifacts": artifact_info,
                **({"error": redact_sandbox_paths(result.error)} if result.error else {}),
            })
        except Exception as e:
            logger.exception("[execute_python] EXCEPTION")
            return self._result(False, {"success": False, "error": redact_sandbox_paths(str(e))})

    @tool(
        app_name="coding_sandbox",
        tool_name="execute_typescript",
        args_schema=ExecuteTypeScriptInput,
        llm_description=(
            "Execute TypeScript code in an isolated sandbox with NO internet access. "
            "The sandbox cannot make any network requests (no fetch, axios, http, API calls, web scraping, etc.). "
            "All data needed for computation must be embedded directly in the code as literals or variables. "
            "Use this when JavaScript/TypeScript is more appropriate for the task. "
            "Write output files to path.join(process.env.OUTPUT_DIR!, 'filename'). "
            "Common packages are pre-installed: fs-extra, chart.js, sharp. "
            "IMPORTANT: Do NOT print or echo the output file's absolute path (e.g. do not "
            "`console.log(outPath)` or `console.log(`Saved at: ${outPath}`)`). Produced files are "
            "attached to the response automatically as artifacts; the UI renders download cards for "
            "them. Any file path printed to stdout is an internal sandbox path and must NEVER appear "
            "in your answer to the user."
        ),
        category=ToolCategory.CODE_EXECUTION,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User needs TypeScript/JavaScript-specific processing",
            "User wants to generate HTML/SVG visualizations",
            "User wants to use Node.js libraries for file generation",
        ],
        when_not_to_use=[
            "User just wants a text answer",
            "Python would be more suitable for the task",
            "The task requires downloading data from the internet or calling external APIs (the sandbox has no network access)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Generate an HTML report",
            "Create an SVG diagram",
        ],
    )
    async def execute_typescript(self, code: str, packages: list[str] | None = None) -> tuple[bool, str]:
        """Execute TypeScript code in the sandbox and return results + artifacts."""
        self._audit_log("execute_typescript", SandboxLanguage.TYPESCRIPT, packages, code)
        try:
            from app.sandbox.models import validate_packages
            validate_packages(packages, language=SandboxLanguage.TYPESCRIPT)
        except PackageNotAllowedError as exc:
            return self._package_rejection(exc)
        except ValueError as exc:
            return self._result(False, {
                "success": False,
                "error_code": "invalid_package_name",
                "message": str(exc),
            })

        try:
            executor = get_executor()
            logger.debug("[execute_typescript] executor=%s", type(executor).__name__)
            result = await executor.execute(
                code=code,
                language=SandboxLanguage.TYPESCRIPT,
                packages=packages or [],
            )
            logger.info(
                "[execute_typescript] OUTPUT | success=%s exit_code=%s time_ms=%s artifacts=%d "
                "stdout_len=%d stderr_len=%d error=%s",
                result.success, result.exit_code, result.execution_time_ms,
                len(result.artifacts), len(result.stdout), len(result.stderr),
                result.error or "None",
            )
            if result.stdout:
                logger.debug("[execute_typescript] STDOUT:\n%s", _truncate(result.stdout, 2000))
            if result.stderr:
                log_fn = logger.warning if not result.success else logger.debug
                log_fn("[execute_typescript] STDERR:\n%s", _truncate(result.stderr, 2000))

            self._schedule_artifact_upload(result, source_tool="coding_sandbox.execute_typescript")

            artifact_info = [
                {"fileName": a.file_name, "mimeType": a.mime_type, "sizeBytes": a.size_bytes}
                for a in result.artifacts
            ]

            return self._result(result.success, {
                "success": result.success,
                "message": "Code executed successfully" if result.success else "Code execution failed",
                "stdout": redact_sandbox_paths(_truncate(result.stdout, 8000)),
                "stderr": redact_sandbox_paths(_truncate(result.stderr, 4000)),
                "exit_code": result.exit_code,
                "execution_time_ms": result.execution_time_ms,
                "artifacts": artifact_info,
                **({"error": redact_sandbox_paths(result.error)} if result.error else {}),
            })
        except Exception as e:
            logger.exception("[execute_typescript] EXCEPTION")
            return self._result(False, {"success": False, "error": redact_sandbox_paths(str(e))})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... (truncated, {len(text)} total chars)"
