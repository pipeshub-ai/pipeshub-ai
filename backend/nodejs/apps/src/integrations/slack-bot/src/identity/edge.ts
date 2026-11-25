/**
 * The receiver's identity guard (ADR-SLACKID-01): an authentic delivery from another workspace, from a channel shared
 * with another organisation, or from a bot never reaches a handler. Exported for any composition of the receiver.
 */
import type { NextFunction, Request, Response } from 'express';
import type { SlackBotConfig } from '../botRegistry';
import { type SlackRefusal, slackEventRefusal } from '../caller-identity';

interface SlackReceiverRequest extends Request {
  matchedSlackBot?: SlackBotConfig;
  slackPayload?: Record<string, unknown>;
}

export interface RefusedSlackEvent {
  bot: SlackBotConfig;
  refusal: SlackRefusal;
  event: Record<string, unknown>;
}

/** Tells a refused person why, privately. Set by the bot process; nothing here looks the person up. */
let refusalNotifier: ((refused: RefusedSlackEvent) => Promise<void>) | null =
  null;

export function setSlackRefusalNotifier(
  notifier: ((refused: RefusedSlackEvent) => Promise<void>) | null,
): void {
  refusalNotifier = notifier;
}

/**
 * An authentic delivery from outside the bot's own workspace (Slack Connect), from a channel shared with another
 * organisation, or from a bot is answered 200 (so Slack does not retry it) and never reaches a handler, which is
 * where a Slack person would be mapped to a PipesHub account (ADR-SLACKID-01).
 */
export function refuseForeignSlackEvents(
  req: SlackReceiverRequest,
  res: Response,
  next: NextFunction,
): void {
  const refusal = slackEventRefusal(req.slackPayload);
  if (!refusal) {
    next();
    return;
  }
  res.status(200).send();
  const event = req.slackPayload?.event;
  if (
    refusal !== 'bot' &&
    refusalNotifier &&
    req.matchedSlackBot &&
    event &&
    typeof event === 'object'
  ) {
    refusalNotifier({
      bot: req.matchedSlackBot,
      refusal,
      event: event as Record<string, unknown>,
    }).catch((error: unknown) => {
      console.error(
        'Failed to tell a refused Slack sender why:',
        error instanceof Error ? error.message : String(error),
      );
    });
  }
}

export function resetSlackRefusalNotifierForTests(): void {
  refusalNotifier = null;
}
