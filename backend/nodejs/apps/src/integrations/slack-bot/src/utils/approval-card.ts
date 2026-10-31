import type { types } from '@slack/bolt';

type KnownBlock = types.KnownBlock;
type Button = types.Button;

/** The `approval` field of a TOOL_CALL_RESULT with `status: "awaiting_approval"`. */
export interface ToolApprovalDetails {
  approvalId: string;
  instanceId: string;
  serverName: string;
  toolName: string;
  toolTitle: string;
  readOnly: boolean;
  kind: 'read' | 'write' | 'destructive';
  canRemember: boolean;
  /** Epoch milliseconds. */
  expiresAt: number;
  arguments: Record<string, unknown> | null;
  argumentsPreview?: string;
  toolPath: string;
  source: string;
  inputSchema?: unknown;
  argsHash: string;
  editable: boolean;
  argsSummary?: string;
  displayName?: string;
  editErrors?: unknown;
}

export const APPROVAL_ALLOW_ACTION = 'approval_allow';
export const APPROVAL_CHANGE_ACTION = 'approval_change';
export const APPROVAL_DENY_ACTION = 'approval_deny';

const HEADER_MAX_CHARS = 150;
const SECTION_MAX_CHARS = 3000;
const MAX_ARGUMENT_LINES = 5;
// Five lines of these stay well under Slack's 3000-character section limit.
const ARGUMENT_KEY_MAX_CHARS = 100;
const ARGUMENT_VALUE_MAX_CHARS = 200;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

const ENTITIES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
};

/**
 * Tool and model text kept plain in mrkdwn, where `<…>` is a link or a
 * mention, in at most `max` characters; cut between entities, never inside one.
 */
function plain(text: string, max = Number.MAX_SAFE_INTEGER): string {
  const escaped = text.replace(/[&<>]/g, (ch) => ENTITIES[ch] ?? ch);
  if (escaped.length <= max) return escaped;
  let out = '';
  for (const ch of text) {
    const piece = ENTITIES[ch] ?? ch;
    if (out.length + piece.length > max - 3) break;
    out += piece;
  }
  return `${out}...`;
}

/**
 * What every button carries. Slack signs the payload, so a clicker can't
 * change it; the bot keeps nothing per card, so a restart or another replica
 * can still answer it.
 */
export interface ApprovalButtonValue {
  approvalId: string;
  argsHash: string;
  requesterSlackId: string;
  expiresAt: number;
  teamId: string;
}

export function parseApprovalButtonValue(
  value: string | undefined,
): ApprovalButtonValue | null {
  try {
    const parsed = JSON.parse(
      value ?? '',
    ) as Partial<ApprovalButtonValue> | null;
    if (
      typeof parsed?.approvalId !== 'string' ||
      typeof parsed.argsHash !== 'string' ||
      typeof parsed.requesterSlackId !== 'string' ||
      typeof parsed.expiresAt !== 'number' ||
      typeof parsed.teamId !== 'string'
    ) {
      return null;
    }
    const { approvalId, argsHash, requesterSlackId, expiresAt, teamId } =
      parsed;
    return { approvalId, argsHash, requesterSlackId, expiresAt, teamId };
  } catch {
    return null;
  }
}

function argumentLines(args: Record<string, unknown> | null): string {
  return Object.entries(args ?? {})
    .slice(0, MAX_ARGUMENT_LINES)
    .map(([key, value]) => {
      const shown = typeof value === 'string' ? value : JSON.stringify(value);
      return `*${plain(key, ARGUMENT_KEY_MAX_CHARS)}:* ${plain(shown, ARGUMENT_VALUE_MAX_CHARS)}`;
    })
    .join('\n');
}

export function buildApprovalCard(
  approval: ToolApprovalDetails,
  requesterSlackId: string,
  teamId: string,
): { text: string; blocks: KnownBlock[] } {
  const name =
    approval.displayName !== undefined && approval.displayName !== ''
      ? approval.displayName
      : approval.toolName;
  const title = `${name} on ${approval.serverName}`;
  const details =
    approval.argsSummary !== undefined && approval.argsSummary !== ''
      ? plain(approval.argsSummary, SECTION_MAX_CHARS)
      : argumentLines(approval.arguments);
  const expiresAtSeconds = Math.floor(approval.expiresAt / 1000);
  const notes = [
    `Waiting on <@${requesterSlackId}>`,
    `<!date^${String(expiresAtSeconds)}^Expires {time}|Expires ${new Date(approval.expiresAt).toUTCString()}>`,
    ...(approval.kind === 'destructive' ? ["Can't be undone"] : []),
  ];
  const buttonValue: ApprovalButtonValue = {
    approvalId: approval.approvalId,
    argsHash: approval.argsHash,
    requesterSlackId,
    expiresAt: approval.expiresAt,
    teamId,
  };
  const value = JSON.stringify(buttonValue);
  const button = (
    action_id: string,
    label: string,
    style?: Button['style'],
  ): Button => ({
    type: 'button',
    action_id,
    text: { type: 'plain_text', text: label },
    value,
    ...(style !== undefined ? { style } : {}),
  });
  const actions: types.ActionsBlock = {
    type: 'actions',
    elements: [
      button(
        APPROVAL_ALLOW_ACTION,
        'Approve',
        approval.kind === 'destructive' ? 'danger' : 'primary',
      ),
      button(APPROVAL_CHANGE_ACTION, 'Change something else'),
      button(APPROVAL_DENY_ACTION, 'Deny'),
    ],
  };
  return {
    text: `Approval needed: ${plain(title)}`,
    blocks: [
      {
        type: 'header',
        text: { type: 'plain_text', text: truncate(title, HEADER_MAX_CHARS) },
      },
      ...(details !== ''
        ? [
            {
              type: 'section' as const,
              text: {
                type: 'mrkdwn' as const,
                text: details,
              },
            },
          ]
        : []),
      {
        type: 'context',
        elements: [{ type: 'mrkdwn', text: notes.join(' · ') }],
      },
      actions,
    ],
  };
}

const RECEIPTS = {
  expired: 'Expired',
  answered: 'Already answered',
  sending: 'Sending your answer…',
};

export function buildApprovalReceipt(
  kind: 'allowed' | 'declined' | keyof typeof RECEIPTS,
  actorSlackId: string,
  note?: string,
): KnownBlock[] {
  let text: string;
  if (kind === 'allowed') text = `Approved by <@${actorSlackId}>`;
  else if (kind !== 'declined') text = RECEIPTS[kind];
  else {
    text = `Declined by <@${actorSlackId}>`;
    // The feedback is capped at 2000, so the line stays under the 3000 limit.
    if (note !== undefined && note !== '') text += `: ${plain(note, 2500)}`;
  }
  return [{ type: 'context', elements: [{ type: 'mrkdwn', text }] }];
}
