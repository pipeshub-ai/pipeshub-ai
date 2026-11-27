"""Edition hooks for chat requests and the agent loop.

`get_chat_extension()` returns the hooks the running edition installs (see
`app.edition_services.chat_extension`). `ChatExtension` itself is the default:
every hook keeps the behaviour of a chat that belongs to one user, so an
edition only overrides what it changes.

Imports here stay lazy: `app.edition_services` imports this module early.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from logging import Logger

    from pydantic import BaseModel

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agents.agent_loop.cancellation.registry import RunOwner
    from app.agents.agent_loop.context import AgentContext
    from app.config.configuration_service import ConfigurationService
    from app.modules.agents.privacy.ledger import LabelLedger
    from app.modules.agents.service.agent_service import AgentService
    from app.modules.authz.chat_content_access import ChatContentAccess
    from app.services.artifact_registry.models import Actor
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["ChatExtension", "HistoryTurnRenderer", "default_chat_extension", "get_chat_extension"]


class HistoryTurnRenderer(Protocol):
    """How a `previousConversations` entry is shown to the model."""

    def user_text(self, content: str, turn: Mapping[str, Any]) -> str: ...

    def answer_text(self, answer: str, turn: Mapping[str, Any]) -> str: ...

    def other_turn(self, role: str, content: str, turn: Mapping[str, Any]) -> list[Message]:
        """Messages for a role other than `user_query` / `bot_response`."""
        ...


class ChatExtension:
    # ── request ─────────────────────────────────────────────────────────────
    def chat_query_model(self, base: type[BaseModel]) -> type[BaseModel]:
        """The model a chat request body is parsed with."""
        return base

    def query_fields(self, chat_query: BaseModel) -> dict[str, Any]:
        """Keys added to the query dict a chat route hands the agent loop."""
        return {}

    async def preflight(
        self, chat_query: BaseModel, *, graph_provider: IGraphDBProvider, config_service: ConfigurationService,
        user_id: str | None, org_id: str | None, logger: Logger,
    ) -> AsyncIterator[bytes] | None:
        """A stream that answers the request before the agent runs, or None."""
        return None

    # ── agent context ───────────────────────────────────────────────────────
    def chat_state_fields(self, chat_query: Mapping[str, Any]) -> dict[str, Any]:
        """Keys `build_initial_state` copies from the query dict into chat state."""
        return {}

    def context_extensions(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """`AgentContext.extensions` for a run built from chat state."""
        return {}

    def tool_state_fields(self, extensions: Mapping[str, Any]) -> dict[str, Any]:
        """Keys `AgentContext.to_tool_state` writes for its extensions."""
        return {}

    # ── agent loop ──────────────────────────────────────────────────────────
    def toolset_modules(self) -> tuple[str, ...]:
        """Extra toolset modules the registry discovers."""
        return ()

    def internal_toolset_keys(self) -> frozenset[str]:
        """Registry keys of the internal toolsets among `toolset_modules`, for while the registry
        is still empty."""
        return frozenset()

    async def skip_apps(self, context: AgentContext) -> set[str]:
        """Toolsets this run must not load."""
        return set()

    def mcp_server_allowed(self, context: AgentContext, instance: Mapping[str, Any]) -> bool:
        """Whether this run may use an attached MCP server; `instance` is its stored settings."""
        return True

    def top_level_only_tool_prefixes(self) -> tuple[str, ...]:
        """Tool name prefixes that are never handed to a delegate agent."""
        return ()

    def transcript_hidden_tool_suffixes(self) -> tuple[str, ...]:
        """Tools whose arguments and results the transcript does not keep."""
        return ()

    def sender_history(self, context: AgentContext) -> list[dict[str, Any]]:
        """The earlier turns this request's own decisions may rest on. Default: all of them."""
        return context.previous_conversations

    def resolve_resume(
        self, query: str, context: AgentContext, *, approval_resume: bool = False,
    ) -> tuple[str | None, str]:
        """`(ask_user_question answers, goal query)` for this request. The answers are the tapped
        selections, or the text typed while a card was open. `approval_resume`: it answers a tool
        approval card, so the goal is the turn that stopped on it."""
        from app.agents.agent_loop.resume import resolve_card_resume

        return resolve_card_resume(
            query, context.previous_conversations, approval_resume=approval_resume,
            conversation_id=context.conversation_id, run_id=context.run_id,
        )

    def install_run_hooks(
        self, hooks: HookRegistry, context: AgentContext, *, query: str, resume_answers: str | None,
    ) -> None:
        """Hooks that depend on the request (installed once the goal is known)."""

    def install_tool_result_hooks(self, hooks: HookRegistry, context: AgentContext) -> None:
        """POST_TOOL_USE hooks after the built-in ones."""

    def may_learn_skills(self, context: AgentContext) -> bool:
        """Whether a finished run may be distilled into a skill, which others' runs can load.
        Default: yes."""
        return True

    def reads_own_skills(self, context: AgentContext) -> bool:
        """Whether the run loads the person's own hand-written skills, besides the builtins and the
        skills assigned to its agent. Default: yes."""
        return True

    def shows_sender_email(self, context: AgentContext) -> bool:
        """Whether the model is given the asker's email address with their name. Default: yes."""
        return True

    def read_by_others(self, context: AgentContext) -> bool:
        """Whether people besides the person asking read this run's answer. Such a run offers the
        assistant nothing that is the person's own: no source they did not pick (`named_sources`), none
        of their personal toolsets or own MCP servers (`own_tools`); and no prompt of it holds an MCP
        server's notes (`MCPToolProvider`). `AgentContext.read_by_others` asks once per run. Default:
        nobody else does."""
        return False

    def install_egress_hooks(self, hooks: HookRegistry, context: AgentContext, *, query: str = "") -> None:
        """Hooks on every run, ahead of the approval gate, that decide whether a call may send what
        the run has read out of PipesHub; `query` is the message the run answers. Default: none."""

    def completion_events(self) -> frozenset[str]:
        """Custom events a non-streaming chat route returns with the answer (`collect_stream_outcome`)."""
        return frozenset()

    def history_renderer(self, context: AgentContext) -> HistoryTurnRenderer | None:
        """None renders history turns as they were sent."""
        return None

    def prompt_sections(self, context: AgentContext, user_context: str | None) -> dict[str, str | None]:
        """Prompt template sections derived from the user context."""
        return {"user_context": user_context}

    # ── answer label ────────────────────────────────────────────────────────
    def label_ledger(
        self, *, asker_id: str, conversation_id: str | None, run_id: str | None, service_identity: bool,
    ) -> LabelLedger:
        """The request's label ledger. `asker_id` is the person who asked (for a
        service agent, not the identity it runs as). Default: one that records
        nothing, so no label is sent."""
        from app.modules.agents.privacy.ledger import NOOP_LEDGER

        return NOOP_LEDGER

    def unopened_label(self) -> dict[str, Any] | None:
        """The label of an error answer written before any ledger opened, or None."""
        return None

    # ── access ──────────────────────────────────────────────────────────────
    def content_access(self) -> ChatContentAccess:
        """Who may read chat attachments and artifacts beyond the graph ACL."""
        from app.modules.authz.chat_content_access import NoChatContentAccess

        return NoChatContentAccess()

    def extra_service_scopes(self) -> frozenset[str]:
        """Service-token scopes accepted beyond the built-in ones."""
        return frozenset()

    def may_cancel_by_grant(self, owner: RunOwner, requester: RunOwner) -> bool:
        """Whether a cancel carrying a service grant may stop `owner`'s run."""
        return False

    # ── files ───────────────────────────────────────────────────────────────
    async def artifact_share_refusal(
        self,
        actor: Actor,
        artifact_id: str,
        artifact: Mapping[str, Any],
        config_service: ConfigurationService,
        caller: Mapping[str, Any] | None = None,
        targets: Sequence[str] = (),
    ) -> str | None:
        """Why the owner may not grant this file to the people ``targets`` names, as an error code; None when nothing
        refuses it. ``caller`` is what the request's credential says of itself (``tokenClass``, ``isOAuth``,
        ``mcpRelay``)."""
        return None

    # ── agents ──────────────────────────────────────────────────────────────
    def agent_service_class(self) -> type[AgentService]:
        from app.modules.agents.service.agent_service import AgentService

        return AgentService

    # ── startup ─────────────────────────────────────────────────────────────
    def install(self, container: object) -> None:
        """Called when a service container initializes."""

    async def run_startup_migrations(
        self, graph_provider: IGraphDBProvider, config_service: ConfigurationService, logger: Logger,
    ) -> None:
        """Edition migrations run by the connector service at startup."""


_DEFAULT = ChatExtension()


def default_chat_extension() -> ChatExtension:
    return _DEFAULT


def get_chat_extension() -> ChatExtension:
    from app import edition_services

    return edition_services.chat_extension()
