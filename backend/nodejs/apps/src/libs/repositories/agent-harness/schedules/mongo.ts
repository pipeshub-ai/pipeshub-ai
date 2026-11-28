import { idOperator } from '../../id-filter-guard/mongo';
import { HarnessScheduleRefusedError } from '../../../../modules/agent-harness/domain/errors';
import {
  type AgentSchedule,
  type AgentVersionPin,
  type EventTriggerKind,
  type FiringOutcome,
  type RunTriggerContext,
  type ScheduleFiring,
  scheduleApprovalUsed,
  SCHEDULE_TOMBSTONE_STATUS,
  type ScheduleStatus,
  WATCH_GRACE_MS,
  settledRun,
  leavesTombstone,
  scheduleChanged,
  type ScheduleChangedFrom,
  type ScheduleRunnerKind,
} from '../../../../modules/agent-harness/domain/schedules';
import type {
  ScheduleEraser,
  ScheduleChange,
  ScheduleLimits,
  ScheduleStore,
} from '../../../../modules/agent-harness/ports/schedules';
import {
  RunInputSeal,
  UNSEALED_RUN_INPUT,
} from '../../../../modules/agent-harness/application/run-input-seal';
import {
  inMongoTransaction,
  MAJORITY_WRITE,
  MAJORITY_CREATE,
} from '../durable-transaction/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';
import {
  AgentScheduleCounterModel,
  AgentScheduleFiringModel,
  AgentScheduleModel,
  ScheduleDocument,
  ScheduleFiringDocument,
} from './schema/schedules.schema';
import type { ScheduleTombstoneDocument } from './document';

/** A schedule at `revision`; one made before revisions holds none and is at revision 0. */
const atRevision = (revision: number): Record<string, unknown> =>
  revision === 0
    ? { $or: [{ revision: { $exists: false } }, { revision: 0 }] }
    : { revision };

/** Every read of schedules leaves out the tombstones deleted chat-made schedules leave (ADR-SCH-01 D3). */
const LIVE = { status: { $ne: SCHEDULE_TOMBSTONE_STATUS } } as const;

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  'code' in error &&
  (error as { code?: unknown }).code === 11000;

/** The part a signed webhook's key is sealed under. */
const SIGNING_KEY_PART = 'webhookSigningKey';

export async function toSchedule(
  seal: RunInputSeal,
  document: ScheduleDocument,
): Promise<AgentSchedule> {
  const {
    _id,
    agentKey,
    maxCostUsd,
    input,
    webhookSigningKey,
    overlap,
    notify,
    ...rest
  } = structuredClone(document);
  return {
    ...rest,
    // Reads never answer a tombstone (`deleted`), so what is read is a schedule's own status.
    status: rest.status as ScheduleStatus,
    input:
      input === null
        ? null
        : await seal.openInput(rest.orgId, { scheduleId: _id }, input),
    id: _id,
    ...(agentKey !== undefined ? { agentKey } : {}),
    ...(maxCostUsd !== undefined ? { maxCostUsd } : {}),
    ...(overlap !== undefined ? { overlap } : {}),
    ...(notify !== undefined ? { notify } : {}),
    ...(webhookSigningKey !== undefined
      ? {
          webhookSigningKey:
            webhookSigningKey === null
              ? null
              : await seal.openSecret(
                  rest.orgId,
                  { scheduleId: _id },
                  SIGNING_KEY_PART,
                  webhookSigningKey,
                ),
        }
      : {}),
  };
}

export function toFiring(document: ScheduleFiringDocument): ScheduleFiring {
  const { _id, ...rest } = structuredClone(document);
  // The watch's round-robin mark is the store's own bookkeeping.
  delete rest.watchCheckedAt;
  return { ...rest, id: _id };
}

export async function scheduleDocument(
  seal: RunInputSeal,
  schedule: AgentSchedule,
): Promise<ScheduleDocument> {
  const { id, input, webhookSigningKey, ...rest } = structuredClone(schedule);
  return {
    ...rest,
    input:
      input === null
        ? null
        : ((await seal.sealInput(
            rest.orgId,
            { scheduleId: id },
            input,
          )) as ScheduleDocument['input']),
    ...(webhookSigningKey !== undefined
      ? {
          webhookSigningKey:
            webhookSigningKey === null
              ? null
              : await seal.sealSecret(
                  rest.orgId,
                  { scheduleId: id },
                  SIGNING_KEY_PART,
                  webhookSigningKey,
                ),
        }
      : {}),
    _id: id,
  };
}

export function firingDocument(firing: ScheduleFiring): ScheduleFiringDocument {
  const { id, ...rest } = structuredClone(firing);
  return { ...rest, _id: id };
}

/**
 * Schedules over Mongo (P8a). A cron slot is claimed in one transaction that
 * moves the schedule's cursor by compare-and-set and inserts the firing under
 * its unique slot key: of two schedulers, one commits and the other's
 * transaction finds the cursor moved (or hits the key) and writes nothing.
 */
export class MongoScheduleStore implements ScheduleStore, ScheduleEraser {
  /** `inputs` seals the template's query, attachments, filters and caller (GAPS-1); plaintext when omitted. */
  constructor(
    private readonly time: MongoTime = mongoServerTime,
    private readonly inputs: RunInputSeal = UNSEALED_RUN_INPUT,
  ) {}

  async now(): Promise<number> {
    return this.time.read();
  }

  async create(
    schedule: AgentSchedule,
    limits: ScheduleLimits,
  ): Promise<AgentSchedule> {
    const sealed = await scheduleDocument(this.inputs, schedule);
    await this.inserted(schedule, () => this.insert(sealed, schedule, limits));
    return structuredClone(schedule);
  }

  /** A duplicate id that is a tombstone: the approval that would make it again is spent. */
  private async inserted(
    schedule: AgentSchedule,
    insert: () => Promise<void>,
  ): Promise<void> {
    try {
      await insert();
    } catch (error) {
      if (
        isDuplicateKey(error) &&
        (await AgentScheduleModel.exists({
          _id: schedule.id,
          orgId: schedule.orgId,
          status: SCHEDULE_TOMBSTONE_STATUS,
        }).read('primary')) !== null
      ) {
        throw scheduleApprovalUsed();
      }
      throw error;
    }
  }

  private async insert(
    sealed: ScheduleDocument,
    schedule: AgentSchedule,
    limits: ScheduleLimits,
  ): Promise<void> {
    await inMongoTransaction(async (session) => {
      // One after the other: the first operation of a transaction starts it, and two sent at once (driver 7)
      // both try to, which the server refuses.
      const org = await AgentScheduleModel.countDocuments({
        orgId: schedule.orgId,
        ...LIVE,
      }).session(session);
      const actor = await AgentScheduleModel.countDocuments({
        orgId: schedule.orgId,
        actorId: schedule.actorId,
        ...LIVE,
      }).session(session);
      if (org >= limits.perOrg || actor >= limits.perActor) {
        throw new HarnessScheduleRefusedError(
          'SCHEDULE_LIMIT',
          'No more schedules may be created',
        );
      }
      // Counts are snapshot reads: two creates could both see room (write
      // skew). Writing what each counted to one shared document makes the
      // later of them conflict, abort and count again (a compare-and-set on
      // the counter's version, retried by the transaction).
      for (const [key, counted] of [
        [`org:${schedule.orgId}`, org],
        [`actor:${schedule.orgId}:${schedule.actorId}`, actor],
      ] as const) {
        await AgentScheduleCounterModel.updateOne(
          { _id: key },
          {
            $set: { count: counted + 1, updatedAt: schedule.createdAt },
            $setOnInsert: { orgId: schedule.orgId },
          },
          { upsert: true, session },
        );
      }
      await AgentScheduleModel.create([sealed], {
        session,
      });
    });
  }

  async get(orgId: string | null, id: string): Promise<AgentSchedule | null> {
    const found = await AgentScheduleModel.findOne({
      _id: id,
      ...(orgId !== null ? { orgId } : {}),
      ...LIVE,
    })
      .read('primary')
      .lean<ScheduleDocument>();
    return found === null ? null : toSchedule(this.inputs, found);
  }

  async list(
    orgId: string,
    filter: { actorId?: string },
    limit: number,
  ): Promise<AgentSchedule[]> {
    const found = await AgentScheduleModel.find({
      orgId,
      ...(filter.actorId !== undefined ? { actorId: filter.actorId } : {}),
      ...LIVE,
    })
      .sort({ createdAt: -1 })
      .limit(limit)
      .lean<ScheduleDocument[]>();
    return Promise.all(found.map((d) => toSchedule(this.inputs, d)));
  }

  async setStatus(
    orgId: string,
    id: string,
    change: {
      from: readonly ScheduleStatus[];
      to: ScheduleStatus;
      reason: string | null;
      nextFireAt: number | null;
      lastChangedFrom?: ScheduleChangedFrom;
      expectedRevision?: number;
    },
    now: number,
  ): Promise<AgentSchedule | null> {
    const updated = await AgentScheduleModel.findOneAndUpdate(
      {
        _id: id,
        orgId,
        status: { $in: [...change.from] },
        ...(change.expectedRevision !== undefined
          ? atRevision(change.expectedRevision)
          : {}),
      },
      {
        $set: {
          status: change.to,
          statusReason: change.reason,
          nextFireAt: change.nextFireAt,
          ...(change.lastChangedFrom !== undefined
            ? { lastChangedFrom: change.lastChangedFrom }
            : {}),
          updatedAt: now,
        },
        $inc: { revision: 1 },
      },
      { returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean<ScheduleDocument>();
    if (updated !== null) return toSchedule(this.inputs, updated);
    if (change.expectedRevision !== undefined) {
      await this.assertAtRevision(orgId, id, change.expectedRevision);
    }
    return null;
  }

  /** Refuses `SCHEDULE_CHANGED` when the live schedule is at another revision; nothing when it is gone. */
  private async assertAtRevision(
    orgId: string,
    id: string,
    expectedRevision: number,
  ): Promise<void> {
    const live = await AgentScheduleModel.findOne(
      { _id: id, orgId, ...LIVE },
      { revision: 1 },
    ).lean<Pick<ScheduleDocument, '_id' | 'revision'>>();
    if (live !== null && (live.revision ?? 0) !== expectedRevision) {
      throw scheduleChanged();
    }
  }

  async setAgentVersion(
    orgId: string,
    id: string,
    agentVersion: AgentVersionPin,
    now: number,
  ): Promise<AgentSchedule | null> {
    const updated = await AgentScheduleModel.findOneAndUpdate(
      {
        _id: id,
        orgId,
        agentKey: idOperator({ $exists: true }),
        status: { $ne: 'disabled' },
      },
      { $set: { agentVersion, updatedAt: now }, $inc: { revision: 1 } },
      { returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean<ScheduleDocument>();
    return updated === null ? null : toSchedule(this.inputs, updated);
  }

  async update(
    orgId: string,
    id: string,
    expectedRevision: number,
    change: ScheduleChange,
    now: number,
  ): Promise<AgentSchedule | 'changed' | null> {
    const { input, skipNextAt, ...fields } = change;
    if (
      typeof skipNextAt === 'number' &&
      change.nextFireAt !== undefined &&
      change.nextFireAt !== skipNextAt
    ) {
      return (await this.get(orgId, id)) === null ? null : 'changed';
    }
    const set: Record<string, unknown> = { ...fields, updatedAt: now };
    if (input !== undefined) {
      set['input'] = await this.inputs.sealInput(
        orgId,
        { scheduleId: id },
        input,
      );
    }
    if (skipNextAt !== undefined && skipNextAt !== null) {
      set['skipNextAt'] = skipNextAt;
    }
    const updated = await AgentScheduleModel.findOneAndUpdate(
      {
        _id: id,
        orgId,
        ...LIVE,
        ...atRevision(expectedRevision),
        // A skip marks the slot the cursor is on: a firing that moved it since the read makes this a stale skip.
        ...(typeof skipNextAt === 'number' && change.nextFireAt === undefined
          ? { status: 'active', nextFireAt: skipNextAt }
          : {}),
      },
      {
        $set: set,
        ...(skipNextAt === null ? { $unset: { skipNextAt: '' } } : {}),
        $inc: { revision: 1 },
      },
      { returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean<ScheduleDocument>();
    if (updated !== null) return toSchedule(this.inputs, updated);
    return (await this.get(orgId, id)) === null ? null : 'changed';
  }

  /** A chat-made schedule leaves its tombstone in the same write (ADR-SCH-01 D3); any other goes. */
  async delete(
    orgId: string,
    id: string,
    expectedRevision?: number,
  ): Promise<boolean> {
    return inMongoTransaction(async (session) => {
      const found = await AgentScheduleModel.findOne(
        { _id: id, orgId, ...LIVE },
        { actorId: 1, origin: 1, revision: 1 },
      )
        .session(session)
        .lean<
          Pick<ScheduleDocument, '_id' | 'actorId' | 'origin' | 'revision'>
        >();
      if (found === null) return false;
      // Read in the transaction: an edit committed after this read fails its write, and the retry reads it.
      if (
        expectedRevision !== undefined &&
        (found.revision ?? 0) !== expectedRevision
      ) {
        throw scheduleChanged();
      }
      if (found.origin !== undefined && leavesTombstone(found)) {
        const now = await this.time.read();
        const tombstone: ScheduleTombstoneDocument = {
          _id: id,
          orgId,
          actorId: found.actorId,
          status: SCHEDULE_TOMBSTONE_STATUS,
          origin: found.origin,
          input: null,
          deletedAt: now,
        };
        // A replace runs no validators: the tombstone keeps none of a schedule's required content.
        await AgentScheduleModel.replaceOne({ _id: id, orgId }, tombstone, {
          session,
        });
      } else {
        await AgentScheduleModel.deleteOne({ _id: id, orgId }, { session });
      }
      await AgentScheduleFiringModel.deleteMany(
        { orgId, scheduleId: id },
        { session },
      );
      return true;
    });
  }

  async dueCron(now: number, limit: number): Promise<AgentSchedule[]> {
    const found = await AgentScheduleModel.find({
      status: 'active',
      nextFireAt: { $lte: now },
    })
      .sort({ nextFireAt: 1 })
      .limit(limit)
      .read('primary')
      .lean<ScheduleDocument[]>();
    return Promise.all(found.map((d) => toSchedule(this.inputs, d)));
  }

  async claimCronSlot(
    firing: ScheduleFiring,
    expectedNextFireAt: number,
    nextFireAt: number | null,
    disable?: string,
    expectedRevision?: number,
  ): Promise<ScheduleFiring | null> {
    try {
      return await inMongoTransaction(async (session) => {
        const moved = await AgentScheduleModel.updateOne(
          {
            _id: firing.scheduleId,
            orgId: firing.orgId,
            status: 'active',
            nextFireAt: expectedNextFireAt,
            ...(expectedRevision !== undefined
              ? atRevision(expectedRevision)
              : {}),
            // A slot its owner asked to skip after the firer read it is never claimed to run (ADR-SCH-03 D4).
            ...(firing.status === 'pending'
              ? { skipNextAt: { $ne: firing.slotAt } }
              : {}),
          },
          {
            $set: {
              nextFireAt,
              ...(disable !== undefined
                ? { status: 'disabled', statusReason: disable }
                : {}),
              updatedAt: firing.createdAt,
            },
            // Disabling it is a change an edit must see: one read before the claim is refused SCHEDULE_CHANGED.
            ...(disable !== undefined ? { $inc: { revision: 1 } } : {}),
          },
          { session },
        );
        if (moved.modifiedCount !== 1) return null;
        await AgentScheduleFiringModel.create([firingDocument(firing)], {
          session,
        });
        return structuredClone(firing);
      });
    } catch (error) {
      if (isDuplicateKey(error)) return null;
      throw error;
    }
  }

  async claimSlot(
    firing: ScheduleFiring,
  ): Promise<{ firing: ScheduleFiring; created: boolean }> {
    try {
      await AgentScheduleFiringModel.create(
        [firingDocument(firing)],
        MAJORITY_CREATE,
      );
      return { firing: structuredClone(firing), created: true };
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
    }
    const existing = await AgentScheduleFiringModel.findOne({
      orgId: firing.orgId,
      scheduleId: firing.scheduleId,
      slotKey: firing.slotKey,
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    if (existing === null) {
      throw new Error('A claimed slot has no firing');
    }
    return { firing: toFiring(existing), created: false };
  }

  async eventSchedules(
    orgId: string,
    kind: EventTriggerKind,
    connectorId?: string,
  ): Promise<AgentSchedule[]> {
    const found = await AgentScheduleModel.find({
      'trigger.kind': kind,
      status: 'active',
      orgId,
      ...(kind === 'connector' ? { 'trigger.connectorId': connectorId } : {}),
    }).lean<ScheduleDocument[]>();
    return Promise.all(found.map((d) => toSchedule(this.inputs, d)));
  }

  async hasActive(kind: EventTriggerKind): Promise<boolean> {
    return (
      (await AgentScheduleModel.exists({
        'trigger.kind': kind,
        status: 'active',
      })) !== null
    );
  }

  async settle(
    orgId: string,
    firingId: string,
    outcome: FiringOutcome,
    now: number,
  ): Promise<boolean> {
    const admitted = outcome.status === 'admitted';
    const result = await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId, status: 'pending' },
      {
        $set: {
          status: outcome.status,
          runId: admitted ? outcome.runId : null,
          sessionId: admitted ? outcome.sessionId : null,
          reason: admitted ? null : outcome.reason,
          watchUntil: admitted ? outcome.watchUntil : null,
          settledAt: now,
          ...settledRun(outcome),
        },
      },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }

  async release(
    orgId: string,
    firingId: string,
    attempts: number,
    leaseUntil: number,
  ): Promise<void> {
    await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId, status: 'pending', attempts },
      { $set: { leaseUntil } },
      MAJORITY_WRITE,
    );
  }

  async takeStale(
    now: number,
    leaseMs: number,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    const candidates = await AgentScheduleFiringModel.find({
      status: 'pending',
      leaseUntil: { $lte: now },
    })
      .sort({ leaseUntil: 1 })
      .limit(limit)
      .read('primary')
      .lean<ScheduleFiringDocument[]>();
    const taken: ScheduleFiring[] = [];
    for (const candidate of candidates) {
      // Another scheduler may take the same one: only the update that still
      // sees its attempt count wins it.
      const won = await AgentScheduleFiringModel.findOneAndUpdate(
        {
          _id: candidate._id,
          status: 'pending',
          attempts: candidate.attempts,
          leaseUntil: { $lte: now },
        },
        { $inc: { attempts: 1 }, $set: { leaseUntil: now + leaseMs } },
        { returnDocument: 'after', ...MAJORITY_WRITE },
      ).lean<ScheduleFiringDocument>();
      if (won !== null) taken.push(toFiring(won));
    }
    return taken;
  }

  async firings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    const found = await AgentScheduleFiringModel.find({ orgId, scheduleId })
      .sort({ slotAt: -1 })
      .limit(limit)
      .lean<ScheduleFiringDocument[]>();
    return found.map(toFiring);
  }

  async getFiring(
    orgId: string,
    firingId: string,
  ): Promise<ScheduleFiring | null> {
    const found = await AgentScheduleFiringModel.findOne({
      _id: firingId,
      orgId,
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    return found === null ? null : toFiring(found);
  }

  async firingBySlot(
    orgId: string,
    scheduleId: string,
    slotKey: string,
  ): Promise<ScheduleFiring | null> {
    const found = await AgentScheduleFiringModel.findOne({
      orgId,
      scheduleId,
      slotKey,
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    return found === null ? null : toFiring(found);
  }

  async watched(now: number, limit: number): Promise<ScheduleFiring[]> {
    const found = await AgentScheduleFiringModel.find({
      status: 'admitted',
      watchUntil: { $gt: now - WATCH_GRACE_MS },
    })
      .sort({ watchCheckedAt: 1 })
      .limit(limit)
      .lean<ScheduleFiringDocument[]>();
    return found.map(toFiring);
  }

  async markChecked(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<void> {
    await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId },
      { $set: { watchCheckedAt: now } },
    );
  }

  async markNotified(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<boolean> {
    const result = await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId, notifiedAt: null },
      { $set: { notifiedAt: now } },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }

  async unwatch(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<boolean> {
    const result = await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId, watchUntil: { $ne: null } },
      { $set: { watchUntil: null, watchEndedAt: now } },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }

  async activeFirings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    const found = await AgentScheduleFiringModel.find({
      orgId,
      scheduleId,
      status: 'admitted',
      watchUntil: { $ne: null },
    })
      .sort({ slotAt: -1 })
      .limit(limit)
      .read('primary')
      .lean<ScheduleFiringDocument[]>();
    return found.map(toFiring);
  }

  async queuedFiring(
    orgId: string,
    scheduleId: string,
  ): Promise<ScheduleFiring | null> {
    const found = await AgentScheduleFiringModel.findOne({
      orgId,
      scheduleId,
      status: 'pending',
      queuedBehind: { $exists: true },
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    return found === null ? null : toFiring(found);
  }

  async requeue(
    orgId: string,
    firingId: string,
    attempts: number,
    runId: string,
    leaseUntil: number,
  ): Promise<boolean> {
    const result = await AgentScheduleFiringModel.updateOne(
      { _id: firingId, orgId, status: 'pending', attempts },
      {
        $set: { queuedBehind: runId, leaseUntil, reason: 'overlap_queued' },
        $inc: { attempts: attempts > 1 ? -1 : 0 },
      },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }

  async fixContext(
    orgId: string,
    firingId: string,
    context: RunTriggerContext,
  ): Promise<RunTriggerContext> {
    const fixed = await AgentScheduleFiringModel.findOneAndUpdate(
      { _id: firingId, orgId, context: { $exists: false } },
      { $set: { context } },
      { returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean<ScheduleFiringDocument>();
    if (fixed?.context !== undefined) return fixed.context;
    const existing = await AgentScheduleFiringModel.findOne({
      _id: firingId,
      orgId,
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    if (existing?.context === undefined) {
      throw new Error('A firing has no trigger context');
    }
    return existing.context;
  }

  async fixRunner(
    orgId: string,
    firingId: string,
    runner: ScheduleRunnerKind,
    from?: ScheduleRunnerKind,
  ): Promise<ScheduleRunnerKind | null> {
    const unset = { runner: { $exists: false } };
    await AgentScheduleFiringModel.updateOne(
      {
        _id: firingId,
        orgId,
        ...(from !== undefined ? { $or: [unset, { runner: from }] } : unset),
      },
      { $set: { runner } },
      MAJORITY_WRITE,
    );
    const found = await AgentScheduleFiringModel.findOne({
      _id: firingId,
      orgId,
    })
      .read('primary')
      .lean<ScheduleFiringDocument>();
    return found === null ? null : (found.runner ?? null);
  }

  async countDelivery(
    orgId: string,
    scheduleId: string,
    windowStart: number,
    expiresAt: number,
  ): Promise<number> {
    const counted = await AgentScheduleCounterModel.findOneAndUpdate(
      { _id: `hits:${orgId}:${scheduleId}:${String(windowStart)}` },
      {
        $inc: { count: 1 },
        $set: { updatedAt: windowStart, expiresAt: new Date(expiresAt) },
        $setOnInsert: { orgId },
      },
      { upsert: true, returnDocument: 'after' },
    ).lean<{ count: number }>();
    // An upsert answering the new document always has one.
    return counted.count;
  }

  async firstDelivery(
    orgId: string,
    scheduleId: string,
    nonce: string,
    at: number,
    expiresAt: number,
  ): Promise<boolean> {
    const seen = await AgentScheduleCounterModel.findOneAndUpdate(
      { _id: `seen:${orgId}:${scheduleId}:${nonce}` },
      {
        $inc: { count: 1 },
        $set: { updatedAt: at, expiresAt: new Date(expiresAt) },
        $setOnInsert: { orgId },
      },
      { upsert: true, returnDocument: 'after' },
    ).lean<{ count: number }>();
    return seen.count === 1;
  }

  /** A deleted user's schedules go entirely (CLEANUP-G2): an account restored later starts with none. */
  async eraseUser(orgId: string, userId: string): Promise<number> {
    return this.remove({ orgId, actorId: userId }, [
      `actor:${orgId}:${userId}`,
    ]);
  }

  async eraseOrg(orgId: string): Promise<number> {
    return this.remove({ orgId }, null);
  }

  /** The schedules, their firings and the counters that counted them; `counters` null: every one of the org. */
  private async remove(
    filter: Record<string, string>,
    counters: string[] | null,
  ): Promise<number> {
    const orgId = filter['orgId'] as string;
    return inMongoTransaction(async (session) => {
      const ids = (
        await AgentScheduleModel.find(filter, { _id: 1 })
          .session(session)
          .lean()
      ).map((schedule) => schedule._id);
      const removed = await AgentScheduleModel.deleteMany(
        { _id: { $in: ids }, orgId },
        { session },
      );
      await AgentScheduleFiringModel.deleteMany(
        { orgId, scheduleId: { $in: ids } },
        { session },
      );
      await AgentScheduleCounterModel.deleteMany(
        counters === null ? { orgId } : { _id: { $in: counters }, orgId },
        { session },
      );
      return removed.deletedCount;
    });
  }

  async eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return this.disable({ orgId, agentKey }, 'agent_deleted');
  }

  async pauseUser(
    orgId: string,
    userId: string,
    reason: string,
  ): Promise<number> {
    const now = await this.time.read();
    const result = await AgentScheduleModel.updateMany(
      { orgId, actorId: userId, status: 'active' },
      {
        $set: {
          status: 'paused',
          statusReason: reason,
          nextFireAt: null,
          updatedAt: now,
        },
        // A change an edit must see: one read before it is refused SCHEDULE_CHANGED, never written back over it.
        $inc: { revision: 1 },
      },
    );
    return result.modifiedCount;
  }

  private async disable(
    filter: Record<string, string>,
    reason: string,
  ): Promise<number> {
    const now = await this.time.read();
    const result = await AgentScheduleModel.updateMany(filter, {
      $set: {
        status: 'disabled',
        statusReason: reason,
        input: null,
        nextFireAt: null,
        webhookTokenHash: null,
        webhookSigningKey: null,
        updatedAt: now,
      },
      // A change an edit must see: an edit read before it cannot write the erased content back.
      $inc: { revision: 1 },
    });
    return result.modifiedCount;
  }
}
