import type { types } from '@slack/bolt';

import {
  approvalLayout,
  approvalProduct,
  epochSeconds,
  toolLabel,
  valueText,
  type ApprovalField,
} from './approval-fields';

type KnownBlock = types.KnownBlock;
type Button = types.Button;

/** The `approval` field of a TOOL_CALL_RESULT with `status: "awaiting_approval"`. */
export interface ToolApprovalDetails {
  approvalId: string;
  instanceId: string;
  serverName: string;
  toolName: string;
  toolTitle?: string | null;
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
const ARGUMENT_KEY_MAX_CHARS = 100;
const ARGUMENT_VALUE_MAX_CHARS = 200;
const SEE_EVERYTHING = 'Open in PipesHub to see everything.';
// Room kept in the section for the "+N more" line.
const MORE_LINE_RESERVE = 80;

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

/** Block ids the card's answer replaces; the rest of the card stays as the record. */
export const APPROVAL_STATUS_BLOCK = 'approval_status';
export const APPROVAL_ACTIONS_BLOCK = 'approval_actions';
export const KEPT_CONTEXT_BLOCKS: ReadonlySet<string> = new Set(['approval_source', 'approval_meta', 'approval_details']);

const BODY_MAX_CHARS = 1500;
const LINE_MAX_CHARS = 1000;
// The server's closing words for a turn that stopped on a card (Python `tool_approvals.WAITING_PREFIX`).
const WAITING_LINE = /Waiting for your approval to run [^\n]*\.\s*$/;

/**
 * The answer without the server's closing "Waiting for your approval to run …":
 * the card above it says that already, with the status line and the buttons.
 * The space before it stays, so a streamed answer never gets shorter.
 */
export function withoutWaitingLine(text: string): string {
  return text.replace(WAITING_LINE, '');
}

const context = (blockId: string, ...texts: string[]): types.ContextBlock => ({
  type: 'context',
  block_id: blockId,
  elements: texts.map((text) => ({ type: 'mrkdwn' as const, text })),
});

/**
 * The call as the thing it makes, in the web card's regions: who it goes
 * to, the headline in bold and the body as text in one section; the other
 * settings as one line; ids and nested values on a Details line. Every set
 * argument is on the card, cut only to fit, and a cut says so.
 */
function documentBlocks(approval: ToolApprovalDetails): KnownBlock[] {
  if (approval.arguments === null) {
    const preview = approval.argumentsPreview ?? '';
    if (preview === '') return [];
    const shown = plain(preview, SECTION_MAX_CHARS - MORE_LINE_RESERVE);
    const text = shown.length < plain(preview).length ? `${shown}\n_${SEE_EVERYTHING}_` : shown;
    return [{ type: 'section', block_id: 'approval_document', text: { type: 'mrkdwn', text } }];
  }
  const layout = approvalLayout(approval.arguments, approval.inputSchema);
  let cut = false;
  const show = (value: unknown, max: number, name = ''): string => {
    const seconds = epochSeconds(name, value);
    if (seconds !== null) return `<!date^${String(seconds)}^{date_short_pretty} {time}|${new Date(seconds * 1000).toUTCString()}>`;
    const text = valueText(value);
    const shown = plain(text, max);
    if (shown.length < plain(text).length) cut = true;
    return shown;
  };
  const pair = (f: ApprovalField, max = ARGUMENT_VALUE_MAX_CHARS) =>
    `${plain(f.label, ARGUMENT_KEY_MAX_CHARS)}: ${show(f.value, max, f.name)}`;
  const lines = [
    ...layout.recipients.map((f) => pair(f, LINE_MAX_CHARS)),
    ...(layout.headline ? [`*${show(layout.headline.value, LINE_MAX_CHARS).replace(/\*/g, '')}*`] : []),
    // A label only tells several bodies apart.
    ...layout.body.map((f) =>
      layout.body.length > 1 ? `_${plain(f.label, ARGUMENT_KEY_MAX_CHARS)}_\n${show(f.value, BODY_MAX_CHARS / 2)}` : show(f.value, BODY_MAX_CHARS),
    ),
  ];
  const blocks: KnownBlock[] = [];
  if (lines.length > 0) {
    blocks.push({ type: 'section', block_id: 'approval_document', text: { type: 'mrkdwn', text: lines.join('\n') } });
  }
  if (layout.meta.length > 0) {
    blocks.push(context('approval_meta', truncate(layout.meta.map((f) => pair(f)).join(' · '), SECTION_MAX_CHARS)));
  }
  const details = layout.details.map((f) => pair(f)).join(' · ');
  if (details !== '' || cut) {
    blocks.push(
      context(
        'approval_details',
        ...(details !== '' ? ['*Details*', truncate(details, SECTION_MAX_CHARS)] : []),
        ...(cut ? [`_${SEE_EVERYTHING}_`] : []),
      ),
    );
  }
  return blocks;
}

export function buildApprovalCard(
  approval: ToolApprovalDetails,
  requesterSlackId: string,
  teamId: string,
): { text: string; blocks: KnownBlock[] } {
  // The tool's own title, as the web card shows it; displayName is past tense.
  const tool = toolLabel(approval.toolName, approval.toolTitle);
  const product = approvalProduct(approval.toolName, approval.serverName);
  const title = tool.toLowerCase().includes(product.toLowerCase()) ? tool : `${tool} in ${product}`;
  const expiresAtSeconds = Math.floor(approval.expiresAt / 1000);
  const status = [
    `Waiting on <@${requesterSlackId}> to answer`,
    `<!date^${String(expiresAtSeconds)}^Expires at {time}|Expires at ${new Date(approval.expiresAt).toUTCString()}>`,
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
  // Left-aligned, so the yes leads; then the maybe, then the no (no confirm: a deny can be asked again).
  const actions: types.ActionsBlock = {
    type: 'actions',
    block_id: APPROVAL_ACTIONS_BLOCK,
    elements: [
      button(
        APPROVAL_ALLOW_ACTION,
        'Approve',
        approval.kind === 'destructive' ? 'danger' : 'primary',
      ),
      button(APPROVAL_CHANGE_ACTION, 'Suggest a change'),
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
      ...(product !== approval.serverName ? [context('approval_source', plain(approval.serverName, ARGUMENT_KEY_MAX_CHARS))] : []),
      ...documentBlocks(approval),
      context(APPROVAL_STATUS_BLOCK, status.join(' · ')),
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
  kind: 'allowed' | 'allowedNotRun' | 'declined' | keyof typeof RECEIPTS,
  actorSlackId: string,
  note?: string,
): KnownBlock[] {
  let text: string;
  if (kind === 'allowed') text = `Approved by <@${actorSlackId}>`;
  else if (kind === 'allowedNotRun') {
    text = `Approved by <@${actorSlackId}>, but it didn't run`;
  } else if (kind !== 'declined') text = RECEIPTS[kind];
  else {
    text = `Declined by <@${actorSlackId}>`;
    // The feedback is capped at 2000, so the line stays under the 3000 limit.
    if (note !== undefined && note !== '') text += `: ${plain(note, 2500)}`;
  }
  return [context(APPROVAL_STATUS_BLOCK, text)];
}
