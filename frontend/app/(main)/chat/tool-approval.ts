import { toolDisplayName } from '../workspace/mcp-servers/tool-rules';
import type { EditError, JsonSchema, MessagePart, ToolApprovalDecision, ToolApprovalDetails } from './types';

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
  const schema = raw.inputSchema;
  const edited = raw.editedArguments;
  const editErrors = Array.isArray(raw.editErrors)
    ? raw.editErrors.filter((e): e is EditError => !!e && typeof e.path === 'string' && typeof e.message === 'string')
    : [];
  return {
    approvalId,
    instanceId: text('instanceId') ?? '',
    serverName,
    toolName,
    toolTitle: text('toolTitle'),
    readOnly: raw.readOnly === true,
    kind: raw.kind === 'read' || raw.kind === 'write' || raw.kind === 'destructive' ? raw.kind : null,
    // Cards saved before it was sent were all MCP cards, which can.
    canRemember: raw.canRemember !== false,
    expiresAt: raw.expiresAt,
    arguments: args && typeof args === 'object' && !Array.isArray(args) ? (args as Record<string, unknown>) : null,
    ...(text('argumentsPreview') ? { argumentsPreview: text('argumentsPreview') as string } : {}),
    ...(text('toolPath') ? { toolPath: text('toolPath') as string } : {}),
    ...(raw.source === 'mcp' || raw.source === 'toolset' ? { source: raw.source } : {}),
    ...(schema && typeof schema === 'object' && !Array.isArray(schema) ? { inputSchema: schema as JsonSchema } : {}),
    ...(text('argsHash') ? { argsHash: text('argsHash') as string } : {}),
    ...(typeof raw.editable === 'boolean' ? { editable: raw.editable } : {}),
    ...(text('argsSummary') ? { argsSummary: text('argsSummary') as string } : {}),
    ...(text('displayName') ? { displayName: text('displayName') as string } : {}),
    ...(editErrors.length ? { editErrors } : {}),
    ...(edited && typeof edited === 'object' && !Array.isArray(edited) ? { editedArguments: edited as Record<string, unknown> } : {}),
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

// shortcut: a fixed list of products a hub server (Atlassian's covers Jira and Confluence) can act in;
// move to the server's own metadata once MCP servers describe their products.
const PRODUCTS = ['Jira', 'Confluence', 'Slack', 'Gmail', 'Outlook', 'Notion', 'GitHub', 'GitLab', 'Asana', 'Trello', 'Zendesk', 'Salesforce', 'HubSpot', 'SharePoint', 'OneDrive', 'Dropbox', 'Figma', 'Linear'];

/** Where a call acts, as a person names it: the product its tool names ("createJiraIssue" on
 * "Atlassian Rovo" is Jira), else the server. */
export function approvalProduct(details: Pick<ToolApprovalDetails, 'toolName' | 'serverName'>): string {
  const words = new Set(details.toolName.split(/[_\-\s.]+|(?<=[a-z0-9])(?=[A-Z])/).map((w) => w.toLowerCase()));
  const server = details.serverName.toLowerCase();
  const product = PRODUCTS.find((p) => words.has(p.toLowerCase()));
  return product && !server.includes(product.toLowerCase()) ? product : details.serverName;
}

export interface SettledToolApproval {
  key: string;
  tool: string;
  server: string;
  /** `allowedNotRun`: allowed, but stopped before it ran (a Deny set since, or the tool is gone). */
  answer: NonNullable<MessagePart['approvalAnswer']> | 'allowedNotRun';
  /** Allowed after the person changed these arguments. */
  changedFields?: string[];
  /** Declined with this note for the agent. */
  note?: string;
}

// How the server words a decline with a note (Python `tool_approvals._settle`, `Outcome.preview`).
const DECLINED_NOTE = /^Declined: ([\s\S]+)$/;

/** The cards this reply answered, in order, at any depth. */
export function settledToolApprovals(parts: MessagePart[] | undefined): SettledToolApproval[] {
  const settled: SettledToolApproval[] = [];
  const walk = (list: MessagePart[] | undefined) => {
    for (const part of list ?? []) {
      if (part.type === 'tool_call' && part.approvalAnswer) {
        const details = toolApprovalDetails(part.approval);
        // The preview is cut at the saved size; parts saved before the note was sent have only it.
        const note = part.approvalAnswer === 'declined' ? part.approvalNote || DECLINED_NOTE.exec(part.resultPreview ?? '')?.[1] : undefined;
        const answer = part.approvalAnswer === 'allowed' && part.status === 'blocked' ? 'allowedNotRun' : part.approvalAnswer;
        settled.push({
          key: part.toolCallId ?? String(settled.length),
          tool: details ? approvalToolLabel(details) : part.displayName || toolDisplayName(part.toolName ?? ''),
          server: details?.serverName ?? '',
          answer,
          ...(answer === 'allowed' && part.changedFields?.length ? { changedFields: part.changedFields } : {}),
          ...(note ? { note } : {}),
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

// Argument names that hold a point in time (`post_at`, `due_date`, `start_time`, `timestamp`, `ts`).
const TIME_ARG_NAME = /(^|[_\-.])(at|ts|time|timestamp|date|datetime)$|^(ts|timestamp)$|(At|Time|Timestamp|Date)$/;
// Unix seconds between 2001 and 2286, or the same range in milliseconds.
const EPOCH_SECONDS = { min: 1e9, max: 1e10 };
const EPOCH_MILLIS = { min: 1e12, max: 1e13 };

/** A Unix time (seconds or milliseconds) under a time-like argument name, as a local date and time. */
export function formatEpochArgument(name: string, value: unknown): string | null {
  if (!TIME_ARG_NAME.test(name)) return null;
  const n = typeof value === 'number' ? value : typeof value === 'string' && /^\d{9,13}(\.\d+)?$/.test(value.trim()) ? Number(value) : NaN;
  if (!Number.isFinite(n)) return null;
  const ms = n >= EPOCH_MILLIS.min && n < EPOCH_MILLIS.max ? n : n >= EPOCH_SECONDS.min && n < EPOCH_SECONDS.max ? n * 1000 : NaN;
  if (!Number.isFinite(ms)) return null;
  return new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// A list item that reads the same in a comma list: a person approving a write must see where each
// item ends and what type it is, so `['admins, owners']` and `['admins', 'owners']`, or `[1, true]`
// and `['1', 'true']`, cannot look alike.
function plainListItem(item: unknown): boolean {
  return typeof item === 'string' && item !== '' && !item.includes(',') && item.trim() === item;
}

/** An argument's value for a person to read before approving: Unix times under time-like names as
 * dates, lists of plain text as a comma list (as JSON when the list is empty, holds numbers or booleans,
 * or an item would blur into its neighbour), and nested values as indented JSON. */
export function approvalValueText(name: string, value: unknown): string {
  if (typeof value === 'string') return formatEpochArgument(name, value) ?? value;
  if (typeof value === 'number') return formatEpochArgument(name, value) ?? String(value);
  if (Array.isArray(value) && value.length > 0 && value.every(plainListItem)) {
    return value.map((item) => String(item)).join(', ');
  }
  if (Array.isArray(value) && value.every((item) => item === null || typeof item !== 'object')) {
    return JSON.stringify(value);
  }
  if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2);
  return JSON.stringify(value) ?? String(value);
}

/** A value cut to what the card shows before "Show all". */
export function clipValue(text: string, full: boolean): { text: string; cut: boolean } {
  const cut = !full && text.length > MAX_VALUE_CHARS;
  return { text: cut ? `${text.slice(0, MAX_VALUE_CHARS)}…` : text, cut };
}
