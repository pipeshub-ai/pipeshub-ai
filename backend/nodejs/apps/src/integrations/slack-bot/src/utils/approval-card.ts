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
const ARGUMENT_VALUE_MAX_CHARS = 200;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

/** Tool and model text kept plain in mrkdwn, where `<…>` is a link or a mention. */
function plain(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function argumentLines(args: Record<string, unknown> | null): string {
  return Object.entries(args ?? {})
    .slice(0, MAX_ARGUMENT_LINES)
    .map(([key, value]) => {
      const shown = typeof value === 'string' ? value : JSON.stringify(value);
      return `*${plain(key)}:* ${plain(truncate(shown, ARGUMENT_VALUE_MAX_CHARS))}`;
    })
    .join('\n');
}

export function buildApprovalCard(
  approval: ToolApprovalDetails,
  requesterSlackId: string,
): { text: string; blocks: KnownBlock[] } {
  const name =
    approval.displayName !== undefined && approval.displayName !== ''
      ? approval.displayName
      : approval.toolName;
  const title = `${name} on ${approval.serverName}`;
  const details =
    approval.argsSummary !== undefined && approval.argsSummary !== ''
      ? plain(approval.argsSummary)
      : argumentLines(approval.arguments);
  const expiresAtSeconds = Math.floor(approval.expiresAt / 1000);
  const notes = [
    `Waiting on <@${requesterSlackId}>`,
    `<!date^${String(expiresAtSeconds)}^Expires {time}|Expires ${new Date(approval.expiresAt).toUTCString()}>`,
    ...(approval.kind === 'destructive' ? ["Can't be undone"] : []),
  ];
  const value = JSON.stringify({
    approvalId: approval.approvalId,
    argsHash: approval.argsHash,
  });
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
    text: `Approval needed: ${title}`,
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
                text: truncate(details, SECTION_MAX_CHARS),
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

export function buildApprovalReceipt(
  kind: 'allowed' | 'declined' | 'expired',
  actorSlackId: string,
  note?: string,
): KnownBlock[] {
  let text = 'Expired';
  if (kind === 'allowed') text = `Approved by <@${actorSlackId}>`;
  if (kind === 'declined')
    text = `Declined by <@${actorSlackId}>${note !== undefined && note !== '' ? `: ${plain(note)}` : ''}`;
  return [
    {
      type: 'context',
      elements: [{ type: 'mrkdwn', text: truncate(text, SECTION_MAX_CHARS) }],
    },
  ];
}
