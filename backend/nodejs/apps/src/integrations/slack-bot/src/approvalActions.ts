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
  KEPT_CONTEXT_BLOCKS,
  APPROVAL_DENY_ACTION,
  buildApprovalReceipt,
  parseApprovalButtonValue,
  type ApprovalButtonValue,
} from './utils/approval-card';

type WebClient = AllMiddlewareArgs['client'];
type KnownBlock = types.KnownBlock;

export const APPROVAL_FEEDBACK_VIEW = 'approval_feedback';
const FEEDBACK_BLOCK = 'feedback';
const FEEDBACK_MAX_CHARS = 2000;
const SOMETHING_WENT_WRONG = 'Something went wrong, try again';

export interface ResumeApprovalRequest {
  channel: string;
  threadTs: string;
  clickerSlackId: string;
  approvalId: string;
  argsHash: string;
  decision: 'allow' | 'deny';
  feedback?: string;
}

/** How the backend settled the answered call, from the answer turn's stream. */
export interface ApprovalSettled {
  answer: string;
  /** The backend replayed an earlier answer (from the web app, say) and ran nothing. */
  replayed: boolean;
  /** How the call's row ended: `blocked` for an allowed call that didn't run (a Block set since). */
  ended?: string;
}

/**
 * `failed`: the answer never reached the backend, so the card can be answered again.
 * `allowedNotRun`: allowed, but the call was stopped before it ran.
 */
export type ApprovalTurnResult =
  'allowed' | 'allowedNotRun' | 'declined' | 'expired' | 'answered' | 'failed';

export interface ApprovalDeps {
  resumeApproval: (
    request: ResumeApprovalRequest,
    slack: { client: WebClient; context: Context },
  ) => Promise<ApprovalTurnResult>;
}

/**
 * The receipt for an answer turn. Once the backend settled the call, its row
 * decides, even if the turn failed later: answering again would only replay it.
 */
export function approvalTurnResult(
  decision: 'allow' | 'deny',
  settled: ApprovalSettled | undefined,
): ApprovalTurnResult {
  if (settled === undefined) return 'failed';
  if (settled.replayed) return 'answered';
  if (settled.answer === 'expired') return 'expired';
  const expected = decision === 'allow' ? 'allowed' : 'declined';
  if (settled.answer !== expected) return 'answered';
  return expected === 'allowed' && settled.ended === 'blocked'
    ? 'allowedNotRun'
    : expected;
}

interface CardLocation {
  channel: string;
  cardTs: string;
  threadTs: string;
}

interface CardMessage {
  text: string;
  blocks: KnownBlock[];
}

// Best-effort guard against a double click on this replica; the backend's claim is what counts.
const answering = new Map<string, number>();

function startAnswering(value: ApprovalButtonValue): boolean {
  const now = Date.now();
  for (const [id, expiresAt] of answering) {
    if (expiresAt < now) answering.delete(id);
  }
  if (answering.has(value.approvalId)) return false;
  answering.set(value.approvalId, value.expiresAt);
  return true;
}

export function resetApprovalGuardForTests(): void {
  answering.clear();
}

/**
 * The card's header and the call; its status line and buttons give way to a
 * receipt. Slack gives an unnamed block a random id, so a context line stays
 * only by one of the card's own names.
 */
function cardDetails(blocks: KnownBlock[]): KnownBlock[] {
  return blocks.filter((block) =>
    block.type === 'actions'
      ? false
      : block.type !== 'context' ||
        KEPT_CONTEXT_BLOCKS.has(block.block_id ?? ''),
  );
}

async function tell(
  client: WebClient,
  where: CardLocation,
  user: string,
  text: string,
): Promise<void> {
  try {
    await client.chat.postEphemeral({
      channel: where.channel,
      thread_ts: where.threadTs,
      user,
      text,
    });
  } catch (error) {
    console.error('Error posting Slack approval notice:', error);
  }
}

async function updateCard(
  client: WebClient,
  where: CardLocation,
  card: CardMessage,
  blocks: KnownBlock[],
): Promise<void> {
  // Without the card's own blocks (it couldn't be read), leave it as it is.
  if (card.blocks.length === 0) return;
  try {
    await client.chat.update({
      channel: where.channel,
      ts: where.cardTs,
      text: card.text,
      blocks,
    });
  } catch (error) {
    console.error('Error updating Slack approval card:', error);
  }
}

async function emailOf(client: WebClient, user: string): Promise<string> {
  const email = (await client.users.info({ user })).user?.profile?.email;
  return email?.toLowerCase() ?? '';
}

/**
 * Whether the clicker asked for this call (the same workspace and email as
 * the requester). Anyone else is told who can answer; nothing else changes.
 */
async function mayAnswer(
  client: WebClient,
  where: CardLocation,
  value: ApprovalButtonValue,
  clickerSlackId: string,
  clickerTeamId: string | undefined,
): Promise<boolean> {
  let allowed = false;
  try {
    if (clickerTeamId === value.teamId) {
      if (clickerSlackId === value.requesterSlackId) {
        allowed = true;
      } else {
        const [clicker, requester] = await Promise.all([
          emailOf(client, clickerSlackId),
          emailOf(client, value.requesterSlackId),
        ]);
        allowed = clicker !== '' && clicker === requester;
      }
    }
  } catch (error) {
    console.error('Error looking up a Slack approval clicker:', error);
    await tell(client, where, clickerSlackId, SOMETHING_WENT_WRONG);
    return false;
  }
  if (!allowed) {
    await tell(
      client,
      where,
      clickerSlackId,
      `Only <@${value.requesterSlackId}> can approve this`,
    );
  }
  return allowed;
}

async function settle(
  deps: ApprovalDeps,
  slack: { client: WebClient; context: Context },
  where: CardLocation,
  card: CardMessage,
  value: ApprovalButtonValue,
  answer: Pick<
    ResumeApprovalRequest,
    'clickerSlackId' | 'decision' | 'feedback'
  >,
): Promise<void> {
  if (!startAnswering(value)) return;
  const { client } = slack;
  const details = cardDetails(card.blocks);
  await updateCard(client, where, card, [
    ...details,
    ...buildApprovalReceipt('sending', answer.clickerSlackId),
  ]);
  let result: ApprovalTurnResult;
  try {
    result = await deps.resumeApproval(
      {
        channel: where.channel,
        threadTs: where.threadTs,
        approvalId: value.approvalId,
        argsHash: value.argsHash,
        ...answer,
      },
      slack,
    );
  } catch (error) {
    console.error('Error sending a Slack approval answer:', error);
    result = 'failed';
  }
  if (result === 'failed') {
    answering.delete(value.approvalId);
    await updateCard(client, where, card, card.blocks);
    await tell(client, where, answer.clickerSlackId, SOMETHING_WENT_WRONG);
    return;
  }
  await updateCard(client, where, card, [
    ...details,
    ...buildApprovalReceipt(result, answer.clickerSlackId, answer.feedback),
  ]);
}

/** The card message, for a modal submission, which doesn't carry it. */
async function readCard(
  client: WebClient,
  where: CardLocation,
): Promise<CardMessage> {
  try {
    const { messages } = await client.conversations.replies({
      channel: where.channel,
      ts: where.threadTs,
      oldest: where.cardTs,
      latest: where.cardTs,
      inclusive: true,
      limit: 2,
    });
    const message = messages?.find((m) => m.ts === where.cardTs);
    return {
      text: message?.text ?? '',
      blocks: (message?.blocks ?? []) as KnownBlock[],
    };
  } catch (error) {
    console.error('Error reading a Slack approval card:', error);
    return { text: '', blocks: [] };
  }
}

function feedbackModal(privateMetadata: string): types.ModalView {
  return {
    type: 'modal',
    callback_id: APPROVAL_FEEDBACK_VIEW,
    private_metadata: privateMetadata,
    title: { type: 'plain_text', text: 'Suggest a change' },
    submit: { type: 'plain_text', text: 'Send suggestion' },
    close: { type: 'plain_text', text: 'Cancel' },
    blocks: [
      {
        type: 'input',
        block_id: FEEDBACK_BLOCK,
        label: { type: 'plain_text', text: 'What should change?' },
        element: {
          type: 'plain_text_input',
          action_id: FEEDBACK_BLOCK,
          multiline: true,
          max_length: FEEDBACK_MAX_CHARS,
        },
      },
    ],
  };
}

function parseModalMetadata(
  raw: string,
): { value: ApprovalButtonValue; where: CardLocation } | null {
  try {
    const parsed = JSON.parse(raw) as {
      value?: unknown;
      where?: Partial<CardLocation>;
    };
    const value = parseApprovalButtonValue(JSON.stringify(parsed.value));
    const { channel, cardTs, threadTs } = parsed.where ?? {};
    if (
      value === null ||
      typeof channel !== 'string' ||
      typeof cardTs !== 'string' ||
      typeof threadTs !== 'string'
    ) {
      return null;
    }
    return { value, where: { channel, cardTs, threadTs } };
  } catch {
    return null;
  }
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
      const value = parseApprovalButtonValue(body.actions[0]?.value);
      const channel = body.channel?.id;
      const cardTs = body.message?.ts;
      if (value === null || channel === undefined || cardTs === undefined)
        return;
      const threadTs =
        (body.message?.thread_ts as string | undefined) ?? cardTs;
      const where = { channel, cardTs, threadTs };
      const clickerSlackId = body.user.id;
      if (
        !(await mayAnswer(client, where, value, clickerSlackId, body.team?.id))
      )
        return;

      const card: CardMessage = {
        text: body.message?.text ?? '',
        blocks: (body.message?.blocks ?? []) as KnownBlock[],
      };
      if (value.expiresAt <= Date.now()) {
        await updateCard(client, where, card, [
          ...cardDetails(card.blocks),
          ...buildApprovalReceipt('expired', clickerSlackId),
        ]);
        return;
      }
      if (answering.has(value.approvalId)) return;
      if (decision !== 'change') {
        await settle(deps, { client, context }, where, card, value, {
          clickerSlackId,
          decision,
        });
        return;
      }
      try {
        await client.views.open({
          trigger_id: body.trigger_id,
          view: feedbackModal(JSON.stringify({ value, where })),
        });
      } catch (error) {
        console.error(
          'Error opening the Slack approval feedback modal:',
          error,
        );
        await tell(client, where, clickerSlackId, SOMETHING_WENT_WRONG);
      }
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
      const metadata = parseModalMetadata(view.private_metadata);
      if (metadata === null) return;
      const { value, where } = metadata;
      const clickerSlackId = body.user.id;
      if (
        !(await mayAnswer(client, where, value, clickerSlackId, body.team?.id))
      )
        return;
      const card = await readCard(client, where);
      if (value.expiresAt <= Date.now()) {
        await updateCard(client, where, card, [
          ...cardDetails(card.blocks),
          ...buildApprovalReceipt('expired', clickerSlackId),
        ]);
        return;
      }
      const feedback = (
        view.state.values[FEEDBACK_BLOCK]?.[FEEDBACK_BLOCK]?.value ?? ''
      )
        .trim()
        .slice(0, FEEDBACK_MAX_CHARS);
      await settle(deps, { client, context }, where, card, value, {
        clickerSlackId,
        decision: 'deny',
        ...(feedback !== '' ? { feedback } : {}),
      });
    },
  );
}
