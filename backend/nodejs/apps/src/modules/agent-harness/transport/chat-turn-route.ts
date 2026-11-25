import { localeOf } from './request-locale';
import { framesOf } from '../projection/frame-version';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import { NextFunction, Request, RequestHandler, Response } from 'express';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { orgHarnessOn } from '../config';
import { ChatTurnService, turnIdempotencyKey } from '../application/chat-turns';
import {
  type RunWatchRegistry,
  slackThreadViewer,
} from '../application/run-end-notices';
import type { ChatModelGroups } from '../ports/group-access';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import { RunSurface } from '../domain/admission';
import {
  gatesNeedRuntime,
  HarnessGroupAccessRefusedError,
  HarnessRunConfigUnavailableError,
  HarnessUnavailableError,
} from '../domain/errors';
import { RunInputSnapshot, RunReplyTarget } from '../domain/execution';
import {
  sessionRepositorySchema,
  type SessionRepository,
} from '../domain/repository';
import { GATES_FIELD, requestedGates } from '../domain/run-gates';
import type { ResolvedRoute } from '../ports/routing-decider';
import { transportError } from './errors';
import { prepareResolvedTurn, signsResolvedTurn } from './resolved-turn-header';
import {
  identityString,
  knowledgeLimited,
  requestPrincipalKind,
  requestVia,
} from './request-identity';
import { boundedId, runCommandSchema, runInputSchema } from './schemas';
import {
  COMMAND_LOCAL,
  REGENERATE_QUERY_LOCAL,
  type RunCommand,
} from '../domain/commands';
import { responseSink, writeSseHead } from './sse';
import { formatSseFrame } from '../projection/frames';
import { SURFACE_HEADER } from './surface-header';

/** Legacy's own run deadline for a chat turn (the admission default). */
const CHAT_TURN_DEADLINE_MS = RELEASE_DEFAULTS.runs.deadlineMs;

const MAX_CALLER_NAME = 256;

/** The legacy body fields a durable run input carries. */
const RUN_INPUT_FIELDS = [
  'query',
  'recordIds',
  'filters',
  'appliedFilters',
  'attachments',
  'tools',
  'agentCapabilities',
  'modelKey',
  'modelName',
  'modelProvider',
  'modelFriendlyName',
  'modelAccountId',
  'reasoningEffort',
  'chatMode',
  'timezone',
  'currentTime',
  'includeRetrievalContext',
  'keywordSearchBudget',
] as const;

const ATTACHMENT_FIELDS = [
  'recordId',
  'recordName',
  'mimeType',
  'extension',
  'virtualRecordId',
  'source',
] as const;

/** The Slack thread a Slack turn came from (`{ channel, threadTs }`): the run's `input.reply` (D-SLACK-ASK). */
export const SLACK_THREAD_FIELD = 'slackThread';

/**
 * Legacy fields a durable turn serves the same way without them: the wire
 * protocol is always AG-UI, the caller context becomes `input.caller`, and
 * the client's run id names the turn (its idempotency key). Anything else
 * (client history, forged identities) keeps the turn on legacy.
 */
const SERVED_FIELDS = new Set([
  'protocol',
  'callerDisplayName',
  'callerEmail',
  'runId',
  SLACK_THREAD_FIELD,
]);

/** The body field naming the coding engine for the chat a turn creates (CMDUI2). */
export const CODING_ENGINE_FIELD = 'codingEngine';

/** The body field naming the GitHub repository for the chat a turn creates (GIT-FLOW, the composer chip). */
export const REPOSITORY_FIELD = 'repository';

/** Fields routing and admission read; the run never carries them. */
const ROUTING_FIELDS = new Set([
  'projectId',
  'projectVisibility',
  'harness',
  CODING_ENGINE_FIELD,
  REPOSITORY_FIELD,
  GATES_FIELD,
]);

/**
 * Fields the chat routes accept that only today's loop serves, each with why: a turn that sends one stays there.
 * Every field a chat stream route accepts is a run input, served, routing, or listed here (R33JGAPS-FU-1), so a
 * field a client starts sending never moves its turns to today's loop unnoticed.
 */
export const LEGACY_ONLY_FIELDS: Readonly<Record<string, string>> = {
  compareTurn:
    "Compare models answers one question with two models side by side, which only today's loop does",
  toolApproval:
    "an answer to today's loop's parked approval card; a durable run's approvals resume through its interaction",
};

/** How the durable route takes a body field; undefined: it does not know the field, so the turn stays on legacy. */
export function chatTurnFieldRole(
  key: string,
): 'input' | 'served' | 'routing' | 'legacy' | undefined {
  if ((RUN_INPUT_FIELDS as readonly string[]).includes(key)) return 'input';
  if (SERVED_FIELDS.has(key)) return 'served';
  if (ROUTING_FIELDS.has(key)) return 'routing';
  return key in LEGACY_ONLY_FIELDS ? 'legacy' : undefined;
}

/** A gated turn that only today's loop could serve (the harness is off for it, or routing chose the classic path). */
export const GATES_NEED_HARNESS =
  'gates_need_harness: dry runs, plan approval and verification run only on the agent harness, which is not serving this chat';

/**
 * The body field a turn asks for the durable harness with (ASSIST1). Only the
 * assistant's Agent mode reads it; the org setting still decides.
 */
export const HARNESS_FIELD = 'harness';

/** Whether the body asks for the durable harness. */
export const durableRequested = (body: unknown): boolean =>
  isObject(body) && body[HARNESS_FIELD] === 'durable';

/** Whether the body asks for the classic path (CTL-7's chat switch, on any route); routing still decides. */
export const legacyRequested = (body: unknown): boolean =>
  isObject(body) && body[HARNESS_FIELD] === 'legacy';

/** Lets a client resend one turn without a second run (RFC draft `Idempotency-Key`). */
export const IDEMPOTENCY_HEADER = 'idempotency-key';

/** A new message, or a regenerate of the conversation's last answer (`…/message/:messageId/regenerate`). */
export type ChatTurnKind = 'send' | 'regenerate';

/**
 * A regenerate names no question: admission asks the one the conversation
 * stores, read and retracted in its own transaction (UX3 RG-3), so the
 * client's copy of it never reaches the run.
 */
export const STORED_QUESTION = '';

const regenerateInputSchema = runInputSchema.extend({
  query: z.literal(STORED_QUESTION),
});

export interface ChatTurnRouteOptions {
  kind: 'chat' | 'agent';
  /** Absent: `send`. */
  turn?: ChatTurnKind;
  /** Where this request comes from, or null (the legacy handler serves it untouched). */
  surface: (req: Request) => RunSurface | null;
  /** Resolves `req.user` from a service credential (the Slack bot's hydration). */
  identify?: (req: Request) => Promise<void>;
  /** False while the harness is off: every request goes to the legacy handler untouched. */
  enabled: () => boolean;
  /**
   * G-41: the org's model groups, on every turn today's loop serves, the harness on or off; admission's
   * ModelGroupStep holds harness turns to them. Absent: group access is not composed.
   */
  modelGroups?: Pick<ChatModelGroups, 'admits'>;
  /** G2 (ADR-INBOX-01): while the turn's stream is open its sender watches the conversation. */
  watchers?: Pick<RunWatchRegistry, 'watch'>;
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const present = (body: Json): Json =>
  Object.fromEntries(
    Object.entries(body).filter(
      ([, value]) => value !== null && value !== undefined,
    ),
  );

/** The MCP module marks its requests; Slack is known by its route. */
export const mcpSurface = (req: Request): RunSurface | null =>
  req.header(SURFACE_HEADER)?.trim().toLowerCase() === 'mcp' ? 'mcp' : null;

export const slackSurface = (): RunSurface => 'slack';

/** The public chat routes: the web app and API/PAT clients, unless the MCP module marked the request. */
export const webSurface = (req: Request): RunSurface =>
  mcpSurface(req) ?? 'web';

/**
 * The coding engine a body names for the conversation it creates (CMDUI2); the route's validator checked its
 * format. An existing conversation's engine changes only through `PUT …/coding-engine`.
 */
export function chatTurnCodingEngine(body: unknown): string | undefined {
  const engine = isObject(body) ? body[CODING_ENGINE_FIELD] : undefined;
  return typeof engine === 'string' && engine.length > 0 ? engine : undefined;
}

/**
 * The repository a body names for the conversation it creates (GIT-FLOW); the route's validator checked its
 * format. An existing conversation's repository changes only through `PUT …/repository`.
 */
export function chatTurnRepository(
  body: unknown,
): SessionRepository | undefined {
  const parsed = sessionRepositorySchema.safeParse(
    isObject(body) ? body[REPOSITORY_FIELD] : undefined,
  );
  return parsed.success ? parsed.data : undefined;
}

/** The project a body names for the conversation it creates; routing reads an existing one's from its record. */
export function chatTurnProject(body: unknown): string | undefined {
  const projectId = isObject(body) ? body['projectId'] : undefined;
  return typeof projectId === 'string' && projectId.length > 0
    ? projectId
    : undefined;
}

/**
 * A legacy stream body as a durable run input, or null when the durable
 * route cannot serve it as sent. Mirrors legacy's defaults: a plain chat
 * with no mode is `quick` (which the rollout keeps on legacy), an agent's
 * `auto`.
 */
/** A regenerate that carries the question's new text or files: an edit, not a plain regenerate. */
const editsTheQuestion = (body: Json): boolean =>
  (typeof body['query'] === 'string' && body['query'].trim() !== '') ||
  Array.isArray(body['attachments']);

export function chatTurnInput(
  body: unknown,
  kind: 'chat' | 'agent',
  turn: ChatTurnKind = 'send',
  command?: RunCommand,
): RunInputSnapshot | null {
  if (!isObject(body)) return null;
  const sent = present(body);
  if (turn === 'regenerate') {
    // An edited question (main #4424) is saved and answered by today's loop; this one re-asks the stored question.
    if (editsTheQuestion(body)) return null;
    sent['query'] = STORED_QUESTION;
  }
  const history = sent['previousConversations'];
  if (Array.isArray(history) && history.length === 0) {
    delete sent['previousConversations'];
  }
  const durable = (key: string): boolean => {
    const role = chatTurnFieldRole(key);
    return role !== undefined && role !== 'legacy';
  };
  if (!Object.keys(sent).every(durable)) return null;
  const picked: Json = Object.fromEntries(
    RUN_INPUT_FIELDS.filter((key) => key in sent).map((key) => [
      key,
      sent[key],
    ]),
  );
  if (Array.isArray(picked['attachments'])) {
    picked['attachments'] = (picked['attachments'] as unknown[]).map((item) =>
      isObject(item)
        ? Object.fromEntries(
            ATTACHMENT_FIELDS.filter(
              (key) => item[key] !== undefined && item[key] !== null,
            ).map((key) => [key, item[key]]),
          )
        : item,
    );
  }
  picked['chatMode'] ??= kind === 'chat' ? 'quick' : 'auto';
  const parsed = (
    turn === 'regenerate' ? regenerateInputSchema : runInputSchema
  ).safeParse(picked);
  if (!parsed.success) return null;
  const input = parsed.data;
  return {
    query: input.query,
    recordIds: input.recordIds,
    filters: input.filters,
    ...(input.appliedFilters !== undefined
      ? { appliedFilters: input.appliedFilters }
      : {}),
    attachments: input.attachments,
    ...(input.tools !== undefined ? { tools: input.tools } : {}),
    ...(input.agentCapabilities !== undefined
      ? { agentCapabilities: input.agentCapabilities }
      : {}),
    model: {
      ...(input.modelKey !== undefined ? { modelKey: input.modelKey } : {}),
      ...(input.modelName !== undefined ? { modelName: input.modelName } : {}),
      ...(input.modelProvider !== undefined
        ? { modelProvider: input.modelProvider }
        : {}),
      ...(input.modelFriendlyName !== undefined
        ? { modelFriendlyName: input.modelFriendlyName }
        : {}),
      ...(input.reasoningEffort !== undefined
        ? { reasoningEffort: input.reasoningEffort }
        : {}),
      ...(input.modelAccountId !== undefined
        ? { accountId: input.modelAccountId }
        : {}),
      chatMode: input.chatMode,
    },
    ...(input.timezone !== undefined ? { timezone: input.timezone } : {}),
    ...(input.currentTime !== undefined
      ? { currentTime: input.currentTime }
      : {}),
    ...(command !== undefined && turn === 'send' ? { command } : {}),
    ...(input.includeRetrievalContext === true
      ? { includeRetrievalContext: true }
      : {}),
    ...(input.keywordSearchBudget !== undefined
      ? { keywordSearchBudget: input.keywordSearchBudget }
      : {}),
  };
}

/** The command the route's expansion resolved for this turn (SLASH1), never the body's. */
function expandedCommand(res: Response): RunCommand | undefined {
  const locals = res.locals as Record<string, unknown> | undefined;
  const parsed = runCommandSchema.safeParse(locals?.[COMMAND_LOCAL]);
  return parsed.success ? parsed.data : undefined;
}

/** A stored `/review`'s fresh expansion the regenerate route made (LC2FIX), never the body's. */
function regeneratedCommandQuery(res: Response): { query?: string } {
  const query = (res.locals as Record<string, unknown> | undefined)?.[
    REGENERATE_QUERY_LOCAL
  ];
  return typeof query === 'string' ? { query } : {};
}

/**
 * Who is behind the Slack bot's service credential, for the model's context
 * (legacy's `callerEmail`/`callerDisplayName`). The email is the one the
 * scoped token was minted for; only the display name comes from the bot.
 */
function slackCaller(
  req: Request,
  body: unknown,
): RunInputSnapshot['caller'] | undefined {
  const token = (req as Request & { tokenPayload?: Json }).tokenPayload;
  const email =
    typeof token?.['email'] === 'string' && token['email'].trim() !== ''
      ? token['email'].trim()
      : undefined;
  const rawName = isObject(body) ? body['callerDisplayName'] : undefined;
  const displayName =
    typeof rawName === 'string' && rawName.trim() !== ''
      ? rawName.trim().slice(0, MAX_CALLER_NAME)
      : undefined;
  if (email === undefined && displayName === undefined) return undefined;
  return {
    ...(email !== undefined ? { email } : {}),
    ...(displayName !== undefined ? { displayName } : {}),
  };
}

const SLACK_CHANNEL = /^[A-Z0-9]{1,32}$/;
const SLACK_TS = /^\d{1,12}\.\d{1,9}$/;

/**
 * ADR-OWD-09 D-SLACK-ASK: the thread a Slack turn came from, where Node posts the run's end when the bot's stream
 * ended first (it handed off at a card, or the bot went away). The bot is the one the scoped token was minted for,
 * never the body's; the body names only its thread.
 */
export function slackReply(
  req: Request,
  body: unknown,
): RunReplyTarget | undefined {
  const token = (req as Request & { tokenPayload?: Json }).tokenPayload;
  const botId =
    typeof token?.['configId'] === 'string' ? token['configId'] : '';
  const thread = isObject(body) ? body[SLACK_THREAD_FIELD] : undefined;
  if (botId === '' || !isObject(thread)) return undefined;
  const { channel, threadTs } = thread;
  if (typeof channel !== 'string' || !SLACK_CHANNEL.test(channel)) {
    return undefined;
  }
  if (typeof threadTs !== 'string' || !SLACK_TS.test(threadTs)) {
    return undefined;
  }
  return { slack: { botId, channel, threadTs } };
}

interface RequestIdentity {
  orgId: string;
  actorId: string;
}

/** The org and actor the auth middleware verified, or null before a service credential is resolved. */
function requestIdentity(req: Request): RequestIdentity | null {
  const user = (req as Request & { user?: Json }).user;
  const orgId = identityString(user?.['orgId']);
  const actorId = identityString(user?.['userId']);
  return orgId === null || actorId === null ? null : { orgId, actorId };
}

/** The model a chat request names, after the default model was filled in; undefined: none. */
const namedModelKey = (body: unknown): string | undefined => {
  if (!isObject(body)) return undefined;
  const key = body['modelKey'];
  return typeof key === 'string' && key.trim() !== '' ? key.trim() : undefined;
};

const sentChatMode = (body: unknown): string | undefined => {
  const chatMode = isObject(body) ? body['chatMode'] : undefined;
  return typeof chatMode === 'string' ? chatMode : undefined;
};

/** The turn's idempotency key: the client's, its run id's, or one minted for this request. */
function turnKey(
  req: Request,
  body: unknown,
): { idempotencyKey: string; firstSubmission: boolean } {
  const header = req.header(IDEMPOTENCY_HEADER);
  if (header !== undefined) {
    const parsed = boundedId.safeParse(header.trim());
    if (!parsed.success) {
      throw new BadRequestError(
        `${IDEMPOTENCY_HEADER} must be 1-128 of A-Z a-z 0-9 . _ : -`,
      );
    }
    return { idempotencyKey: parsed.data, firstSubmission: false };
  }
  const runId = isObject(body) ? body['runId'] : undefined;
  const fromRun =
    typeof runId === 'string'
      ? boundedId.safeParse(turnIdempotencyKey(runId))
      : null;
  return fromRun?.success === true
    ? { idempotencyKey: fromRun.data, firstSubmission: false }
    : { idempotencyKey: `surface:${randomUUID()}`, firstSubmission: true };
}

/**
 * One chat turn on an existing chat route (LAT1, FR-40; P1b C-5 for Slack
 * and MCP), in front of the legacy stream handler: admitted, routed by the
 * server and streamed on the same response, so no client pays a second
 * request. Admission trusts only the authenticated request: org, actor and
 * `via` from the verified credential, never the body. Whenever the harness
 * does not take the turn (off, a body it cannot serve, a legacy routing
 * decision, or it could not decide and wrote nothing), the legacy handler
 * runs as if this were not here.
 */
export function chatTurnMiddleware(
  service: ChatTurnService,
  options: ChatTurnRouteOptions,
): RequestHandler {
  const handle = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const agentKey =
      options.kind === 'agent' ? (req.params['agentKey'] as string) : null;
    // Taken off the body here: it is admission's alone, and today's loop, which has no coding engine, never sees it.
    const sentEngine = chatTurnCodingEngine(req.body);
    const sentRepository = chatTurnRepository(req.body);
    // G-03..G-05: admission's alone, and only the durable harness enforces them.
    const sentGates = requestedGates(req.body);
    if (isObject(req.body)) {
      Reflect.deleteProperty(req.body, CODING_ENGINE_FIELD);
      Reflect.deleteProperty(req.body, REPOSITORY_FIELD);
      Reflect.deleteProperty(req.body, GATES_FIELD);
    }
    if (sentGates.kind === 'invalid') {
      next(new BadRequestError(sentGates.message));
      return;
    }
    // Today's loop serves the turn: pinned first under CONTROL_PLANE_PIN=on, so one that cannot be is answered here.
    const legacy = async (route?: ResolvedRoute): Promise<void> => {
      if (sentGates.kind === 'gates') {
        // Today's loop has no gates: a dry run there would act for real, so the turn is refused, never run ungated.
        next(new BadRequestError(GATES_NEED_HARNESS));
        return;
      }
      // Without group access nothing is awaited: today's loop is reached in the same tick (QA6-F11).
      if (options.modelGroups !== undefined && !(await admittedModel())) return;
      if (signsResolvedTurn(req)) {
        try {
          await prepareResolvedTurn(req, agentKey, route);
        } catch (error) {
          next(transportError(error));
          return;
        }
      }
      next();
    };
    /** G-41: the turn's model is one the sender's groups may use; false once the refusal is answered. */
    const admittedModel = async (): Promise<boolean> => {
      if (options.modelGroups === undefined) return true;
      if (requestIdentity(req) === null && options.identify !== undefined) {
        try {
          await options.identify(req);
        } catch {
          // The legacy handler resolves the identity again and answers the failure.
          return true;
        }
      }
      const sender = requestIdentity(req);
      // No verified sender: the legacy handler refuses the request itself.
      if (sender === null) return true;
      let admitted: boolean;
      try {
        admitted = await options.modelGroups.admits({
          ...sender,
          agentKey,
          modelKey: namedModelKey(req.body),
          ...(typeof req.headers.authorization === 'string'
            ? { authorization: req.headers.authorization }
            : {}),
        });
      } catch {
        next(
          transportError(
            new HarnessUnavailableError(
              'Which models you may use could not be checked; try again',
            ),
          ),
        );
        return false;
      }
      if (!admitted) {
        next(
          transportError(
            new HarnessGroupAccessRefusedError('MODEL_GROUP_REQUIRED'),
          ),
        );
      }
      return admitted;
    };
    const surface = options.surface(req);
    if (surface === null) {
      await legacy();
      return;
    }
    const body: unknown = req.body;
    const chatMode = sentChatMode(body);
    const sessionId = req.params['conversationId'];
    const routed = <T extends Partial<RequestIdentity>>(
      identity: T,
    ): T & {
      agentKey?: string;
      sessionId?: string;
      via: ReturnType<typeof requestVia>;
      principalKind: ReturnType<typeof requestPrincipalKind>;
      surface: typeof surface;
      locale?: string;
      requestId?: string;
    } => ({
      ...identity,
      ...(agentKey !== null ? { agentKey } : {}),
      ...(sessionId !== undefined ? { sessionId } : {}),
      via: requestVia(req),
      principalKind: requestPrincipalKind(req),
      surface,
      ...localeOf(req),
      ...(req.context?.requestId !== undefined
        ? { requestId: req.context.requestId }
        : {}),
    });
    /**
     * WP-64: today's loop serves a turn admission never decided only if it answers no gated run's question. That
     * loop holds no gates, so a dry run's answer would act for real: refused in plain words, or, when the check
     * cannot be read, refused as unavailable (fail closed). A new conversation answers nothing and is not read.
     */
    const refusedAsGatedAnswer = async (
      who: RequestIdentity,
    ): Promise<boolean> => {
      if (sessionId === undefined) return false;
      let gated: boolean;
      try {
        gated = await service.answersGatedQuestion({
          orgId: who.orgId,
          actorId: who.actorId,
          sessionId,
          ...(agentKey !== null ? { agentKey } : {}),
        });
      } catch {
        next(
          transportError(
            new HarnessRunConfigUnavailableError(
              'Whether this message answers a question could not be checked; try again',
            ),
          ),
        );
        return true;
      }
      if (gated) next(transportError(gatesNeedRuntime()));
      return gated;
    };
    if (!options.enabled()) {
      // The harness's infrastructure is not ready, but its gated questions still bind their answers (WP-64). A new
      // conversation answers none: it reads nothing and reaches today's loop in the same tick (QA6-F11).
      // An org with the harness off asks no gated question to bind an answer (ADR-OPS-08).
      if (sessionId !== undefined) {
        if (options.identify !== undefined) {
          try {
            await options.identify(req);
          } catch {
            // The legacy handler resolves the identity again and answers the failure.
            next();
            return;
          }
        }
        const asked = requestIdentity(req);
        if (
          asked !== null &&
          (await orgHarnessOn(asked.orgId)) &&
          (await refusedAsGatedAnswer(asked))
        ) {
          return;
        }
      }
      const known = requestIdentity(req);
      service.declined(
        { ...routed(known ?? {}), chatMode },
        'infrastructure_unavailable',
      );
      await legacy();
      return;
    }
    if (options.identify !== undefined) {
      try {
        await options.identify(req);
      } catch {
        // The legacy handler resolves the identity again and answers the failure.
        next();
        return;
      }
    }
    const identity = requestIdentity(req);
    if (identity === null) {
      next();
      return;
    }
    const who = routed(identity);
    const base = {
      ...who,
      ...(typeof req.headers.authorization === 'string'
        ? { authorization: req.headers.authorization }
        : {}),
    };
    const turn = options.turn ?? 'send';
    const input = chatTurnInput(body, options.kind, turn, expandedCommand(res));
    // A knowledge-limited token's turn stays with today's loop, which holds it to the limit.
    if (input === null || knowledgeLimited(req)) {
      if (await refusedAsGatedAnswer(identity)) return;
      service.declined({ ...who, chatMode }, 'surface_request_not_supported');
      await legacy();
      return;
    }
    const caller = surface === 'slack' ? slackCaller(req, body) : undefined;
    const reply = surface === 'slack' ? slackReply(req, body) : undefined;
    const projectId =
      sessionId === undefined ? chatTurnProject(body) : undefined;
    const codingEngine =
      sessionId === undefined && turn === 'send' ? sentEngine : undefined;
    const repository =
      sessionId === undefined && turn === 'send' ? sentRepository : undefined;
    let start;
    try {
      start = await service.start({
        ...base,
        ...turnKey(req, body),
        ...(projectId !== undefined ? { projectId } : {}),
        ...(codingEngine !== undefined ? { codingEngine } : {}),
        ...(repository !== undefined ? { repository } : {}),
        ...(sentGates.kind === 'gates' ? { gates: sentGates.gates } : {}),
        ...(options.kind === 'chat' && durableRequested(body)
          ? { durableRequested: true }
          : {}),
        ...(legacyRequested(body) ? { legacyRequested: true } : {}),
        ...(turn === 'regenerate'
          ? {
              regenerate: {
                messageId: req.params['messageId'] as string,
                ...regeneratedCommandQuery(res),
              },
            }
          : {}),
        deadlineMs: CHAT_TURN_DEADLINE_MS,
        input: {
          ...input,
          ...(caller !== undefined ? { caller } : {}),
          ...(reply !== undefined ? { reply } : {}),
        },
        ...framesOf(req),
      });
    } catch (error) {
      // Nothing was written: today's loop answers, as a client used to after a 503 (UX3 AR-2). Not a pin
      // that could not be made: that loop would run the turn on the live documents.
      if (
        error instanceof HarnessUnavailableError &&
        !(error instanceof HarnessRunConfigUnavailableError)
      ) {
        if (await refusedAsGatedAnswer(identity)) return;
        service.declined({ ...who, chatMode }, 'admission_unavailable');
        await legacy();
        return;
      }
      next(error instanceof BadRequestError ? error : transportError(error));
      return;
    }
    if (start.route === 'legacy') {
      await legacy(start.resolved);
      return;
    }
    writeSseHead(res);
    if (start.route === 'queued') {
      for (const frame of start.frames) res.write(formatSseFrame(frame));
      res.end();
      return;
    }
    const unwatch = options.watchers?.watch({
      orgId: identity.orgId,
      // The bot's stream watches for its thread, not for the person's web app (D-SLACK-ASK).
      userId:
        surface === 'slack'
          ? slackThreadViewer(identity.actorId)
          : identity.actorId,
      sessionId: start.sessionId,
    });
    const stream = start.open(responseSink(res));
    res.on('close', () => {
      unwatch?.();
      stream.close('client_closed');
    });
    try {
      await stream.done;
    } finally {
      unwatch?.();
    }
  };
  return (req, res, next) => {
    handle(req, res, next).catch(next);
  };
}
