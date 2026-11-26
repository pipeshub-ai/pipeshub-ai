import { mongoTx } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
/**
 * Node side of the L4 fault suite (`integration-tests/harness/fault`): the
 * harness composition `es.container.ts` wires, on a real MongoDB replica set,
 * with the app's middleware order (JSON body, the global XSS middleware, the
 * session and internal routers at their real prefixes, the ErrorMiddleware),
 * authority documents in Redis KV, the Redis LiveBus, the sweeper and audit
 * anchorer, and the real OutboxDispatcher publishing wake hints to Kafka or
 * Redis Streams.
 *
 * Simulated: authentication (the bearer `<orgId>.<userId>` is the identity),
 * the agent definition, and the org's rollout (on; the chat-mode gate is the
 * product's, so the built-in assistant is admitted in its own modes). The
 * chat stream routes (web, API, MCP) and the Slack bot's internal one are the
 * product's chat-turn middleware (P1b C-5, C-20, LAT1) in front of a
 * stand-in legacy handler, which answers 501; run-ready notices go to Redis
 * after commit and the outbox polls every `OUTBOX_POLL_MS` (default 200); the bearer stands in for the Slack hydration
 * too. Adds `/__fault/*` read routes for the checker; they are not product
 * routes.
 *
 * Run with ts-node from backend/nodejs/apps; configuration comes from the
 * process environment exactly as the app reads it, plus MESSAGE_BROKER and
 * PORT. Prints `FAULT NODE READY <port>` when it listens.
 */
import 'reflect-metadata';
import http from 'http';
import express, { NextFunction, Request, Response } from 'express';
import mongoose from 'mongoose';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { Container } from 'inversify';
import { Logger } from '../../../src/libs/services/logger.service';
import { agentHarnessHealth } from '../../../src/modules/agent-harness/config';
import { trackRequests } from '../../../src/libs/services/http-drain';
import { processShutdown, SHUTDOWN_DEADLINE_MS } from '../../../src/libs/services/process-shutdown';
import { MongoExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/mongo';
import {
  SWEEP_INTERVAL_MS,
  SWEEPER_LEASE_MS,
  SWEEPER_LEASE_NAME,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import { MongoService } from '../../../src/libs/services/mongo.service';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config';
import {
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_DATA_KEYS,
  AGENT_HARNESS_WORKER_FEATURES,
  bindAgentHarness,
  createChatCancelMiddleware,
  createChatTurnMiddleware,
  HarnessJournal,
  prepareAgentHarnessAuthority,
  prepareAgentHarnessStore,
  startAgentHarnessBackgroundServices,
  stopAgentHarnessBackgroundServices,
} from '../../../src/modules/agent-harness/composition';
import {
  createApprovalsStore,
  createHarnessAuthorityStores,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { RedisLiveBus } from '../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { RedisRunWakeup } from '../../../src/modules/agent-harness/adapters/redis/redis-run-wakeup';
import { LiveBusCapabilityProbe } from '../../../src/modules/agent-harness/adapters/redis/live-bus-probe';
import { harnessRedisProvider } from '../../../src/modules/agent-harness/adapters/redis/harness-redis';
import type { RedisConfig } from '../../../src/libs/types/redis.types';
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationReader } from '../../../src/libs/repositories/agent-harness/conversation-reader/mongo';
import { createAgentHarnessInternalRouter, AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { decideChatModeRollout, HarnessRolloutDecision } from '../../../src/modules/agent-harness/domain/rollout';
import { slackSurface, webSurface } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { RedisRunReadyNotifier } from '../../../src/modules/agent-harness/adapters/redis/redis-run-ready';
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { OutboxDispatcher, outboxBacklog } from '../../../src/libs/services/outbox/outbox.dispatcher';
import { changeEventsGroupId } from '../../../src/modules/agent-harness/adapters/messaging/control-plane-event-broker';
import type { ChangeEventBroker } from '../../../src/modules/agent-harness/binders/change-notifier';
import {
  buildRedisBrokerConfig,
  createBroadcastMessageConsumer,
  createMessageAdmin,
  createMessageProducer,
  ensureMessageTopicsExist,
  ResolvedMessageBrokerConfig,
} from '../../../src/libs/services/message-broker.factory';
import { MessageBrokerType } from '../../../src/libs/types/messaging.types';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { WorkerFeatureAvailability } from '../../../src/modules/agent-harness/application/worker-feature-availability';
import { WorkerFeature } from '../../../src/modules/agent-harness/domain/worker-compat';
import { calibrationUs, CpuLedger, harnessRequests } from './cpu-ledger';

const PORT = Number(process.env.PORT);
const DRAIN_MS = 10_000;

/**
 * What happens to this process around its next claim (OPS1). `kill`: a crash
 * the moment the claim's transaction has committed, before it is answered.
 * `term`: a deploy's SIGTERM arriving there, the answer still a second away.
 * `term-before`: the SIGTERM arrives while the claim has yet to reach the store.
 */
type ClaimFailpoint = 'kill' | 'term' | 'term-before';
let atClaimCommit: ClaimFailpoint | null = null;
const TERM_ANSWER_DELAY_MS = 1_000;
const TERM_BEFORE_CLAIM_MS = 300;
const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function installClaimFailpoint(): void {
  const claim = MongoExecutionStore.prototype.claim;
  MongoExecutionStore.prototype.claim = async function failpoint(
    this: MongoExecutionStore,
    ...args: Parameters<typeof claim>
  ) {
    const action = atClaimCommit;
    atClaimCommit = null;
    if (action === 'term-before') {
      process.kill(process.pid, 'SIGTERM');
      await pause(TERM_BEFORE_CLAIM_MS);
    }
    const claimed = await claim.apply(this, args);
    if (action === 'kill') process.kill(process.pid, 'SIGKILL');
    if (action === 'term') {
      process.kill(process.pid, 'SIGTERM');
      await pause(TERM_ANSWER_DELAY_MS);
    }
    return claimed;
  };
}

const agentDefinition = (agentKey: string): AgentRunDefinition => ({
  agentKey,
  definitionVersion: 1,
  models: [{ modelKey: 'model-1', modelName: 'scripted-x', provider: 'openAI', isReasoning: false }],
  defaultReasoningEffort: 'medium',
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  webSearch: null,
});

const ENABLED: HarnessRolloutDecision = { useHarness: true, harnessMajor: 2, reason: 'enabled' };

const authenticate = (req: Request, res: Response, next: NextFunction): void => {
  const [orgId, userId] = (req.headers.authorization ?? '')
    .replace(/^Bearer /, '')
    .split('.');
  if (!orgId || !userId) {
    res.status(401).json({ error: { message: 'unauthenticated' } });
    return;
  }
  (req as Request & { user: unknown }).user = { orgId, userId };
  next();
};

const route =
  (handler: (req: Request) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction): void => {
    handler(req).then((body) => res.json(body), next);
  };

const resolvedBroker = (redis: RedisConfig): ResolvedMessageBrokerConfig =>
  process.env.MESSAGE_BROKER === 'kafka'
    ? {
        type: MessageBrokerType.KAFKA,
        kafka: {
          type: MessageBrokerType.KAFKA,
          brokers: (process.env.KAFKA_BROKERS ?? '').split(','),
          clientId: 'fault-node',
        },
      }
    : {
        type: MessageBrokerType.REDIS,
        redis: buildRedisBrokerConfig(redis),
      };

/** This Node's subscription to `control-plane-events`, as `controlPlaneEventBroker` wires it in the app (OPS1 item 4). */
const changeEvents = (broker: ResolvedMessageBrokerConfig, logger: Logger): ChangeEventBroker => ({
  producer: createMessageProducer(broker, logger),
  consumer: createBroadcastMessageConsumer(broker, logger, changeEventsGroupId()),
  admin: createMessageAdmin(broker, logger),
});

async function main(): Promise<void> {
  const logger = Logger.getInstance({ service: 'fault-node' });
  // The app's own connection (its timeouts decide how long a request waits on an unreachable Mongo).
  const attributeCpu = process.env.FAULT_CPU_ATTRIBUTE === 'true';
  const mongoUri = process.env.MONGO_URI as string;
  await new MongoService({
    // The CPU bench's command census: the driver reports each command only when asked to.
    uri: attributeCpu ? `${mongoUri}${mongoUri.includes('?') ? '&' : '?'}monitorCommands=true` : mongoUri,
    db: process.env.MONGO_DB_NAME as string,
  }).initialize();

  const kvConfig = loadConfigurationManagerConfig();
  const kv = KeyValueStoreService.getInstance(kvConfig);
  await kv.connect();
  // The app's own (es.container): the worker reads the configured Redis whole, `REDIS_DB` included.
  const redis = harnessRedisProvider(kvConfig.redisConfig);

  const container = new Container();
  container.bind('Logger').toConstantValue(logger);
  container.bind('AuthMiddleware').toConstantValue({ authenticate });
  bindAgentHarness(container, {
    sessions: new HarnessChatSessionRepository(),
    conversations: new HarnessConversationWriter(),
    history: new HarnessHistorySource(),
    agentAccess: { authorizeRun: async ({ agentKey }) => agentDefinition(agentKey) },
    rollout: {
      decide: async (request) => decideChatModeRollout(ENABLED, request.model.chatMode, request.assistant === true),
      decideForOrg: async () => ({
        useHarness: true,
        harnessMajor: 2,
        reason: 'enabled',
        harnessOn: true, resumeInFlightRuns: true,
      }),
      // What the control plane folds as the org's rollout rows: the same answer as `decide` above.
      settings: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }),
    },
    authority: createHarnessAuthorityStores(kv),
    changeEvents: changeEvents(resolvedBroker(kvConfig.redisConfig), logger),
    // The org's approvals document, so the rig can save it as an admin does.
    approvalSettings: {
      document: createApprovalsStore(kv),
    },
    conversationReader: new HarnessConversationReader(),
    liveBus: new RedisLiveBus(redis),
    wakeup: new RedisRunWakeup(redis),
    runReady: new RedisRunReadyNotifier(redis, logger),
    capabilityProbes: [new LiveBusCapabilityProbe(redis)],
  });
  installClaimFailpoint();
  await prepareAgentHarnessStore(logger, process.env, container.get(AGENT_HARNESS_CAPABILITIES), undefined, {
    whenPrepared: () => startAgentHarnessBackgroundServices(container),
  });
  await prepareAgentHarnessAuthority(container, logger);
  startAgentHarnessBackgroundServices(container);

  const broker = resolvedBroker(kvConfig.redisConfig);
  await ensureMessageTopicsExist(broker, logger);
  const producer = createMessageProducer(broker, logger);
  await producer.connect();
  new OutboxDispatcher(producer, logger, Number(process.env.OUTBOX_POLL_MS ?? 200)).start();

  const journal = container.get<HarnessJournal>(AGENT_HARNESS_DATA_KEYS);
  const app = express();
  app.use(express.json({ limit: '10mb' }));
  app.use(xssSanitizationMiddleware);
  const cpu = new CpuLedger(attributeCpu);
  app.use(cpu.middleware(harnessRequests));
  mongoose.connection.getClient().on('commandStarted', (event) => {
    const target = event.command[event.commandName] as unknown;
    cpu.command(event.commandName, typeof target === 'string' ? target : '');
  });
  app.get('/__fault/cpu', (_req, res) => {
    res.json(cpu.snapshot());
  });
  app.get('/__fault/cpu/calibration', (_req, res) => {
    res.json({ us: calibrationUs() });
  });
  app.post('/__fault/cpu/reset', (_req, res) => {
    cpu.reset();
    res.json({});
  });
  app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
  app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
  const legacyChat = (_req: Request, res: Response): void => {
    res.status(501).json({ error: { message: 'the legacy chat route is not part of the fault stack' } });
  };
  const chatTurn = createChatTurnMiddleware(container, { kind: 'chat', surface: webSurface });
  const agentTurn = createChatTurnMiddleware(container, { kind: 'agent', surface: webSurface });
  app.post('/api/v1/conversations/stream', authenticate, chatTurn, legacyChat);
  app.post('/api/v1/conversations/:conversationId/messages/stream', authenticate, chatTurn, legacyChat);
  app.post('/api/v1/agents/:agentKey/conversations/stream', authenticate, agentTurn, legacyChat);
  app.post('/api/v1/agents/:agentKey/conversations/:conversationId/messages/stream', authenticate, agentTurn, legacyChat);
  app.post(
    '/api/v1/conversations/:conversationId/message/:messageId/regenerate',
    authenticate,
    createChatTurnMiddleware(container, { kind: 'chat', surface: webSurface, turn: 'regenerate' }),
    legacyChat,
  );
  app.post(
    '/api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
    authenticate,
    createChatTurnMiddleware(container, { kind: 'agent', surface: webSurface, turn: 'regenerate' }),
    legacyChat,
  );
  app.post('/api/v1/conversations/:conversationId/cancel', authenticate, createChatCancelMiddleware(container, { kind: 'chat' }), legacyChat);
  app.post(
    '/api/v1/agents/:agentKey/conversations/:conversationId/cancel',
    authenticate,
    createChatCancelMiddleware(container, { kind: 'agent' }),
    legacyChat,
  );
  app.post(
    '/api/v1/conversations/internal/stream',
    authenticate,
    createChatTurnMiddleware(container, { kind: 'chat', surface: slackSurface }),
    legacyChat,
  );
  app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
  app.get(
    '/__fault/journal/:orgId/:sessionId',
    route(async (req) => ({
      events: await journal.store.readStream(String(req.params.orgId), String(req.params.sessionId), 0, 100_000),
    })),
  );
  app.get(
    '/__fault/conversation/:sessionId',
    route(async (req) => ({
      session: await ChatSession.findById(req.params.sessionId).lean(),
      messages: await ChatSessionMessage.find({ sessionId: req.params.sessionId }).sort({ seq: 1 }).lean(),
    })),
  );
  app.get(
    '/__fault/run/:runId',
    route(async (req) => ({
      run: await mongoose.connection.db!.collection('agentRuns').findOne({ runId: req.params.runId }),
    })),
  );
  // Who holds each leader lease (the sweeper's among them) and until when: evidence for a stalled recovery (CPD6-F-RU4A).
  app.get(
    '/__fault/leases',
    route(async () => ({
      at: Date.now(),
      leases: await mongoose.connection.db!.collection('agentHarnessLeases').find({}).toArray(),
    })),
  );
  // What admission decides with (its cache included): a new conversation goes to legacy while this is false (LAT1).
  const availability = container.get<WorkerFeatureAvailability>(AGENT_HARNESS_WORKER_FEATURES);
  app.get(
    '/__fault/liveness/:feature',
    route(async (req) => ({ live: await availability.live(String(req.params.feature) as WorkerFeature) })),
  );
  app.get('/__fault/outbox', route(async () => ({ backlog: await outboxBacklog() })));
  // The harness's part of the application's health route (OPS1).
  app.get('/__fault/health', route(async () => ({ agentHarness: agentHarnessHealth() })));
  app.post(
    '/__fault/failpoint',
    route(async (req) => {
      atClaimCommit = (req.body as { atClaimCommit: ClaimFailpoint | null }).atClaimCommit;
      return { atClaimCommit };
    }),
  );
  // The sweeper's leader lease and a run's attempt lease as stored, with the store's own time to compare them on.
  app.get(
    '/__fault/leases/:runId',
    route(async (req) => {
      const db = mongoose.connection.db!;
      const [hello, leader, run] = await Promise.all([
        db.admin().command({ hello: 1 }),
        db.collection<{ _id: string }>('agentHarnessLeases').findOne({ _id: SWEEPER_LEASE_NAME }),
        db.collection('agentRuns').findOne({ runId: req.params.runId }),
      ]);
      return {
        now: (hello['localTime'] as Date).getTime(),
        sweepIntervalMs: SWEEP_INTERVAL_MS,
        sweeperLeaseMs: SWEEPER_LEASE_MS,
        leader,
        run,
      };
    }),
  );
  // PERF-C: what is committed and not on the chain, read without linking it (a read through the store would link).
  app.get(
    '/__fault/audit/:orgId/:runId',
    route(async (req) => ({
      unlinked: await journal.audit.unlinked(String(req.params.orgId)),
      records: await mongoose.connection.db!
        .collection('agentAuditRecords')
        .countDocuments({ orgId: req.params.orgId, runId: req.params.runId }),
    })),
  );
  // The crash between a commit and its link: the audit entry commits as any commit's does, and the process dies
  // before the committing session ends, which is when the link would run.
  app.post(
    '/__fault/audit/:orgId/:runId/crash-after-commit',
    route(async (req) => {
      const orgId = String(req.params.orgId);
      const runId = String(req.params.runId);
      const session = await mongoose.startSession();
      await session.withTransaction(
        () =>
          journal.audit.append(
            orgId,
            [
              {
                kind: 'run.cancel_requested',
                sessionId: `fault-${runId}`,
                streamId: `fault-${runId}`,
                runId,
                journalSeq: 1,
                writer: 'node',
                attemptId: null,
                fence: null,
                fields: { by: 'fault-suite' },
                dataDigest: `sha256:${'0'.repeat(64)}`,
              },
            ],
            new Date().toISOString(),
            mongoTx(session),
          ),
        DURABLE_TRANSACTION_OPTIONS,
      );
      process.kill(process.pid, 'SIGKILL');
      return {};
    }),
  );
  // OPS2: a full disk, where every write the process makes (each log line among them) throws again.
  app.post(
    '/__fault/uncaught',
    route(async (req) => {
      const { count } = req.body as { count: number };
      for (let i = 0; i < count; i += 1) {
        setImmediate(() => {
          throw new Error('ENOSPC: no space left on device, write');
        });
      }
      return { count };
    }),
  );
  app.use(ErrorMiddleware.handleError());
  const server = http.createServer((req, res) => {
    app(req, res);
  });
  const requests = trackRequests(server);
  server.listen(PORT, () => {
    logger.info('fault node ready', { port: PORT, broker: broker.type });
     
    console.log(`FAULT NODE READY ${String(PORT)}`);
  });
  // A rolling deploy's stop, in the order of the app's `stop()`: leadership, then the answers owed, then the exit;
  // through the product's one shutdown, as index.ts does.
  const shutdown = processShutdown({
    stop: () => stopAgentHarnessBackgroundServices(container).then(() => requests.drain(DRAIN_MS)),
    logger,
    exit: (code) => process.exit(code),
    deadlineMs: SHUTDOWN_DEADLINE_MS,
  });
  process.on('SIGTERM', () => {
    shutdown.signal('SIGTERM');
  });
  process.on('uncaughtException', (error) => {
    shutdown.fault('uncaughtException', error);
  });
}

main().catch((error: unknown) => {
   
  console.error('fault node failed', error);
  process.exit(1);
});
