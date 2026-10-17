import crypto from "node:crypto";

import { ExpressReceiver } from "@slack/bolt";
import { type NextFunction, type Request, type Response, Router } from "express";
import {
  type SlackBotConfig,
  refreshSlackBotRegistry,
  runWithSlackRequestContext,
} from "./botRegistry";

const SLACK_EVENTS_ENDPOINT = process.env.SLACK_EVENTS_ENDPOINT || "/slack/events";
const MAX_SIGNATURE_AGE_SECONDS = 60 * 5;

/**
 * A signature that matches nothing may be a bot added since the cache was
 * filled. One forced refresh is worth it, but only at this interval, so
 * forged signatures cannot turn into unbounded registry traffic.
 */
const FORCED_REFRESH_COOLDOWN_MS = 30_000;
let lastForcedRefreshAt = 0;

/** Slack redelivers an event when the handler is slow. The same event_id must
 * not start a second answer. */
const EVENT_DEDUPE_TTL_MS = 5 * 60 * 1000;
const EVENT_DEDUPE_MAX_ENTRIES = 5000;
const seenEventIds = new Map<string, number>();

interface SlackReceiverRequest extends Request {
  rawBody?: Buffer;
  matchedSlackBot?: SlackBotConfig;
  slackPayload?: Record<string, unknown>;
}

function parseSlackPayload(rawBody: Buffer): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(rawBody.toString('utf8'));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function readStringField(
  payload: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = payload?.[key];
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function getHeaderValue(
  req: SlackReceiverRequest,
  key: string,
): string | undefined {
  const headerValue = req.headers[key];
  if (Array.isArray(headerValue)) {
    return headerValue[0];
  }
  return typeof headerValue === "string" ? headerValue : undefined;
}

async function readRawRequestBody(req: SlackReceiverRequest): Promise<Buffer> {
  if (req.rawBody && Buffer.isBuffer(req.rawBody)) {
    return req.rawBody;
  }

  const chunks: Buffer[] = [];
  await new Promise<void>((resolve, reject) => {
    req.on("data", (chunk: string | Buffer) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    req.on("end", () => resolve());
    req.on("error", (error) => reject(error));
  });

  const rawBody = Buffer.concat(chunks);
  req.rawBody = rawBody;
  return rawBody;
}

function constantTimeCompare(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left, "utf8");
  const rightBuffer = Buffer.from(right, "utf8");
  if (leftBuffer.length !== rightBuffer.length) {
    return false;
  }
  return crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function computeSlackSignature(
  signingSecret: string,
  requestTimestamp: string,
  rawBody: string,
): string {
  const signatureBase = `v0:${requestTimestamp}:${rawBody}`;
  const digest = crypto
    .createHmac("sha256", signingSecret)
    .update(signatureBase)
    .digest("hex");
  return `v0=${digest}`;
}

function findBotBySignature(
  bots: SlackBotConfig[],
  requestTimestamp: string,
  rawBody: string,
  signature: string,
): SlackBotConfig | null {
  return (
    bots.find((bot) =>
      constantTimeCompare(
        computeSlackSignature(bot.signingSecret, requestTimestamp, rawBody),
        signature,
      ),
    ) || null
  );
}

function isDuplicateSlackEvent(eventId: string): boolean {
  const now = Date.now();
  for (const [id, seenAt] of seenEventIds) {
    if (now - seenAt <= EVENT_DEDUPE_TTL_MS) {
      break;
    }
    seenEventIds.delete(id);
  }

  if (seenEventIds.has(eventId)) {
    return true;
  }

  seenEventIds.set(eventId, now);
  while (seenEventIds.size > EVENT_DEDUPE_MAX_ENTRIES) {
    const oldest = seenEventIds.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    seenEventIds.delete(oldest);
  }
  return false;
}

export function resetSlackEventGuardsForTests(): void {
  lastForcedRefreshAt = 0;
  seenEventIds.clear();
}

/**
 * Runs after verification so only authentic deliveries occupy a dedupe slot.
 * In-process, so a retry that lands on another replica is still processed.
 */
function dropDuplicateSlackEvents(
  req: SlackReceiverRequest,
  res: Response,
  next: NextFunction,
): void {
  const eventId = readStringField(req.slackPayload, 'event_id');
  if (!eventId) {
    next();
    return;
  }

  if (isDuplicateSlackEvent(eventId)) {
    res.status(200).send();
    return;
  }

  // A failed or dropped delivery is retried by Slack; that retry must be handled.
  res.on('close', () => {
    if (!res.writableFinished || res.statusCode >= 400) {
      seenEventIds.delete(eventId);
    }
  });
  next();
}

async function verifySlackRequest(
  req: SlackReceiverRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const signature = getHeaderValue(req, "x-slack-signature");
    const requestTimestamp = getHeaderValue(req, "x-slack-request-timestamp");
    if (!signature || !requestTimestamp) {
      res.status(401).send("Missing Slack signature headers");
      return;
    }

    const timestampSeconds = Number(requestTimestamp);
    if (Number.isNaN(timestampSeconds)) {
      res.status(401).send("Invalid Slack timestamp");
      return;
    }

    const nowSeconds = Math.floor(Date.now() / 1000);
    if (Math.abs(nowSeconds - timestampSeconds) > MAX_SIGNATURE_AGE_SECONDS) {
      res.status(401).send("Slack timestamp outside allowed window");
      return;
    }

    const rawBodyBuffer = await readRawRequestBody(req);
    const rawBody = rawBodyBuffer.toString("utf8");

    let matchedBot = findBotBySignature(
      await refreshSlackBotRegistry(),
      requestTimestamp,
      rawBody,
      signature,
    );

    if (
      !matchedBot &&
      Date.now() - lastForcedRefreshAt >= FORCED_REFRESH_COOLDOWN_MS
    ) {
      lastForcedRefreshAt = Date.now();
      matchedBot = findBotBySignature(
        await refreshSlackBotRegistry({ force: true }),
        requestTimestamp,
        rawBody,
        signature,
      );
    }

    if (!matchedBot) {
      res.status(401).send("This bot is not configured on pipeshub platform.");
      return;
    }

    req.slackPayload = parseSlackPayload(rawBodyBuffer) ?? undefined;
    req.matchedSlackBot = matchedBot;
    runWithSlackRequestContext(matchedBot, () => {
      next();
    });
  } catch (error) {
    // Message only: an axios failure here carries the gateway request config,
    // including the Authorization header used to read the bot registry.
    console.error(
      'Failed to verify Slack signature:',
      error instanceof Error ? error.message : String(error),
    );
    res.status(500).send("Slack verification error");
  }
}

export function registerSlackEventGuards(target: Router): void {
  target.use(
    SLACK_EVENTS_ENDPOINT,
    verifySlackRequest,
    dropDuplicateSlackEvents,
  );
}

const router = Router();
registerSlackEventGuards(router);

const receiver = new ExpressReceiver({
  signingSecret: process.env.SLACK_SIGNING_SECRET || "unused",
  signatureVerification: false,
  router,
  customPropertiesExtractor: (req) => {
    const matchedBot = (req as SlackReceiverRequest).matchedSlackBot;
    return {
      matchedBotAgentId: matchedBot?.agentId || null,
    };
  },
});

export default receiver;
