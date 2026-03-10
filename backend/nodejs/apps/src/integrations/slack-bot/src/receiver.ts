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

interface SlackReceiverRequest extends Request {
  rawBody?: Buffer;
  matchedSlackBot?: SlackBotConfig;
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

async function verifySlackRequest(
  req: SlackReceiverRequest,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    console.log("verifySlackRequest");
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
    const bots = await refreshSlackBotRegistry({ force: true });
    const matchedBot =
      bots.find((bot) =>
        constantTimeCompare(
          computeSlackSignature(bot.signingSecret, requestTimestamp, rawBody),
          signature,
        ),
      ) || null;

    if (!matchedBot) {
      res.status(401).send("This bot is not configured on pipeshub platform.");
      return;
    }

    req.matchedSlackBot = matchedBot;
    runWithSlackRequestContext(matchedBot, () => {
      next();
    });
  } catch (error) {
    console.error("Failed to verify Slack signature", error);
    res.status(500).send("Slack verification error");
  }
}

const router = Router();
router.use(SLACK_EVENTS_ENDPOINT, verifySlackRequest);

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
