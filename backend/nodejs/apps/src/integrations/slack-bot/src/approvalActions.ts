import type {
  AllMiddlewareArgs,
  App,
  BlockAction,
  ButtonAction,
  Context,
  types,
  ViewSubmitAction,
} from '@slack/bolt';
import {
  APPROVAL_ALLOW_ACTION,
  APPROVAL_CHANGE_ACTION,
  APPROVAL_DENY_ACTION,
  buildApprovalReceipt,
  type ToolApprovalDetails,
} from './utils/approval-card';

type WebClient = AllMiddlewareArgs['client'];
type KnownBlock = types.KnownBlock;

export const APPROVAL_FEEDBACK_VIEW = 'approval_feedback';
const FEEDBACK_BLOCK = 'feedback';
const FEEDBACK_MAX_CHARS = 2000;

export interface ResumeApprovalRequest {
  channel: string;
  threadTs: string;
  clickerSlackId: string;
  approvalId: string;
  argsHash: string;
  decision: 'allow' | 'deny';
  feedback?: string;
}

export interface ApprovalDeps {
  resumeApproval: (
    request: ResumeApprovalRequest,
    slack: { client: WebClient; context: Context },
  ) => Promise<void>;
}

interface ApprovalCardEntry {
  requesterSlackId: string;
  requesterEmail: string;
  channel: string;
  threadTs: string;
  cardTs: string;
  expiresAt: number;
  text: string;
  blocks: KnownBlock[];
  answered: boolean;
}

// shortcut: in-process, so a click after a restart or on another replica reads as expired; upgrade by fetching the approval from the backend.
const cards = new Map<string, ApprovalCardEntry>();

/** The card's header and details; its waiting line and buttons give way to the receipt. */
function withoutButtons(blocks: KnownBlock[]): KnownBlock[] {
  return blocks.filter(
    (block) => block.type !== 'actions' && block.type !== 'context',
  );
}

export function rememberApprovalCard(
  approval: ToolApprovalDetails,
  card: { text: string; blocks: KnownBlock[] },
  posted: Pick<
    ApprovalCardEntry,
    'requesterSlackId' | 'requesterEmail' | 'channel' | 'threadTs' | 'cardTs'
  >,
): void {
  const now = Date.now();
  for (const [id, entry] of cards) {
    if (entry.expiresAt < now) cards.delete(id);
  }
  cards.set(approval.approvalId, {
    ...posted,
    expiresAt: approval.expiresAt,
    text: card.text,
    blocks: withoutButtons(card.blocks),
    answered: false,
  });
}

export function resetApprovalCardsForTests(): void {
  cards.clear();
}

function parseButtonValue(
  value: string | undefined,
): { approvalId: string; argsHash: string } | null {
  try {
    const parsed: unknown = JSON.parse(value ?? '');
    const { approvalId, argsHash } = (parsed ?? {}) as Record<string, unknown>;
    return typeof approvalId === 'string' && typeof argsHash === 'string'
      ? { approvalId, argsHash }
      : null;
  } catch {
    return null;
  }
}

async function isRequester(
  client: WebClient,
  entry: ApprovalCardEntry,
  clickerSlackId: string,
): Promise<boolean> {
  if (clickerSlackId === entry.requesterSlackId) return true;
  const email = (await client.users.info({ user: clickerSlackId })).user
    ?.profile?.email;
  return (
    email !== undefined &&
    email !== '' &&
    email.toLowerCase() === entry.requesterEmail.toLowerCase()
  );
}

async function showReceipt(
  client: WebClient,
  channel: string,
  ts: string,
  text: string,
  blocks: KnownBlock[],
  receipt: KnownBlock[],
): Promise<void> {
  try {
    await client.chat.update({
      channel,
      ts,
      text,
      blocks: [...blocks, ...receipt],
    });
  } catch (error) {
    console.error('Error updating Slack approval card:', error);
  }
}

/**
 * The live card for a click, or null when the click must do nothing more:
 * already answered, expired (the card then says so) or not the requester's
 * (who then gets an ephemeral notice). `claim` marks the card answered.
 */
async function cardForClick(
  client: WebClient,
  approvalId: string,
  clickerSlackId: string,
  fallback: { channel?: string; ts?: string; blocks?: KnownBlock[] },
  claim: boolean,
): Promise<ApprovalCardEntry | null> {
  const entry = cards.get(approvalId);
  if (entry?.answered) return null;
  if (!entry || entry.expiresAt <= Date.now()) {
    cards.delete(approvalId);
    const channel = entry?.channel ?? fallback.channel;
    const ts = entry?.cardTs ?? fallback.ts;
    if (channel !== undefined && ts !== undefined) {
      await showReceipt(
        client,
        channel,
        ts,
        'Expired',
        entry?.blocks ?? withoutButtons(fallback.blocks ?? []),
        buildApprovalReceipt('expired', clickerSlackId),
      );
    }
    return null;
  }
  if (!(await isRequester(client, entry, clickerSlackId))) {
    await client.chat.postEphemeral({
      channel: entry.channel,
      thread_ts: entry.threadTs,
      user: clickerSlackId,
      text: `Only <@${entry.requesterSlackId}> can approve this`,
    });
    return null;
  }
  // Checked and claimed with no await between, so a second click cannot also answer.
  if (entry.answered) return null;
  if (claim) entry.answered = true;
  return entry;
}

async function settle(
  deps: ApprovalDeps,
  slack: { client: WebClient; context: Context },
  entry: ApprovalCardEntry,
  request: Omit<ResumeApprovalRequest, 'channel' | 'threadTs'>,
): Promise<void> {
  const receipt =
    request.decision === 'allow'
      ? buildApprovalReceipt('allowed', request.clickerSlackId)
      : buildApprovalReceipt(
          'declined',
          request.clickerSlackId,
          request.feedback,
        );
  await showReceipt(
    slack.client,
    entry.channel,
    entry.cardTs,
    entry.text,
    entry.blocks,
    receipt,
  );
  await deps.resumeApproval(
    { channel: entry.channel, threadTs: entry.threadTs, ...request },
    slack,
  );
}

function feedbackModal(privateMetadata: string): types.ModalView {
  return {
    type: 'modal' as const,
    callback_id: APPROVAL_FEEDBACK_VIEW,
    private_metadata: privateMetadata,
    title: { type: 'plain_text' as const, text: 'Change something else' },
    submit: { type: 'plain_text' as const, text: 'Send' },
    close: { type: 'plain_text' as const, text: 'Cancel' },
    blocks: [
      {
        type: 'input' as const,
        block_id: FEEDBACK_BLOCK,
        label: { type: 'plain_text' as const, text: 'What should change?' },
        element: {
          type: 'plain_text_input' as const,
          action_id: FEEDBACK_BLOCK,
          multiline: true,
          max_length: FEEDBACK_MAX_CHARS,
        },
      },
    ],
  };
}

export function registerApprovalActions(app: App, deps: ApprovalDeps): void {
  const onButton =
    (decision: 'allow' | 'deny' | 'change') =>
    async ({
      ack,
      body,
      client,
      context,
    }: {
      ack: () => Promise<void>;
      body: BlockAction<ButtonAction>;
      client: WebClient;
      context: Context;
    }): Promise<void> => {
      await ack();
      const target = parseButtonValue(body.actions[0]?.value);
      if (!target) return;
      const clickerSlackId = body.user.id;
      const entry = await cardForClick(
        client,
        target.approvalId,
        clickerSlackId,
        {
          channel: body.channel?.id,
          ts: body.message?.ts,
          blocks: body.message?.blocks as KnownBlock[] | undefined,
        },
        decision !== 'change',
      );
      if (!entry) return;
      if (decision === 'change') {
        await client.views.open({
          trigger_id: body.trigger_id,
          view: feedbackModal(JSON.stringify(target)),
        });
        return;
      }
      await settle(deps, { client, context }, entry, {
        clickerSlackId,
        decision,
        ...target,
      });
    };

  app.action<BlockAction<ButtonAction>>(
    APPROVAL_ALLOW_ACTION,
    onButton('allow'),
  );
  app.action<BlockAction<ButtonAction>>(APPROVAL_DENY_ACTION, onButton('deny'));
  app.action<BlockAction<ButtonAction>>(
    APPROVAL_CHANGE_ACTION,
    onButton('change'),
  );

  app.view<ViewSubmitAction>(
    APPROVAL_FEEDBACK_VIEW,
    async ({ ack, body, view, client, context }) => {
      await ack();
      const target = parseButtonValue(view.private_metadata);
      if (!target) return;
      const clickerSlackId = body.user.id;
      const entry = await cardForClick(
        client,
        target.approvalId,
        clickerSlackId,
        {},
        true,
      );
      if (!entry) return;
      const feedback = (
        view.state.values[FEEDBACK_BLOCK]?.[FEEDBACK_BLOCK]?.value ?? ''
      )
        .trim()
        .slice(0, FEEDBACK_MAX_CHARS);
      await settle(deps, { client, context }, entry, {
        clickerSlackId,
        decision: 'deny',
        ...target,
        ...(feedback !== '' ? { feedback } : {}),
      });
    },
  );
}
