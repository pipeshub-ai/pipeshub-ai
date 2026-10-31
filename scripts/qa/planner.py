"""Model adapters. Each task reads one input file and writes one reply file; any
failure is reported as a status, and the task falls back to its empty result."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qa import paths, schema
from qa.report import redact

MAX_INPUT_BYTES = 200_000
# Read from the engine checkout (the base branch in CI), so a PR cannot rewrite the prompt that plans it.
PROMPT = "scripts/qa/prompts/planner.md"
# Every task gets READ_ONLY_TOOLS: the model never writes files; the engine writes what a task returns.
TASKS: dict[str, dict[str, Any]] = {
    "plan": {
        "prompt": PROMPT,
        "schema": "plan-llm.v1.json",
        "diff": True,
        "empty": {"schema": "qa-plan-llm/v1", "add": [], "add_matrix": [], "missing": [], "risk": {"level": "low", "reasons": []}},
    },
}
READ_ONLY_TOOLS = "Read,Glob,Grep,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(grep:*)"
FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for block in reversed(FENCED.findall(text)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object in model output")


def _prompt(task: str) -> str:
    # Policy: prompt and notes come from the engine checkout, never from the tree under test.
    name = "planner_notes.md" if task == "plan" else f"{task}_notes.md"
    return (paths.REPO / TASKS[task]["prompt"]).read_text(encoding="utf-8") + "".join(
        "\n\n## Notes for this repository\n\n" + n.read_text(encoding="utf-8")
        for n in (o / name for o in paths.overlays(paths.REPO)) if n.is_file()
    )


def _instructions(root: Path, input_path: Path, task: str = "plan") -> str:
    files = f"`{input_path}`"
    if TASKS[task]["diff"]:
        files += f" and `{input_path.parent / 'changeset.diff'}`"
    return f"{_prompt(task)}\n\n## This run\n\nRead {files}. Reply with the JSON object only."


def _none(root: Path, input_path: Path, timeout: int, task: str = "plan") -> dict[str, Any]:
    return copy.deepcopy(TASKS[task]["empty"])


def _claude_code(root: Path, input_path: Path, timeout: int, task: str = "plan") -> dict[str, Any]:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found")
    cmd = [exe, "-p", _instructions(root, input_path, task), "--output-format", "json",
           "--max-turns", os.environ.get("QA_PLANNER_MAX_TURNS", "15"), "--allowedTools", READ_ONLY_TOOLS]
    if os.environ.get("QA_PLANNER_MODEL"):
        cmd += ["--model", os.environ["QA_PLANNER_MODEL"]]
    out = subprocess.run(cmd, check=False, cwd=root, capture_output=True, text=True, timeout=timeout)
    try:
        envelope = json.loads(out.stdout)
    except json.JSONDecodeError:
        envelope = None
    if out.returncode != 0 or not isinstance(envelope, dict) or envelope.get("is_error"):
        # The CLI reports max-turns and API errors in the stdout envelope, not stderr.
        detail = out.stderr.strip()
        if isinstance(envelope, dict):
            detail = " ".join(str(envelope.get(k)) for k in ("subtype", "result") if envelope.get(k)) or detail
        raise RuntimeError(f"claude exited {out.returncode}: {(detail or out.stdout.strip())[:300]}")
    return extract_json(envelope.get("result", ""))


def _command(root: Path, input_path: Path, timeout: int, task: str = "plan") -> dict[str, Any]:
    """Any agent CLI: QA_PLANNER_CMD with {prompt_file}, {input} and {output} placeholders."""
    template = os.environ.get("QA_PLANNER_CMD")
    if not template:
        raise RuntimeError("QA_PLANNER_CMD is not set")
    prompt_file = input_path.parent / "planner-prompt.md"
    prompt_file.write_text(_instructions(root, input_path, task), encoding="utf-8")
    output = input_path.parent / "plan-llm.raw"
    output.unlink(missing_ok=True)
    cmd = (template.replace("{prompt_file}", shlex.quote(str(prompt_file)))
           .replace("{input}", shlex.quote(str(input_path)))
           .replace("{output}", shlex.quote(str(output))))
    out = subprocess.run(cmd, check=False, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"planner command exited {out.returncode}: {out.stderr.strip()[:300]}")
    text = output.read_text(encoding="utf-8") if output.is_file() else out.stdout
    return extract_json(text)


INLINE = (
    "\n\n## This run\n\nYou cannot read files or run commands here: plan-input.json and the diff "
    "are in the next message. The catalogs are not available, so add only ids that appear in "
    "`candidates` or `floor`, exactly as written; any other id is dropped. Reply with the JSON object only."
)
INLINE_TASK = "\n\n## This run\n\nYou cannot read files or run commands here: the input is in the next message. Reply with the JSON object only."


def _capped(plan_input: dict[str, Any]) -> str:
    """The floor goes as ids only (the model adds, never explains it); candidates are ranked, so the tail is cut."""
    plan_input = {**plan_input, "floor": {"tests": plan_input["floor"]["tests"]}}
    candidates, plan_input["candidates"] = plan_input.get("candidates", []), []
    # shortcut: floor ids alone can exceed MAX_INPUT_BYTES and are sent anyway; trim or summarize them if a provider rejects such a payload.
    room = MAX_INPUT_BYTES - len(json.dumps(plan_input))
    for c in candidates:
        room -= len(json.dumps(c)) + 2
        if room < 0:
            break
        plan_input["candidates"].append(c)
    return json.dumps(plan_input)


def _litellm(root: Path, input_path: Path, timeout: int, task: str = "plan") -> dict[str, Any]:
    """Any provider LiteLLM supports (OpenAI, Azure OpenAI, Gemini, Bedrock, ...), keyed by its usual env vars."""
    model = os.environ.get("QA_PLANNER_MODEL")
    if not model:
        raise RuntimeError("QA_PLANNER_MODEL is not set")
    import litellm  # optional dependency; only this adapter needs it

    if task == "plan":
        payload = _capped(json.loads(input_path.read_text(encoding="utf-8")))
        diff_path = input_path.parent / "changeset.diff"
        diff = diff_path.read_text(encoding="utf-8")[:60000] if diff_path.is_file() else ""
        system, user = _prompt(task) + INLINE, f"plan-input.json:\n{payload}\n\nchangeset.diff:\n{diff}"
    else:
        system, user = _prompt(task) + INLINE_TASK, f"{input_path.name}:\n{input_path.read_text(encoding='utf-8')}"
    response = litellm.completion(
        model=model,
        timeout=timeout,
        drop_params=True,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"{model} returned no text")
    return extract_json(content)


ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "none": _none,
    "claude_code": _claude_code,
    "command": _command,
    "litellm": _litellm,
}


def default_adapter() -> str:
    return os.environ.get("QA_PLANNER", "claude_code")


def run(root: Path, adapter: str, input_path: Path, output_path: Path, timeout: int = 600) -> dict[str, Any]:
    return ask(root, adapter, "plan", input_path, output_path, timeout)


def ask(root: Path, adapter: str, task: str, input_path: Path, output_path: Path, timeout: int = 600) -> dict[str, Any]:
    """Always writes output_path; the status says whether the model contributed."""
    started = time.monotonic()
    status: dict[str, Any] = {"adapter": adapter, "model": os.environ.get("QA_PLANNER_MODEL")}
    try:
        if adapter not in ADAPTERS:
            raise RuntimeError(f"unknown adapter {adapter!r}; known: {sorted(ADAPTERS)}")
        # plan caps its own input (_capped); other tasks send theirs whole, so they are refused before any call.
        if task != "plan" and input_path.stat().st_size > MAX_INPUT_BYTES:
            raise RuntimeError(f"{input_path.name} is over {MAX_INPUT_BYTES} bytes")
        result = ADAPTERS[adapter](root, input_path, timeout, task)
        problems = schema.check(result, TASKS[task]["schema"])
        if problems:
            raise ValueError(f"reply does not match {TASKS[task]['schema']}: {'; '.join(problems[:3])}")
        status["status"] = "skipped" if adapter == "none" else "ok"
    except Exception as exc:  # noqa: BLE001 - any failure means "the task's empty result" (floor only for plan)
        result = _none(root, input_path, timeout, task)
        status.update(status="failed", error=redact(f"{type(exc).__name__}: {exc}")[:500])
    status["seconds"] = round(time.monotonic() - started, 1)
    result["_planner"] = status
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return status
