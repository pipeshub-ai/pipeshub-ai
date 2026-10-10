/**
 * An agent draft belongs to the person who asked for it. Every other viewer of the chat gets a
 * placeholder instead of its contents, on every path that returns messages.
 */

export const DRAFT_AGENT_TOOL = 'draft_agent';
/** Lists the requester's own collections and toolsets for drafting, so it is as private as the draft. */
export const LIST_AGENT_OPTIONS_TOOL = 'list_agent_options';

export interface RedactedDraft {
  redacted: true;
  authorId?: string;
}

interface IdLike {
  toString(): string;
}

interface ToolEntry {
  toolName?: string;
  toolResult?: unknown;
}

interface PartLike {
  type?: string;
  toolName?: string;
  args?: unknown;
  argsSummary?: unknown;
  resultPreview?: unknown;
  resultSummary?: unknown;
  parts?: PartLike[];
}

export interface DraftBearingMessage {
  requestedBy?: IdLike | string | null;
  tools?: ToolEntry[];
  parts?: PartLike[];
}

const isDraftTool = (name: string | undefined): boolean =>
  typeof name === 'string' && name.endsWith(DRAFT_AGENT_TOOL);

export const isRequesterPrivateTool = (name: string | undefined): boolean =>
  isDraftTool(name) ||
  (typeof name === 'string' && name.endsWith(LIST_AGENT_OPTIONS_TOOL));

const idOf = (value: IdLike | string | null | undefined): string | undefined =>
  value === undefined || value === null ? undefined : String(value);

const scrubParts = (parts: PartLike[]): PartLike[] =>
  parts.map((part) => {
    const nested = part.parts ? { parts: scrubParts(part.parts) } : {};
    if (part.type !== 'tool_call' || !isRequesterPrivateTool(part.toolName)) {
      return part.parts ? { ...part, ...nested } : part;
    }
    const {
      argsSummary: _argsSummary,
      resultSummary: _resultSummary,
      ...rest
    } = part;
    return { ...rest, ...nested, args: '{}', resultPreview: '' };
  });

const touchesDraft = (message: DraftBearingMessage): boolean => {
  const hasTool = (message.tools ?? []).some((t) =>
    isRequesterPrivateTool(t.toolName),
  );
  const hasPart = (parts: PartLike[] | undefined): boolean =>
    (parts ?? []).some(
      (p) =>
        (p.type === 'tool_call' && isRequesterPrivateTool(p.toolName)) ||
        hasPart(p.parts),
    );
  return hasTool || hasPart(message.parts);
};

/** The message as `viewerId` may see it. Unchanged for the requester and for rows with no draft. */
export function redactAgentDraft<T extends object>(
  message: T,
  viewerId: string | undefined,
): T {
  const m = message as DraftBearingMessage;
  if (!touchesDraft(m)) return message;
  const requester = idOf(m.requestedBy);
  if (
    viewerId !== undefined &&
    requester !== undefined &&
    viewerId === requester
  ) {
    return message;
  }
  const redacted: RedactedDraft = {
    redacted: true,
    ...(requester !== undefined && { authorId: requester }),
  };
  const { agentDraftCreated: _created, ...rest } = message as T & {
    agentDraftCreated?: unknown;
  };
  return {
    ...(rest as T),
    ...(m.tools && {
      tools: m.tools.map((t) =>
        isDraftTool(t.toolName)
          ? { ...t, toolResult: redacted }
          : isRequesterPrivateTool(t.toolName)
            ? { ...t, toolResult: '' }
            : t,
      ),
    }),
    ...(m.parts && { parts: scrubParts(m.parts) }),
  };
}

export function redactAgentDrafts<T extends object>(
  messages: T[],
  viewerId: string | undefined,
): T[] {
  return messages.map((m) => redactAgentDraft(m, viewerId));
}
