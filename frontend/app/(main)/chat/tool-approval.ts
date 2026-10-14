import { toolDisplayName } from '../workspace/mcp-servers/tool-rules';
import type { MessagePart, ToolApprovalDecision, ToolApprovalDetails } from './types';

const MAX_VALUE_CHARS = 300;
// How the app's answer to a card starts (Python `TOOL_APPROVAL_RESUME_PREFIX`).
const RESUME_PREFIX = 'Tool approval:';

/** The query that answers a card. It carries on the reply the card is in; it is never shown. */
export function toolApprovalResumeQuery(decision: ToolApprovalDecision): string {
  return `${RESUME_PREFIX} ${decision}`;
}

export function isToolApprovalResumeQuery(text: string | undefined | null): boolean {
  return typeof text === 'string' && text.trimStart().startsWith(RESUME_PREFIX);
}

/** The card's details from a wire frame or a saved part; anything not shaped like them is dropped. */
export function toolApprovalDetails(value: unknown): ToolApprovalDetails | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const text = (key: string) => (typeof raw[key] === 'string' && raw[key] ? (raw[key] as string) : null);
  const approvalId = text('approvalId');
  const serverName = text('serverName');
  const toolName = text('toolName');
  if (!approvalId || !serverName || !toolName || typeof raw.expiresAt !== 'number') return null;
  const args = raw.arguments;
  return {
    approvalId,
    instanceId: text('instanceId') ?? '',
    serverName,
    toolName,
    toolTitle: text('toolTitle'),
    readOnly: raw.readOnly === true,
    kind: raw.kind === 'read' || raw.kind === 'write' || raw.kind === 'destructive' ? raw.kind : null,
    expiresAt: raw.expiresAt,
    arguments: args && typeof args === 'object' && !Array.isArray(args) ? (args as Record<string, unknown>) : null,
    ...(text('argumentsPreview') ? { argumentsPreview: text('argumentsPreview') as string } : {}),
  };
}

/** The call in this reply that is waiting for approval, at any depth (a sub-agent can ask too). */
export function findToolApproval(parts: MessagePart[] | undefined): ToolApprovalDetails | null {
  if (!parts) return null;
  let found: ToolApprovalDetails | null = null;
  for (const part of parts) {
    if (part.type === 'tool_call' && part.status === 'awaiting_approval') {
      found = toolApprovalDetails(part.approval) ?? found;
    } else if (part.type === 'sub_agent') {
      found = findToolApproval(part.parts) ?? found;
    }
  }
  return found;
}

/** Whether a reply ran a call a person approved: regenerating it could run that call again. */
export function hasApprovedCall(parts: MessagePart[] | undefined): boolean {
  return (parts ?? []).some((part) => (part.type === 'tool_call' && part.approved === true) || hasApprovedCall(part.parts));
}

/** How a card names its tool: the server's title, else its name made readable. */
export function approvalToolLabel(details: Pick<ToolApprovalDetails, 'toolTitle' | 'toolName'>): string {
  return toolDisplayName(details.toolName, details.toolTitle);
}

export interface SettledToolApproval {
  key: string;
  tool: string;
  server: string;
  /** `allowedNotRun`: allowed, but stopped before it ran (a Deny set since, or the tool is gone). */
  answer: NonNullable<MessagePart['approvalAnswer']> | 'allowedNotRun';
}

/** The cards this reply answered, in order, at any depth. */
export function settledToolApprovals(parts: MessagePart[] | undefined): SettledToolApproval[] {
  const settled: SettledToolApproval[] = [];
  const walk = (list: MessagePart[] | undefined) => {
    for (const part of list ?? []) {
      if (part.type === 'tool_call' && part.approvalAnswer) {
        const details = toolApprovalDetails(part.approval);
        settled.push({
          key: part.toolCallId ?? String(settled.length),
          tool: details ? approvalToolLabel(details) : part.displayName || toolDisplayName(part.toolName ?? ''),
          server: details?.serverName ?? '',
          answer: part.approvalAnswer === 'allowed' && part.status === 'blocked' ? 'allowedNotRun' : part.approvalAnswer,
        });
      }
      walk(part.parts);
    }
  };
  walk(parts);
  return settled;
}

// The server's closing words for a turn that stopped on a card (Python `tool_approvals._WAITING`).
const WAITING_LINE = /\s*Waiting for your approval to run [^\n]*\.\s*$/;

/** The answer without "Waiting for your approval to run …": the card says it. */
export function withoutWaitingLine(text: string): string {
  return text.replace(WAITING_LINE, '');
}

/** Whether a reply carries on from an answered approval card: the server won't regenerate it. */
export function answeredToolApproval(parts: MessagePart[] | undefined): boolean {
  return (parts ?? []).some(
    (part) => (part.type === 'tool_call' && Boolean(part.approvalAnswer)) || answeredToolApproval(part.parts)
  );
}

/** One line per argument, for a person to read before approving; long values are cut unless `full`. */
export function approvalArgumentLines(
  args: Record<string, unknown>,
  { full = false }: { full?: boolean } = {}
): Array<{ name: string; value: string; cut: boolean }> {
  return Object.entries(args).map(([name, value]) => {
    const shown = typeof value === 'string' ? value : JSON.stringify(value) ?? String(value);
    const cut = !full && shown.length > MAX_VALUE_CHARS;
    return { name, value: cut ? `${shown.slice(0, MAX_VALUE_CHARS)}…` : shown, cut };
  });
}
