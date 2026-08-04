import { toolStatusLabel } from "./tool-display";
import {
  coerceAskUserQuestionEvent,
  isAskUserQuestionToolName,
  type AskUserQuestionEvent,
} from "./ask-user-format";

export interface AGUIJsonPatchOp {
  op: string;
  path: string;
  value?: unknown;
}

export interface AGUIEventEnvelope {
  type?: string;
  runId?: string;
  parentRunId?: string;
  name?: string;
  value?: unknown;
  delta?: unknown;
  message?: string;
  result?: unknown;
  snapshot?: unknown;
  stepName?: string;
  toolCallId?: string;
  toolCallName?: string;
  displayName?: string;
  status?: string;
  content?: unknown;
  resultSummary?: string;
  messageId?: string;
  [key: string]: unknown;
}

export interface SlackStreamEvent {
  event: string;
  data: unknown;
}

export interface SlackArtifactPayload {
  artifactId?: string;
  fileName?: string;
  downloadUrl?: string;
  mimeType?: string;
  recordId?: string;
  visibility?: string;
  [key: string]: unknown;
}

export interface SlackConversationComplete {
  _id: string;
  messages: Array<{
    content?: string;
    citations?: unknown[];
    messageType?: string;
  }>;
  [key: string]: unknown;
}

export interface SlackAGUICallbacks {
  onStatus?: (message: string) => void;
  onReasoning?: (delta: string, done: boolean) => void;
  /**
   * Settled root TEXT_MESSAGE preamble that preceded a tool call — same
   * narration the frontend keeps in the activity timeline.
   */
  onNarration?: (text: string) => void;
  onToolStart?: (toolName: string, displayName?: string) => void;
  onToolResult?: (
    toolName: string,
    displayName?: string,
    status?: string,
  ) => void;
  onSubAgent?: (role: string, phase: "started" | "completed" | "failed") => void;
  /** Full current answer text (caller diffs against last streamed length). */
  onAnswerAccumulated?: (accumulated: string) => void;
  onClearAnswerPreamble?: () => void;
  onAskUserQuestion?: (payload: AskUserQuestionEvent) => void;
  onArtifact?: (artifact: SlackArtifactPayload) => void;
  onConversationCreated?: (conversationId: string) => void;
  onComplete?: (conversation: SlackConversationComplete) => void;
  onError?: (message: string) => void;
}

interface AGUIStreamState {
  citations: unknown[];
  normalizedAnswer: string;
  confidence?: string;
  rawLength: number;
}

function asEnvelope(data: unknown): AGUIEventEnvelope | undefined {
  if (!data || typeof data !== "object") return undefined;
  return data as AGUIEventEnvelope;
}

function applyStatePatch(
  state: AGUIStreamState,
  ops: AGUIJsonPatchOp[],
): AGUIStreamState {
  let next = state;
  for (const op of ops) {
    if (op.op !== "replace" && op.op !== "add") continue;
    if (op.path === "/citations" && Array.isArray(op.value)) {
      next = { ...next, citations: op.value };
    } else if (op.path === "/normalizedAnswer" && typeof op.value === "string") {
      next = { ...next, normalizedAnswer: op.value };
    } else if (
      op.path === "/confidence" &&
      (typeof op.value === "string" || op.value === null)
    ) {
      next = { ...next, confidence: (op.value as string | null) ?? undefined };
    } else if (op.path === "/rawLength" && typeof op.value === "number") {
      next = { ...next, rawLength: op.value };
    }
  }
  return next;
}

function extractConversation(
  result: unknown,
): SlackConversationComplete | null {
  if (!result || typeof result !== "object") return null;
  const record = result as Record<string, unknown>;
  const conversation = record.conversation;
  if (!conversation || typeof conversation !== "object") return null;
  const conv = conversation as SlackConversationComplete;
  if (typeof conv._id !== "string") return null;
  return conv;
}

/**
 * Minimal root-run tracker: root runIds are registered on RUN_STARTED without
 * parentRunId; child runs (with parentRunId) are ignored for answer streaming.
 */
class RootRunTracker {
  private rootRunIds = new Set<string>();
  private childRunIds = new Set<string>();
  private pendingRoleByParent = new Map<string, string>();
  private childRoleByRunId = new Map<string, string>();

  handleStepStarted(stepName: string, runId?: string): string | null {
    if (!stepName.startsWith("sub_agent:") || !runId) return null;
    const role = stepName.slice("sub_agent:".length);
    this.pendingRoleByParent.set(runId, role);
    return role;
  }

  handleRunStarted(runId?: string, parentRunId?: string): string | null {
    if (!runId) return null;
    if (!parentRunId) {
      this.rootRunIds.add(runId);
      return null;
    }
    this.childRunIds.add(runId);
    const role = this.pendingRoleByParent.get(parentRunId) ?? "sub-agent";
    this.pendingRoleByParent.delete(parentRunId);
    this.childRoleByRunId.set(runId, role);
    return role;
  }

  handleRunEnded(runId?: string): string | null {
    if (!runId) return null;
    const role = this.childRoleByRunId.get(runId) ?? null;
    this.childRunIds.delete(runId);
    this.childRoleByRunId.delete(runId);
    return role;
  }

  isRootRun(runId: string | undefined): boolean {
    if (!runId) return true;
    if (this.childRunIds.has(runId)) return false;
    return true;
  }
}

/**
 * One handler per backend stream. Translates AG-UI frames into Slack-oriented
 * callbacks (mirrors frontend `createAGUIEventHandler`, without parts UI).
 */
export function createSlackAGUIEventHandler(
  callbacks: SlackAGUICallbacks,
): (event: SlackStreamEvent) => void {
  let textBuffer = "";
  let rawTextReceived = 0;
  let state: AGUIStreamState = {
    citations: [],
    normalizedAnswer: "",
    rawLength: 0,
  };
  const runs = new RootRunTracker();
  const toolNameById = new Map<string, { name: string; displayName?: string }>();
  /** Full tool args by call id — TOOL_CALL_RESULT.content is truncated to 200 chars. */
  const toolArgsById = new Map<string, unknown>();
  const askUserEmittedForCall = new Set<string>();

  const emitAnswer = (): void => {
    callbacks.onAnswerAccumulated?.(textBuffer);
  };

  const emitAskUserFromRaw = (raw: unknown, toolCallId?: string): void => {
    if (toolCallId && askUserEmittedForCall.has(toolCallId)) {
      return;
    }
    const askEvent = coerceAskUserQuestionEvent(raw);
    if (!askEvent) {
      return;
    }
    if (toolCallId) {
      askUserEmittedForCall.add(toolCallId);
    }
    callbacks.onAskUserQuestion?.(askEvent);
  };

  return (event: SlackStreamEvent): void => {
    const data = asEnvelope(event.data);
    const type = data?.type ?? event.event;

    switch (type) {
      case "STEP_STARTED": {
        const stepName = typeof data?.stepName === "string" ? data.stepName : "";
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        const role = runs.handleStepStarted(stepName, runId);
        if (role) {
          callbacks.onStatus?.(`Delegating to ${role}...`);
        }
        break;
      }

      case "RUN_STARTED": {
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        const parentRunId =
          typeof data?.parentRunId === "string" ? data.parentRunId : undefined;
        const role = runs.handleRunStarted(runId, parentRunId);
        if (role) {
          callbacks.onSubAgent?.(role, "started");
          callbacks.onStatus?.(`Delegating to ${role}...`);
        }
        break;
      }

      case "TEXT_MESSAGE_START": {
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (runs.isRootRun(runId)) {
          textBuffer = "";
          rawTextReceived = 0;
        }
        break;
      }

      case "TEXT_MESSAGE_CONTENT": {
        const delta = typeof data?.delta === "string" ? data.delta : "";
        if (!delta) break;
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (!runs.isRootRun(runId)) break;
        textBuffer += delta;
        rawTextReceived += delta.length;
        emitAnswer();
        break;
      }

      case "TEXT_MESSAGE_END": {
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (runs.isRootRun(runId)) {
          callbacks.onStatus?.("Thinking...");
        }
        break;
      }

      case "REASONING_MESSAGE_CONTENT": {
        const delta = typeof data?.delta === "string" ? data.delta : "";
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (delta && runs.isRootRun(runId)) {
          callbacks.onReasoning?.(delta, false);
        }
        break;
      }

      case "REASONING_END":
        callbacks.onReasoning?.("", true);
        break;

      case "TOOL_CALL_START": {
        const toolCallName =
          typeof data?.toolCallName === "string" ? data.toolCallName : "tool";
        const displayName =
          typeof data?.displayName === "string" ? data.displayName : undefined;
        const toolCallId =
          typeof data?.toolCallId === "string" ? data.toolCallId : undefined;
        if (toolCallId) {
          toolNameById.set(toolCallId, { name: toolCallName, displayName });
        }

        // Settle preamble into the activity timeline before the tool row so
        // narration appears above the tool (same order as the frontend).
        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (textBuffer && runs.isRootRun(runId)) {
          const narration = textBuffer.trim();
          textBuffer = "";
          rawTextReceived = 0;
          state = { ...state, normalizedAnswer: "", rawLength: 0 };
          if (narration) {
            callbacks.onNarration?.(narration);
          }
          callbacks.onClearAnswerPreamble?.();
          emitAnswer();
        }

        callbacks.onStatus?.(`${toolStatusLabel(toolCallName)}...`);
        callbacks.onToolStart?.(toolCallName, displayName);
        break;
      }

      case "TOOL_CALL_ARGS": {
        // Full ask_user args arrive here. TOOL_CALL_RESULT.content is
        // truncated to 200 chars in tool_loop.py, so JSON parse fails there.
        const toolCallId =
          typeof data?.toolCallId === "string" ? data.toolCallId : undefined;
        const meta = toolCallId ? toolNameById.get(toolCallId) : undefined;
        const toolCallName = meta?.name ?? "";
        if (typeof data?.delta === "string" && data.delta) {
          if (toolCallId) {
            toolArgsById.set(toolCallId, data.delta);
          }
          if (isAskUserQuestionToolName(toolCallName)) {
            emitAskUserFromRaw(data.delta, toolCallId);
          }
        }
        break;
      }

      case "TOOL_CALL_RESULT": {
        const toolCallId =
          typeof data?.toolCallId === "string" ? data.toolCallId : undefined;
        const meta = toolCallId ? toolNameById.get(toolCallId) : undefined;
        const stashedArgs = toolCallId ? toolArgsById.get(toolCallId) : undefined;
        if (toolCallId) {
          toolNameById.delete(toolCallId);
          toolArgsById.delete(toolCallId);
        }
        const toolCallName = meta?.name ?? "tool";
        const displayName = meta?.displayName;
        const status = typeof data?.status === "string" ? data.status : "completed";
        callbacks.onToolResult?.(toolCallName, displayName, status);

        if (isAskUserQuestionToolName(toolCallName)) {
          // Prefer full args; content is a 200-char preview and usually invalid JSON.
          emitAskUserFromRaw(stashedArgs ?? data?.content, toolCallId);
        }

        const runId = typeof data?.runId === "string" ? data.runId : undefined;
        if (runs.isRootRun(runId) && !isAskUserQuestionToolName(toolCallName)) {
          callbacks.onStatus?.("Thinking...");
        }
        break;
      }

      case "STATE_DELTA": {
        // Artifacts only for live Slack UX. Answer/citation rewrites are
        // applied in the final Block Kit message from RUN_FINISHED — emitting
        // normalizedAnswer mid-stream doubles append churn on top of
        // TEXT_MESSAGE_CONTENT tokens.
        const ops = Array.isArray(data?.delta)
          ? (data.delta as AGUIJsonPatchOp[])
          : [];
        const textOps: AGUIJsonPatchOp[] = [];
        for (const op of ops) {
          if (op.op === "add" && op.path === "/artifacts/-") {
            callbacks.onArtifact?.(op.value as SlackArtifactPayload);
          } else {
            textOps.push(op);
          }
        }
        if (textOps.length > 0) {
          state = applyStatePatch(state, textOps);
        }
        break;
      }

      case "CUSTOM": {
        const name = data?.name;
        const value = data?.value;
        if (name === "conversation_created") {
          const payload =
            value && typeof value === "object"
              ? (value as Record<string, unknown>)
              : {};
          const conversationId =
            (typeof payload.conversationId === "string" &&
              payload.conversationId) ||
            (typeof payload._id === "string" && payload._id) ||
            "";
          if (conversationId) {
            callbacks.onConversationCreated?.(conversationId);
          }
        } else if (name === "ask_user_question") {
          emitAskUserFromRaw(value);
        } else if (name === "artifact") {
          callbacks.onArtifact?.((value ?? {}) as SlackArtifactPayload);
        }
        break;
      }

      case "RUN_FINISHED": {
        if (data?.parentRunId) {
          const runId = typeof data.runId === "string" ? data.runId : undefined;
          const role = runs.handleRunEnded(runId);
          if (role) {
            callbacks.onSubAgent?.(role, "completed");
          }
          break;
        }
        const conversation = extractConversation(data?.result);
        if (conversation) {
          callbacks.onComplete?.(conversation);
        }
        break;
      }

      case "RUN_ERROR": {
        if (data?.parentRunId) {
          const runId = typeof data.runId === "string" ? data.runId : undefined;
          const role = runs.handleRunEnded(runId);
          if (role) {
            callbacks.onSubAgent?.(role, "failed");
          }
          break;
        }
        const message =
          typeof data?.message === "string" && data.message.trim()
            ? data.message.trim()
            : "Stream ended with an error";
        callbacks.onError?.(message);
        break;
      }

      case "STATE_SNAPSHOT": {
        const snapshot =
          data?.snapshot && typeof data.snapshot === "object"
            ? (data.snapshot as Record<string, unknown>)
            : undefined;
        if (!snapshot || snapshot.final === true) break;
        const status = typeof snapshot.status === "string" ? snapshot.status : "";
        const STATUS_MESSAGES: Record<string, string> = {
          starting: "Thinking...",
          calling_llm: "Thinking...",
          running_tool: "Executing tool...",
        };
        let message = STATUS_MESSAGES[status];
        if (message) {
          if (
            status === "running_tool" &&
            typeof snapshot.current_tool === "string"
          ) {
            message = `${toolStatusLabel(snapshot.current_tool)}...`;
          }
          callbacks.onStatus?.(message);
        }
        break;
      }

      default:
        break;
    }
  };
}
