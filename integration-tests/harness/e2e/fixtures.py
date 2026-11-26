"""What a row needs on its stack besides the turn (04 §4.2), made through Node's public API as an admin would.

Each fixture is made once per stack and kept in the run's ``qa6-seed.json``, so rerunning rows against a stack left up
reuses them: a model per provider wire pointing at the scripted model, an agent per (tool family, engine, egress), the
Jira Data Center toolset instance with the lane's token, a skill. The row's settings (Labs flags, the deployment's
approval rule, the admin's own memory switch) are set per row to exactly what the row asks, then read back.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lane import Stack, StackError, wait_until
from plan import ROLE_KEY, AgentSpec, OrgSettings
from scripts import SKILL_NAME

Json = dict[str, Any]
FAKE_LLM = "http://pxh-fake-llm:8080"
FAKE_SAAS = "http://pxh-fake-saas:8090"
AWS_KEY_ID, AWS_SECRET, AWS_REGION = "AKIAE2EFAKE0000000000", "e2e-fake-secret", "us-east-1"
# Labs "Agent harness": on for every row; the path factor's kill switch is the operator's env setting (ProcessEnv).
AGENT_HARNESS_FLAG = "ENABLE_AGENT_HARNESS"
SCHEDULED_RUNS_FLAG = "ENABLE_SCHEDULED_RUNS"
# ENGPICK: the org's default coding engine and the admin's own choice (smoke_human.py's routes).
ORG_CODING_ENGINE = "/api/v1/agent-harness/admin/coding-engine"
CODING_ENGINE = "/api/v1/agent-harness/model-accounts/coding-engine"
MCP_NAME, MCP_INSTANCE_KEY = "e2esaas", "mcpInstanceId"
# The deployment's approval rule is the operator's document, written as run.sh writes it: in the app container.
SEED_CONFIG = ("python", "/e2e/pyrun.py", "/e2e/seed_config.py")
OWN_MEMORY = "/api/v1/agent-harness/memories/preference"
KB_RECORD = "# Onboarding guide\n\nNew joiners get a laptop on day one and meet their buddy on day two.\n"
JIRA_TOOLSET, JIRA_TYPE, JIRA_NAME = "Jira Data Center", "jiradatacenter", "QA6 Jira"


def _models(stack: Stack) -> dict[str, tuple[str, Json]]:
    """Each wire's provider configuration; the scripted model serves them all (fake_llm/wires.py)."""
    key = stack.env["E2E_LLM_KEY"]
    return {
        "openai_chat": ("openAICompatible", {"endpoint": f"{FAKE_LLM}/v1", "apiKey": key, "model": "e2e-llm"}),
        "azure_openai": ("azureOpenAI", {"endpoint": FAKE_LLM, "apiKey": key, "deploymentName": "e2e-llm", "model": "e2e-llm"}),
        "openai_responses": ("openAI", {"apiKey": key, "model": "gpt-5-mini"}),
        "anthropic_messages": ("anthropic", {"apiKey": key, "model": "claude-sonnet-4-5"}),
        "gemini": ("gemini", {"apiKey": key, "model": "gemini-2.5-flash"}),
        "bedrock": ("bedrock", {"awsAccessKeyId": AWS_KEY_ID, "awsAccessSecretKey": AWS_SECRET, "region": AWS_REGION,
                                "model": "anthropic.claude-3-5-sonnet-20240620-v1:0", "provider": "anthropic"}),
    }


def set_deployment_rules(stack: Stack, **fields: object) -> Json:
    """Sets these keys of the deployment's approval rule (a None removes one: the release default) and returns the
    rule as stored; seed_config.py reads it back and fails on a mismatch."""
    out = stack.exec_app(*SEED_CONFIG, "rules", json.dumps(fields))
    return json.loads(out.strip().splitlines()[-1])["default"]


class Fixtures:
    def __init__(self, stack: Stack, *, live: tuple[str, Json] | None = None) -> None:
        self.stack = stack
        self.live = live
        self.path = stack.run_dir / "qa6-seed.json"
        self.state: Json = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=1))

    def _remember(self, key: str, make: Any) -> Any:  # noqa: ANN401 - whatever the fixture is
        if key not in self.state:
            self.state[key] = make()
            self._save()
        return self.state[key]

    # --- models -----------------------------------------------------------------------

    def model(self, wire: str) -> Json:
        """The turn's model fields for a wire (the body's modelKey and modelName)."""
        if self.live is not None:
            provider, configuration = self.live
            wire = "live_azure_openai"
        elif wire == "openai_chat":
            return {"modelKey": self.stack.seed["llmKey"], "modelName": "e2e-llm"}
        else:
            provider, configuration = _models(self.stack)[wire]

        def make() -> Json:
            answer = self.stack.api.ok("POST", "/api/v1/configurationManager/ai-models/providers", {
                "modelType": "llm", "provider": provider, "configuration": configuration,
                # A reasoning model on OpenAI's own API is what the product calls through the Responses API.
                "isMultimodal": False, "isReasoning": wire == "openai_responses", "isDefault": False,
                "contextLength": None,
            }, timeout=180)
            model_key = ((answer or {}).get("details") or {}).get("modelKey")
            if not model_key:
                raise StackError(f"adding the {wire} model returned no model key: {str(answer)[:300]}")
            return {"modelKey": model_key, "modelName": configuration["model"]}

        return self._remember(f"model:{wire}", make)

    def role_model(self, wire: str) -> Json:
        """A wire's model as an agent's ``roleModels`` entry names it, and the wire, for the checks."""
        return {**self.model(wire), "provider": _models(self.stack)[wire][0], "wire": wire}

    # --- tools ------------------------------------------------------------------------

    def jira_instance(self) -> str:
        def make() -> str:
            api = self.stack.api
            existing = api.ok("GET", "/api/v1/toolsets/instances") or {}
            found = next((i for i in existing.get("instances") or [] if i.get("instanceName") == JIRA_NAME), None)
            if found is None:
                answer = api.ok("POST", "/api/v1/toolsets/instances", {
                    "instanceName": JIRA_NAME, "toolsetType": JIRA_TYPE, "authType": "API_TOKEN",
                    "authConfig": {"baseUrl": FAKE_SAAS}, "baseUrl": FAKE_SAAS,
                })
                found = (answer or {}).get("instance") or answer or {}
            instance_id = str(found.get("_id") or found.get("instanceId") or found.get("id") or "")
            if not instance_id:
                raise StackError(f"the Jira instance create returned no id: {str(found)[:300]}")
            api.ok("POST", f"/api/v1/toolsets/instances/{instance_id}/authenticate",
                   {"auth": {"apiToken": self.stack.env["E2E_JIRA_TOKEN"]}})
            return instance_id

        return self._remember("jiraInstanceId", make)

    def skill(self) -> str:
        def make() -> str:
            api = self.stack.api
            if api.request("GET", f"/api/v1/skills/{SKILL_NAME}").status != 200:
                api.ok("POST", "/api/v1/skills", {
                    "name": SKILL_NAME, "description": "The steps of a release checklist.",
                    "body": "# Release checklist\n\n1. Freeze.\n2. Tag.\n3. Announce.\n",
                })
            return SKILL_NAME

        return self._remember("skill", make)

    def project(self) -> str:
        """A project a new conversation is created in (04 §2.1 `mode=project`)."""

        def make() -> str:
            answer = self.stack.api.ok("POST", "/api/v1/projects", {"name": "QA6 project"})
            project = (answer or {}).get("project") or answer or {}
            project_id = str(project.get("_id") or project.get("id") or "")
            if not project_id:
                raise StackError(f"the project create returned no id: {str(answer)[:300]}")
            return project_id

        return self._remember("projectId", make)

    def knowledge_base(self) -> str:
        """A knowledge base with one indexed record the actor can read (04 §4.2 `knowledge`)."""

        def make() -> str:
            api = self.stack.api
            kb = api.ok("POST", "/api/v1/knowledgeBase", {"kbName": "QA6 Handbook"})
            kb_id = str((kb or {}).get("id") or (kb or {}).get("_key") or ((kb or {}).get("knowledgeBase") or {}).get("id") or "")
            if not kb_id:
                raise StackError(f"the knowledge base create returned no id: {str(kb)[:300]}")
            events = self.stack.upload(f"/api/v1/knowledgeBase/{kb_id}/upload", "onboarding-guide.md", KB_RECORD.encode())
            record = next((str(e.data.get("recordId")) for e in events if e.event == "file:succeeded"), None)
            if record is None:
                raise StackError(f"the upload reported no stored file: {[e.event for e in events]}")

            def indexed() -> bool:
                found = api.ok("GET", f"/api/v1/knowledgeBase/record/{record}") or {}
                return str((found.get("record") or {}).get("indexingStatus")) == "COMPLETED"

            wait_until(indexed, timeout=300, what="the knowledge base record to be indexed", every=3)
            return kb_id

        return self._remember("kbId", make)

    def _family_fields(self, family: str) -> Json:
        if family == "mcp":
            return {"mcpServers": [{"instanceId": self.stack.seed[MCP_INSTANCE_KEY], "name": MCP_NAME, "tools": [
                {"name": "lookup_note", "fullName": f"mcp_{MCP_NAME}_lookup_note"},
                {"name": "create_ticket", "fullName": f"mcp_{MCP_NAME}_create_ticket"},
            ]}]}
        if family == "toolset":
            return {"toolsets": [{"name": "jiradatacenter", "displayName": JIRA_TOOLSET, "type": "app",
                                  "instanceId": self.jira_instance(), "instanceName": JIRA_NAME,
                                  "tools": [{"name": "get_issue", "fullName": "jiradatacenter.get_issue"},
                                            {"name": "create_issue", "fullName": "jiradatacenter.create_issue"}]}]}
        if family == "skills":
            return {"skills": [{"name": self.skill()}]}
        if family == "knowledge":
            return {"knowledge": [{"connectorId": self.knowledge_base()}]}
        return {}

    def agent(self, spec: AgentSpec, role_model: Json | None = None) -> str:
        """The agent of a row; with ``role_model``, every sub-agent role of it is set to that model (MA1)."""
        key = f"agent:{spec.family}:{spec.engine}:{spec.egress}"
        if role_model is not None:
            key += f":{ROLE_KEY}={role_model['wire']}"

        def make() -> str:
            api = self.stack.api
            body: Json = {"name": "QA6 " + key.removeprefix("agent:").replace(":", " "),
                          "description": "An options-matrix agent.", "systemPrompt": "You are a careful analyst."}
            if spec.engine != "none":
                # Egress stays the release profile's (`none`): a layer may only narrow it (E4a), so the row's
                # sandbox level reaches an engine through the deployment's SANDBOX_* settings alone.
                body["engineProfile"] = {"engines": [spec.engine]}
            answer = api.ok("POST", "/api/v1/agents/create", body)
            agent = (answer or {}).get("agent") or answer or {}
            agent_key = str(agent.get("_key") or agent.get("agentKey") or "")
            if not agent_key:
                raise StackError(f"agent create returned no key: {str(answer)[:300]}")
            fields = self._family_fields(spec.family)
            if role_model is not None:
                fields["roleModels"] = {ROLE_KEY: {k: role_model[k] for k in ("modelKey", "modelName", "provider")}}
            if fields:
                api.ok("PUT", f"/api/v1/agents/{agent_key}", fields)
            return agent_key

        return self._remember(key, make)

    # --- org settings -------------------------------------------------------------------

    def apply_org(self, org: OrgSettings) -> None:
        if self.state.get("org") == asdict(org):
            return
        self._flags({AGENT_HARNESS_FLAG: True, SCHEDULED_RUNS_FLAG: org.scheduled_runs})
        set_deployment_rules(self.stack, writes="ask" if org.writes == "ask" else None,
                             flow={"level": "deny"} if org.flow_deny else None)
        if org.memory:
            # Every org keeps each person's own memories; a memory row needs only the admin's own switch on.
            stored = self.stack.api.ok("PUT", OWN_MEMORY, {"remember": True}) or {}
            if stored.get("remember") is not True:
                raise StackError(f"the admin's memory switch reads back as {stored}")
        # ENGPICK: the default chat's job goes to the org's coding engine while the admin's own choice follows it.
        self.stack.api.ok("PUT", ORG_CODING_ENGINE,
                          {"defaultEngine": None if org.coding_engine == "none" else org.coding_engine})
        self.stack.api.ok("PUT", CODING_ENGINE, {"choice": "org_default"})
        self.state["org"] = asdict(org)
        self._save()

    def _flags(self, wanted: dict[str, bool]) -> None:
        api = self.stack.api
        current = api.ok("GET", "/api/v1/configurationManager/platform/settings") or {}
        flags = {**(current.get("featureFlags") or {}), **wanted}
        body: Json = {"featureFlags": flags,
                      "fileUploadMaxSizeBytes": current.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024}
        if current.get("agentHarness") is not None:
            body["agentHarness"] = current["agentHarness"]
        api.ok("POST", "/api/v1/configurationManager/platform/settings", body)
        stored = (api.ok("GET", "/api/v1/configurationManager/platform/settings") or {}).get("featureFlags") or {}
        wrong = {name: stored.get(name) for name, on in wanted.items() if stored.get(name) is not on}
        if wrong:
            raise StackError(f"Labs flags read back as {wrong}")
