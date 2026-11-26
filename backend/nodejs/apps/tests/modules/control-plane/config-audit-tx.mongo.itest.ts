/**
 * QA-4 on a MongoDB replica set with Redis as the configuration KV: every
 * admin config write (W1..W11, support/admin-write-cases.ts) through its real
 * route, the real store adapters, the replica-set transaction and the Mongo
 * audit chain. Each write is audited in its transaction (QA4-1); a failure
 * writing the audit (QA4-2) or after the change is staged (QA4-3) leaves
 * nothing; a commit lost after the effect is reported (QA4-4). The org's chain
 * verifies with run records and config records mixed (QA4-5) and holds no
 * secret (QA4-6). Needs MONGO_IT_URI (rs0) and REDIS_IT_URL.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createHash, randomUUID } from 'node:crypto';
import yaml from 'js-yaml';
import mongoose, { ClientSession, Model } from 'mongoose';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { StoreType } from '../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';
import { ChangeEvent, ChangeOutbox, ConfigAuditLog, ConfigChanged, ControlPlaneTransaction, CpTx, MongoControlPlaneTransaction, Work, WriteContext } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import {
  AgentAuditHeadModel,
  AgentAuditRecordModel,
} from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { DURABLE_TRANSACTION_OPTIONS, inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { auditLinksIdle, MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import {
  AgentScheduleCounterModel,
  AgentScheduleFiringModel,
  AgentScheduleModel,
} from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import { AuditVerifier } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { AuditRecord } from '../../../src/modules/agent-harness/domain/audit';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { testCipher } from '../agent-harness/support/data-protection';
import { position, workerEvent } from '../agent-harness/support/journal-fixtures';
import { ADMIN_USER_ID, AdminWriteWorld, McpHubUpstream, ProviderKv, startAdminWriteWorld } from './support/admin-write-world';
import { SECRETS, WRITE_CASES, WriteCase } from './support/admin-write-cases';
import { RecordingLog } from './support/in-memory';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';

registerHarnessModels([CpOrgHeadModel, AgentScheduleModel, AgentScheduleCounterModel, AgentScheduleFiringModel] as unknown as Array<
  Model<never>
>);

/** The run journal's fixtures write to org-1. */
const ORG = position().orgId;
const CIPHER = EncryptionService.withKey('aes-256-gcm', createHash('sha256').update('qa4-itest').digest('hex'));
const OUTBOX = 'qa4ChangeOutbox';
const TOKEN_PATTERNS = (
  JSON.parse(readFileSync(resolve(__dirname, '../../../../../../contracts/harness/vectors/token-patterns.json'), 'utf8')) as {
    patterns: Array<{ kind: string; source: string }>;
  }
).patterns.map(({ kind, source }) => ({ kind, pattern: new RegExp(source) }));

const OPENAPI = yaml.load(
  readFileSync(resolve(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8'),
) as { paths: Record<string, Record<string, { responses?: Record<string, unknown> }>> };

/** The statuses the API document declares for a write case's route (`PUT /api/v1/x/:id`). */
const declaredStatuses = (route: string): string[] => {
  const [method, path] = route.split(' ') as [string, string];
  const documented = path.replace(/^\/api\/v1/, '').replace(/:(\w+)/g, '{$1}');
  return Object.keys(OPENAPI.paths[documented]?.[method.toLowerCase()]?.responses ?? {});
};

type FaultPoint = 'audit' | 'outbox' | 'commit' | null;

/** Where the next write fails; each fault strikes after its own write reached the session or the store. */
class Faults {
  point: FaultPoint = null;
}

class AuditFailingAfterAppend implements ConfigAuditLog {
  constructor(
    private readonly inner: ConfigAuditLog,
    private readonly faults: Faults,
  ) {}

  async record(tx: CpTx, changed: ConfigChanged): Promise<void> {
    await this.inner.record(tx, changed);
    if (this.faults.point === 'audit') throw new Error('the audit store failed after the append');
  }
}

/** A change outbox that writes through the session, as CPD-6's will, and fails after its row on request. */
class SessionOutbox implements ChangeOutbox {
  constructor(private readonly faults: Faults) {}

  enqueue(tx: CpTx, event: ChangeEvent): Promise<void> {
    return tx.enlist(async () => {
      await mongoose.connection.collection(OUTBOX).insertOne({ ...event }, { session: tx.session as ClientSession });
      if (this.faults.point === 'outbox') throw new Error('the outbox failed after its row');
    });
  }

  committed(): Promise<void> {
    return Promise.resolve();
  }
}

/** Aborts the session once an effect reached its store, so the commit that follows fails. */
class CommitLostAfterEffect implements ControlPlaneTransaction {
  constructor(
    private readonly inner: ControlPlaneTransaction,
    private readonly faults: Faults,
  ) {}

  run<T>(ctx: WriteContext, work: Work<T>) {
    return this.inner.run(ctx, (tx) =>
      work({
        orgId: tx.orgId,
        headRevision: tx.headRevision,
        session: tx.session,
        enlist: (write) => tx.enlist(write),
        external: (effect) =>
          tx.external(async () => {
            const value = await effect();
            if (this.faults.point === 'commit') await (tx.session as ClientSession).abortTransaction();
            return value;
          }),
      }),
    );
  }
}

const redisKv = (): ProviderKv => {
  const raw = process.env.REDIS_IT_URL;
  if (!raw) throw new Error('REDIS_IT_URL must be set for the QA-4 itest');
  const url = new URL(raw);
  return new ProviderKv(
    KeyValueStoreFactory.createStore<string>(
      StoreType.Redis,
      { host: url.hostname, port: Number(url.port || 6379), keyPrefix: `pxh_qa4:${randomUUID()}:kv:` },
      (value) => Buffer.from(value),
      (buffer) => buffer.toString(),
    ),
  );
};

const chain = new MongoAuditChainStore();
const configRecords = async (orgId = ORG): Promise<AuditRecord[]> =>
  (await chain.read(orgId, 0, 10_000)).filter((record) => record.streamId === `cp:${orgId}`);
const orgHead = async (orgId = ORG) => (await CpOrgHeadModel.findById(orgId).lean())?.revision ?? 0;
const chainHead = async (orgId = ORG) => {
  await auditLinksIdle();
  return (await AgentAuditHeadModel.findOne({ orgId }).lean())?.seq ?? 0;
};

describe('QA-4 config writes and their audit on a MongoDB replica set', function () {
  this.timeout(120_000);
  const faults = new Faults();
  const log = new RecordingLog();
  const kv = redisKv();
  const upstream = new McpHubUpstream();
  let codingEngines = new InMemoryCodingEnginePreferences();
  let world: AdminWriteWorld;

  /**
   * Everything a config write can change: the KV, the schedules, what the connectors service accepted, and the
   * coding-engine choices on the admin's user document and the org document (ENGPICK).
   */
  const stored = async () =>
    JSON.stringify({
      kv: await kv.dump(),
      schedules: await AgentScheduleModel.find({ orgId: ORG }).sort({ _id: 1 }).lean(),
      upstream: upstream.received,
      codingEngines: await codingEngines.read(ORG, ADMIN_USER_ID),
    });

  const clearKv = async () => {
    for (const key of await kv.listKeysInDirectory('/')) await kv.delete(key);
  };

  before(connectHarnessMongo);
  before(async () => {
    world = await startAdminWriteWorld({
      orgId: ORG,
      kv,
      cipher: CIPHER,
      schedules: new MongoScheduleStore(),
      transaction: new CommitLostAfterEffect(
        new MongoControlPlaneTransaction({
          audit: new AuditFailingAfterAppend(new ChainConfigAuditLog(chain), faults),
          outbox: new SessionOutbox(faults),
          transaction: DURABLE_TRANSACTION_OPTIONS,
          log,
        }),
        faults,
      ),
      connectorBackend: await upstream.start(),
      codingEngines: {
        read: (orgId, userId) => codingEngines.read(orgId, userId),
        writeUser: (orgId, userId, choice) => codingEngines.writeUser(orgId, userId, choice),
        writeOrg: (orgId, engine) => codingEngines.writeOrg(orgId, engine),
        writeOrgAutoRoute: (orgId, on) => codingEngines.writeOrgAutoRoute(orgId, on),
        readSession: (orgId, sessionId) => codingEngines.readSession(orgId, sessionId),
        writeSession: (orgId, sessionId, choice) => codingEngines.writeSession(orgId, sessionId, choice),
        readSessionPin: (orgId, sessionId) => codingEngines.readSessionPin(orgId, sessionId),
        writeSessionPin: (orgId, sessionId, engine) => codingEngines.writeSessionPin(orgId, sessionId, engine),
        readSessionRepository: (orgId, sessionId) => codingEngines.readSessionRepository(orgId, sessionId),
        writeSessionRepository: (orgId, sessionId, repository) =>
          codingEngines.writeSessionRepository(orgId, sessionId, repository),
        eraseUser: (orgId, userId) => codingEngines.eraseUser(orgId, userId),
        eraseSession: (orgId, sessionId) => codingEngines.eraseSession(orgId, sessionId),
      },
    });
  });

  after(async () => {
    world?.close();
    upstream.close();
    await clearKv();
    await disconnectHarnessMongo();
  });

  beforeEach(async () => {
    faults.point = null;
    log.lines.length = 0;
    upstream.received.length = 0;
    codingEngines = new InMemoryCodingEnginePreferences();
    await resetHarnessMongo();
    await mongoose.connection.collection(OUTBOX).deleteMany({});
    await clearKv();
  });

  const prepared = async (writeCase: WriteCase) => {
    const target = (await writeCase.prepare?.(world)) ?? '';
    return {
      target,
      stored: await stored(),
      records: (await configRecords()).length,
      orgHead: await orgHead(),
      chainHead: await chainHead(),
      outbox: await mongoose.connection.collection(OUTBOX).countDocuments(),
    };
  };

  const send = (writeCase: WriteCase, target: string) => {
    const { method, path, body, ifMatch } = writeCase.request(target);
    return world.call(method, path, body, ifMatch);
  };

  describe('QA4-1 (AUD-11, IX-CST-AUD-3, LAB-16): each write is one config.changed at the next org revision', () => {
    for (const writeCase of WRITE_CASES) {
      it(writeCase.id, async () => {
        const before = await prepared(writeCase);
        const answer = await send(writeCase, before.target);
        expect(answer.status, JSON.stringify(answer.json)).to.be.within(200, 299);
        const written = (await configRecords()).slice(before.records);
        expect(written).to.have.length(1);
        const [scope, objectType, objectId] = writeCase.object(ORG, before.target, answer.json);
        expect(written[0]!.fields).to.include({ scope, objectType, objectId, actor: `user:${ADMIN_USER_ID}` });
        expect(written[0]!.journalSeq).to.equal(before.orgHead + 1);
        expect(await orgHead()).to.equal(before.orgHead + 1);
        expect(await stored()).to.not.equal(before.stored);
      });
    }
  });

  const refusedWithNothingChanged = async (writeCase: WriteCase, point: Exclude<FaultPoint, null | 'commit'>) => {
    const before = await prepared(writeCase);
    faults.point = point;
    const answer = await send(writeCase, before.target);
    expect([answer.status, answer.json?.error?.code]).to.deep.equal([503, 'HTTP_CP_UNAVAILABLE']);
    expect(declaredStatuses(writeCase.route), 'the API document does not declare the 503').to.include('503');
    expect(await stored(), 'the change reached its store').to.equal(before.stored);
    expect((await configRecords()).length).to.equal(before.records);
    expect(await chainHead()).to.equal(before.chainHead);
    expect(await orgHead()).to.equal(before.orgHead);
    expect(await mongoose.connection.collection(OUTBOX).countDocuments()).to.equal(before.outbox);
  };

  describe('QA4-2 (AUD-12, CST-03, IX-CST-AUD-1): an audit append that fails after reaching the session leaves nothing', () => {
    for (const writeCase of WRITE_CASES) {
      it(writeCase.id, () => refusedWithNothingChanged(writeCase, 'audit'));
    }
  });

  describe('QA4-3 (CST-02): a failure after the audit entry and the change are staged leaves nothing', () => {
    for (const writeCase of WRITE_CASES) {
      it(writeCase.id, () => refusedWithNothingChanged(writeCase, 'outbox'));
    }
  });

  describe('QA4-4 (TX-8): a commit lost after the effect is 503, unaudited and reported once', () => {
    for (const writeCase of WRITE_CASES) {
      it(writeCase.id, async () => {
        const before = await prepared(writeCase);
        faults.point = 'commit';
        const answer = await send(writeCase, before.target);
        expect([answer.status, answer.json?.error?.code]).to.deep.equal([503, 'HTTP_CP_UNAVAILABLE']);
        expect((await configRecords()).length).to.equal(before.records);
        expect(await orgHead()).to.equal(before.orgHead);
        const reported = log.lines.filter(([level]) => level === 'error');
        expect(reported.map(([, message]) => message)).to.deep.equal(['cp.change.unaudited']);
        const [, objectType, objectId] = writeCase.object(ORG, before.target, null);
        expect(reported[0]![2]).to.include({ orgId: ORG, objectType, headRevision: before.orgHead + 1 });
        if (!objectId.includes('undefined')) expect(reported[0]![2]).to.include({ objectId });
        // No store here joins the Mongo transaction yet (CPD-1 deviation 2; QA4-F3): the change stays applied.
        expect(await stored()).to.not.equal(before.stored);
      });
    }
  });

  it('QA4-5 (AUD-14, AUD-15, IX-CST-AUD-2, IX-APR-AUD-1): run and config records in one chain verify; a changed config record is caught', async () => {
    const journal = new AuditedJournalStore(new SealedJournalStore(new MongoJournalStore(), testCipher()), chain);
    const service = new JournalService(new MongoPayloadStore(), testCipher());
    const append = async (runId: string, type: string, data: Record<string, unknown>) => {
      const prepared = await service.prepare({ ...position({ runId }), writer: 'node', events: [workerEvent(type, data)] });
      await inMongoTransaction((session) => journal.append(prepared, session));
    };
    await append('run-1', 'run.admitted', { runKind: 'interactive', principalRef: 'p_user-1', via: 'session' });
    // Writes that need no prepared target: an index into WRITE_CASES moves whenever a case is added or removed.
    const unprepared = WRITE_CASES.filter((writeCase) => writeCase.prepare === undefined);
    for (const writeCase of unprepared.slice(0, 3)) {
      expect((await send(writeCase, '')).status).to.equal(200);
    }
    await append('run-1', 'interaction.granted', { interactionId: 'int-9', scope: 'conversation' });
    expect((await send(unprepared[3]!, '')).status).to.be.within(200, 299);

    const records = await chain.read(ORG, 0, 100);
    expect(records.map((record) => record.kind)).to.deep.equal([
      'run.admitted',
      'config.changed',
      'config.changed',
      'config.changed',
      'interaction.granted',
      'config.changed',
    ]);
    expect(records[4]!.fields).to.deep.equal({ interactionId: 'int-9', scope: 'conversation' });
    const verifier = new AuditVerifier(chain, new MongoAuditAnchorSink());
    expect(await verifier.verify(ORG)).to.deep.include({ ok: true, records: 6 });

    await AgentAuditRecordModel.collection.updateOne({ orgId: ORG, seq: 3 }, { $set: { 'fields.actor': 'user:someone-else' } });
    expect((await verifier.verify(ORG)).problems.map((problem) => problem.kind)).to.deep.equal(['tampered']);
  });

  it('QA4-6 (AUD-13, IX-CST-AUD-4): after every write, keys of every shape included, no record holds a secret', async () => {
    for (const writeCase of WRITE_CASES) {
      const target = (await writeCase.prepare?.(world)) ?? '';
      expect((await send(writeCase, target)).status, writeCase.id).to.be.within(200, 299);
    }
    const text = JSON.stringify(await chain.read(ORG, 0, 10_000));
    expect(text).to.include('config.changed');
    for (const secret of Object.values(SECRETS)) expect(text).to.not.include(secret);
    for (const { kind, pattern } of TOKEN_PATTERNS) expect(pattern.test(text), kind).to.equal(false);
  });
});
