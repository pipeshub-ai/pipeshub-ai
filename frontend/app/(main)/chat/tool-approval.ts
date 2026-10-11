import type { MessagePart, ToolApprovalDetails } from './types';

const MAX_VALUE_CHARS = 300;

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
    canAlwaysAllow: raw.canAlwaysAllow === true,
    companyAlwaysAsk: raw.companyAlwaysAsk === true,
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
