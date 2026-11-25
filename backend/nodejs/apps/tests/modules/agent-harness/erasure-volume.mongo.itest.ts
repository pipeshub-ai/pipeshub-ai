/**
 * QAFINAL-FU-VOL on a MongoDB replica set: the store commands a person's deletion (`handOverUser`), one
 * conversation's erasure, a session's whole erasure job, an org deletion's page of sessions and the sweep's abandoned
 * forks cost, at a small and a large size (R30-LAT, R31-LAT). The budgets are pre-registered in
 * frontend/tests/e2e/perf/volume-budgets.json (`stores`); a count that grows with the data is a round trip per
 * record inside one request or one transaction.
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" TS_NODE_PROJECT=tsconfig.test.json npx mocha \
 *     --no-config --require reflect-metadata --require ts-node/register --exit --timeout 120000 \
 *     tests/modules/agent-harness/erasure-volume.mongo.itest.ts
 */
import 'reflect-metadata';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import mongoose, { Model, Types } from 'mongoose';
import type { CommandStartedEvent, CommandSucceededEvent } from 'mongodb';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels, resetHarnessMongo } from './support/harness-mongo';
import { FakeChatAttachments } from './support/chat-attachments';
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { AgentPrincipalErasureModel } from '../../../src/libs/repositories/agent-harness/principal-erasure/schema/principal-erasure.schema';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import { erasureJobId } from '../../../src/modules/agent-harness/domain/session-erasure';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { PrincipalDeletionService } from '../../../src/modules/user_management/services/principal-deletion.service';
import { MongoErasureJobStore, MongoSessionRunLookup } from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { MongoSessionStoreEraser } from '../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import { MongoOrgData } from '../../../src/libs/repositories/agent-harness/principal-erasure/mongo';
import { MongoAbandonedForks } from '../../../src/libs/repositories/agent-harness/abandoned-forks/mongo';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { AgentSessionForkModel } from '../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import { createErasureSteps } from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import type { SessionRunLookup } from '../../../src/modules/agent-harness/ports/session-erasure';
import { ABANDONED_FORK_AFTER_MS } from '../../../src/modules/agent-harness/domain/session-retention';
import { RUN_KEY_DIGEST_PATTERN } from '../../../src/modules/agent-harness/domain/run-key-digest';
import { testCipher, testRunKeys } from './support/data-protection';
import { sweeperWithAbandonedForks } from './support/agent-erasure-sweeper';

registerHarnessModels([
  Users,
  ChatSession,
  ChatSessionMessage,
  Citation,
  AgentPrincipalErasureModel,
  AgentSessionErasureModel,
  AgentRunModel,
  AgentSessionForkModel,
] as unknown as Array<Model<never>>);

interface StoreBudgets {
  'person-handover': { small: number; large: number; extraCommands: number };
  'conversation-erase': { small: number; large: number; extraCommandsPer1000: number };
  'session-erasure-job': { small: number; large: number; runReadsPerPass: number; extraCommandsPer1000: number };
  'org-sessions-page': { sessions: number; limit: number; idsPerPagePerSource: number };
  'abandoned-forks': { small: number; large: number; extraCommands: number };
}

const BUDGETS = (
  JSON.parse(
    readFileSync(join(__dirname, '../../../../../../frontend/tests/e2e/perf/volume-budgets.json'), 'utf8'),
  ) as { stores: StoreBudgets }
).stores;

/** Monitoring and session housekeeping the driver sends whatever the work. */
const UNCOUNTED = new Set(['hello', 'isMaster', 'ismaster', 'ping', 'endSessions']);

const commandsDuring = async (work: () => Promise<unknown>): Promise<{ total: number; by: Record<string, number> }> => {
  const by: Record<string, number> = {};
  const count = (event: CommandStartedEvent): void => {
    if (UNCOUNTED.has(event.commandName)) return;
    const target = event.command[event.commandName] as unknown;
    const key = typeof target === 'string' ? `${event.commandName} ${target}` : event.commandName;
    by[key] = (by[key] ?? 0) + 1;
  };
  const client = mongoose.connection.getClient();
  client.on('commandStarted', count);
  try {
    await work();
  } finally {
    client.off('commandStarted', count);
  }
  return { total: Object.values(by).reduce((sum, n) => sum + n, 0), by };
};

const oid = (): Types.ObjectId => new Types.ObjectId();

describe('QAFINAL-FU-VOL: store commands at volume on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  let docStore: string | undefined;
  before(() => {
    docStore = process.env.DOC_STORE;
    delete process.env.DOC_STORE;
  });
  after(() => {
    if (docStore === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = docStore;
  });
  beforeEach(resetHarnessMongo);

  describe("person-handover: a person's deletion over their conversations", () => {
    /** A person with `n` conversations, one in four shared with a reader and the admin, beside the org's admin. */
    const seedPerson = async (n: number) => {
      const org = oid();
      const [leaving, admin, reader] = [oid(), oid(), oid()];
      for (const [index, _id] of [admin, leaving, reader].entries()) {
        // One by one: the save hook gives each account its unique slug.
        await new Users({
          _id,
          orgId: org,
          email: `${String(_id)}@example.com`,
          kind: 'human',
          role: _id === admin ? 'admin' : 'member',
          isDisabled: false,
          isDeleted: false,
          createdAt: new Date(Date.UTC(2026, 0, 1) + index),
        }).save({ timestamps: false });
      }
      const conversations = Array.from({ length: n }, (_unused, index) => {
        const shared = index % 4 === 0;
        return {
          _id: oid(),
          orgId: org,
          userId: leaving,
          initiator: leaving,
          title: 'c',
          isShared: shared,
          sharedWith: shared
            ? [
                { userId: reader, accessLevel: 'read' },
                { userId: admin, accessLevel: 'write' },
              ]
            : [],
          isDeleted: false,
        };
      });
      await ChatSession.insertMany(conversations);
      return {
        org: String(org),
        leaving: String(leaving),
        admin: String(admin),
        reader: String(reader),
        shared: conversations.filter((c) => c.isShared).map((c) => String(c._id)),
        own: conversations.filter((c) => !c.isShared).map((c) => String(c._id)),
      };
    };

    const handOver = async (n: number) => {
      const person = await seedPerson(n);
      const service = new PrincipalDeletionService();
      let result: { erased: number; transferred: number } | undefined;
      const commands = await commandsDuring(async () => {
        result = await service.handOverUser(person.org, person.leaving, person.admin);
      });
      return { person, commands, result: result! };
    };

    it('VOL-1: the commands do not grow with the conversations, and every conversation ends where it should', async () => {
      const budget = BUDGETS['person-handover'];
      await handOver(1);
      const small = await handOver(budget.small);
      const large = await handOver(budget.large);

      const { person, result } = large;
      expect(result).to.deep.equal({ erased: person.own.length, transferred: person.shared.length });
      const sessions = await ChatSession.find({ orgId: new Types.ObjectId(person.org) })
        .select('+erasureRequestedAt')
        .lean<Array<{ _id: Types.ObjectId; userId: Types.ObjectId; initiator: Types.ObjectId; isDeleted: boolean; erasureRequestedAt?: number; sharedWith: Array<{ userId: Types.ObjectId }> }>>();
      const byId = new Map(sessions.map((s) => [String(s._id), s]));
      for (const id of person.shared) {
        const s = byId.get(id)!;
        expect([String(s.userId), String(s.initiator), s.isDeleted]).to.deep.equal([person.admin, person.admin, false]);
        expect(s.sharedWith.map((m) => String(m.userId))).to.deep.equal([person.reader]);
      }
      for (const id of person.own) {
        const s = byId.get(id)!;
        expect(s.isDeleted).to.equal(true);
        expect(s.erasureRequestedAt).to.be.a('number');
      }
      const jobs = await AgentSessionErasureModel.find({ orgId: person.org }).lean();
      expect(jobs.map((j) => j._id).sort()).to.deep.equal(person.own.map((id) => erasureJobId(person.org, id)).sort());
      expect(jobs.every((j) => j.status === 'pending' && j.reason === 'user_delete' && j.by === `p_${person.admin}`)).to.equal(true);
      const job = await AgentPrincipalErasureModel.findOne({ orgId: person.org, userId: person.leaving }).lean<{ transfers: unknown[] }>();
      expect(job!.transfers).to.have.length(person.shared.length);

      const extra = large.commands.total - small.commands.total;
      console.log(
        `person-handover commands: small(${String(budget.small)})=${String(small.commands.total)} large(${String(budget.large)})=${String(large.commands.total)}`,
        JSON.stringify(large.commands.by),
      );
      expect(extra, `extra commands at ${String(budget.large)} conversations: ${JSON.stringify(large.commands.by)}`).to.be.at.most(budget.extraCommands);
    });
  });

  describe("conversation-erase: one conversation's documents", () => {
    const seedConversation = async (messages: number) => {
      const [org, session, owner] = [oid(), oid(), oid()];
      await ChatSession.create({ _id: session, orgId: org, userId: owner, initiator: owner, title: 'c', isDeleted: true });
      const citations = Array.from({ length: messages }, (_unused, i) => ({
        _id: oid(),
        content: `c ${String(i)}`,
        chunkIndex: i,
        citationType: 'vectordb|document',
        metadata: { orgId: String(org), mimeType: 'text/plain', recordId: `rec-${String(i)}`, recordName: 'r', origin: 'UPLOAD', extension: 'txt' },
      }));
      await Citation.insertMany(citations);
      await ChatSessionMessage.insertMany(
        citations.map((citation, seq) => ({
          sessionId: session,
          orgId: org,
          seq,
          messageType: 'bot_response',
          content: 'm',
          citations: [{ citationId: citation._id }],
        })),
      );
      return { org: String(org), session: String(session) };
    };

    const erase = async (messages: number) => {
      const { org, session } = await seedConversation(messages);
      let erased: Awaited<ReturnType<HarnessConversationEraser['erase']>> | undefined;
      const commands = await commandsDuring(async () => {
        erased = await new HarnessConversationEraser(new FakeChatAttachments()).erase(org, session);
      });
      return { org, session, commands, erased: erased! };
    };

    it('VOL-2: at most a couple of commands more per thousand messages, and nothing of the conversation is left', async () => {
      const budget = BUDGETS['conversation-erase'];
      await erase(1);
      const small = await erase(budget.small);
      const large = await erase(budget.large);

      expect(large.erased).to.deep.include({ citations: budget.large, messages: budget.large, sessions: 1 });
      const org = new Types.ObjectId(large.org);
      expect(await ChatSessionMessage.countDocuments({ orgId: org, sessionId: new Types.ObjectId(large.session) })).to.equal(0);
      expect(await Citation.countDocuments({ 'metadata.orgId': large.org })).to.equal(0);

      const extra = large.commands.total - small.commands.total;
      const allowed = (budget.extraCommandsPer1000 * (budget.large - budget.small)) / 1000;
      console.log(
        `conversation-erase commands: small(${String(budget.small)})=${String(small.commands.total)} large(${String(budget.large)})=${String(large.commands.total)} allowed extra ${allowed.toFixed(2)}`,
        JSON.stringify(large.commands.by),
      );
      expect(extra, JSON.stringify(large.commands.by)).to.be.at.most(allowed);
    });
  });
  describe("session-erasure-job: one session's erasure job over its ended runs", () => {
    /** A deleted conversation whose `runs` ended, each stored by an older build with the caller's own key. */
    const seedSession = async (runs: number) => {
      const [org, session, owner] = [String(oid()), oid(), oid()];
      await ChatSession.create({ _id: session, orgId: new Types.ObjectId(org), userId: owner, initiator: owner, title: 'c', isDeleted: true });
      await AgentRunModel.collection.insertMany(
        Array.from({ length: runs }, (_unused, i) => ({
          orgId: org,
          runId: `run-${String(session)}-${String(i)}`,
          sessionId: String(session),
          status: 'completed',
          revision: 3,
          harnessMajor: 2,
          idempotencyKey: `legacy-key-${String(i)}`,
        })),
      );
      return { org, session: String(session) };
    };

    const eraseJob = async (runs: number) => {
      const { org, session } = await seedSession(runs);
      let runReads = 0;
      const lookup = new MongoSessionRunLookup();
      const counted: SessionRunLookup = {
        runsOf: (orgId, sessionId) => {
          runReads += 1;
          return lookup.runsOf(orgId, sessionId);
        },
      };
      const conversations = new HarnessConversationEraser(new FakeChatAttachments());
      const jobs = new MongoErasureJobStore();
      const runner = new SessionErasureRunner({
        jobs,
        conversations,
        erasers: createErasureSteps({
          runs: counted,
          executions: { control: () => Promise.reject(new Error('every run has ended')) },
          workspaceKeys: { shred: () => Promise.resolve(false) },
          liveBus: null,
          artifacts: {
            eraseConversation: () => Promise.resolve({ artifacts: 0, blobs: 0, remaining: false }),
            collectDiscarded: () => Promise.resolve({ artifacts: 0, blobs: 0, remaining: false }),
          } as never,
          stores: new MongoSessionStoreEraser(testCipher(), testRunKeys()),
          conversations,
          journal: { store: { lastSeq: () => Promise.resolve({}) } as never, preparer: {} as never, transaction: inMongoTransaction },
        }),
      });
      await jobs.enqueue({ orgId: org, sessionId: session, reason: 'user_delete', by: 'p_volume' });
      const job = await jobs.claimDue('volume', 60_000);
      let result: string | undefined;
      const commands = await commandsDuring(async () => {
        result = await runner.run(job!);
      });
      return { org, session, commands, runReads, result };
    };

    it("VOL-3: the job reads the session's runs at most twice in a pass, its commands barely grow with the runs, and every key ends a digest", async () => {
      const budget = BUDGETS['session-erasure-job'];
      await eraseJob(1);
      const small = await eraseJob(budget.small);
      const large = await eraseJob(budget.large);

      expect(large.result).to.equal('completed');
      const keys = await AgentRunModel.collection.find({ orgId: large.org, sessionId: large.session }).project<{ idempotencyKey: string }>({ idempotencyKey: 1 }).toArray();
      expect(keys).to.have.length(budget.large);
      expect(keys.every((run) => RUN_KEY_DIGEST_PATTERN.test(run.idempotencyKey))).to.equal(true);
      expect(await ChatSession.countDocuments({ _id: new Types.ObjectId(large.session) })).to.equal(0);

      const extra = large.commands.total - small.commands.total;
      const allowed = (budget.extraCommandsPer1000 * (budget.large - budget.small)) / 1000;
      console.log(
        `session-erasure-job: runsOf reads small=${String(small.runReads)} large=${String(large.runReads)}; commands small(${String(budget.small)})=${String(small.commands.total)} large(${String(budget.large)})=${String(large.commands.total)} allowed extra ${allowed.toFixed(2)}`,
        JSON.stringify(large.commands.by),
      );
      expect(large.runReads, "reads of the session's runs in one pass").to.be.at.most(budget.runReadsPerPass);
      expect(extra, JSON.stringify(large.commands.by)).to.be.at.most(allowed);
    });
  });

  describe("org-sessions-page: an org deletion's page of session ids", () => {
    it('VOL-4: a page reads at most one page of ids from each source, and the pages together name every session once', async () => {
      const budget = BUDGETS['org-sessions-page'];
      const org = String(oid());
      // Named only by the harness's own records, as a session with no conversation document is.
      const ids = Array.from({ length: budget.sessions }, () => String(oid())).sort();
      const db = mongoose.connection.db!;
      await db.collection('agentRuns').insertMany(ids.map((sessionId, i) => ({ orgId: org, sessionId, runId: `r-${String(i)}` })));
      await db.collection('agentEventCounters').insertMany(ids.map((sessionId) => ({ orgId: org, sessionId })));
      await db.collection('agentWorkspaceKeys').insertMany(ids.filter((_id, i) => i % 2 === 0).map((sessionId) => ({ orgId: org, sessionId })));
      try {
        const store = new MongoOrgData();
        const seen: string[] = [];
        const perPage: number[] = [];
        let after: string | null = null;
        for (;;) {
          let returned = 0;
          const count = (event: CommandSucceededEvent): void => {
            const reply = event.reply as { values?: unknown[]; cursor?: { firstBatch?: unknown[]; nextBatch?: unknown[] } };
            returned += (reply.values ?? reply.cursor?.firstBatch ?? reply.cursor?.nextBatch ?? []).length;
          };
          const client = mongoose.connection.getClient();
          client.on('commandSucceeded', count);
          let page: string[];
          try {
            page = await store.sessionsAfter(org, after, budget.limit);
          } finally {
            client.off('commandSucceeded', count);
          }
          perPage.push(returned);
          seen.push(...page);
          if (page.length < budget.limit) break;
          after = page[page.length - 1]!;
        }
        console.log(`org-sessions-page: ids the store sent per page ${JSON.stringify(perPage)} (sessions ${String(budget.sessions)}, limit ${String(budget.limit)})`);
        expect(seen).to.deep.equal(ids);
        // chatSessions and the three harness sources.
        expect(Math.max(...perPage), JSON.stringify(perPage)).to.be.at.most(4 * budget.idsPerPagePerSource);
      } finally {
        for (const name of ['agentEventCounters', 'agentWorkspaceKeys']) await db.collection(name).deleteMany({ orgId: org });
      }
    });
  });

  describe("abandoned-forks: the sweep's abandoned forks of one org", () => {
    const sweepForks = async (forks: number) => {
      const org = String(oid());
      const now = Date.now();
      await AgentSessionForkModel.collection.insertMany(
        Array.from({ length: forks }, (_unused, i) => ({
          _id: `${org}:fork-of:${String(i)}` as never,
          orgId: org,
          sourceSessionId: String(oid()),
          actorId: 'p_volume',
          forkSessionId: String(oid()),
          result: null,
          createdAt: now - ABANDONED_FORK_AFTER_MS - 1_000 - i,
          completedAt: null,
        })),
      );
      const jobs = new MongoErasureJobStore();
      const sweeper = sweeperWithAbandonedForks({ forks: new MongoAbandonedForks(), erasures: jobs, transaction: inMongoTransaction, now });
      let erased = 0;
      const commands = await commandsDuring(async () => {
        erased = (await sweeper.tick())?.abandonedForks ?? 0;
      });
      return { org, erased, commands };
    };

    it('VOL-5: the commands do not grow with the forks, every fork record goes and each fork session has its erasure job', async () => {
      const budget = BUDGETS['abandoned-forks'];
      await sweepForks(1);
      const small = await sweepForks(budget.small);
      await resetHarnessMongo();
      const large = await sweepForks(budget.large);

      expect(large.erased).to.equal(budget.large);
      expect(await AgentSessionForkModel.countDocuments({ orgId: large.org })).to.equal(0);
      const jobs = await AgentSessionErasureModel.find({ orgId: large.org }).lean();
      expect(jobs).to.have.length(budget.large);
      expect(jobs.every((j) => j.status === 'pending' && j.reason === 'retention' && j.by === null)).to.equal(true);

      const extra = large.commands.total - small.commands.total;
      console.log(
        `abandoned-forks commands: small(${String(budget.small)})=${String(small.commands.total)} large(${String(budget.large)})=${String(large.commands.total)}`,
        JSON.stringify(large.commands.by),
      );
      expect(extra, JSON.stringify(large.commands.by)).to.be.at.most(budget.extraCommands);
    });
  });
});
