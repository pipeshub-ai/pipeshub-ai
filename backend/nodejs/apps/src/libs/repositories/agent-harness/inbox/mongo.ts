import {
  MAX_MUTED_SCHEDULES,
  type InboxPreferences,
} from '../../../../modules/agent-harness/domain/inbox';
import type { InboxStore } from '../../../../modules/agent-harness/ports/inbox';
import {
  pauseFields,
  preferencesOf,
  type StoredInboxPreferences,
} from './records';
import {
  InboxPreferencesModel,
  RunEndNoticeClaimModel,
  RunWatchLeaseModel,
} from './schema/inbox.schema';

const id = (orgId: string, part: string): string => `${orgId}:${part}`;
const DUPLICATE_KEY = 11000;

const isDuplicate = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === DUPLICATE_KEY;

/** `InboxStore` on MongoDB: three collections, every change one atomic update. */
export class MongoInboxStore implements InboxStore {
  async preferences(orgId: string, userId: string): Promise<InboxPreferences> {
    const doc = await InboxPreferencesModel.findById(
      id(orgId, userId),
    ).lean<StoredInboxPreferences>();
    return preferencesOf(doc);
  }

  async setMuted(
    orgId: string,
    userId: string,
    scheduleId: string,
    muted: boolean,
    now: number,
  ): Promise<InboxPreferences> {
    const others = {
      $filter: {
        input: { $ifNull: ['$mutedSchedules', []] },
        cond: { $ne: ['$$this', { $literal: scheduleId }] },
      },
    };
    return this.update(orgId, userId, now, [
      {
        $set: {
          mutedSchedules: muted
            ? {
                $slice: [
                  { $concatArrays: [others, [{ $literal: scheduleId }]] },
                  -MAX_MUTED_SCHEDULES,
                ],
              }
            : others,
        },
      },
    ]);
  }

  setPause(
    orgId: string,
    userId: string,
    pause: InboxPreferences['pause'],
    now: number,
  ): Promise<InboxPreferences> {
    return this.update(orgId, userId, now, [{ $set: pauseFields(pause) }]);
  }

  setRunNotices(
    orgId: string,
    userId: string,
    channels: Partial<InboxPreferences['runNotices']>,
    now: number,
  ): Promise<InboxPreferences> {
    return this.update(orgId, userId, now, [
      {
        $set: {
          ...(channels.email !== undefined
            ? { runNoticeEmail: channels.email }
            : {}),
          ...(channels.slack !== undefined
            ? { runNoticeSlack: channels.slack }
            : {}),
        },
      },
    ]);
  }

  async claimRunNotice(
    orgId: string,
    runId: string,
    userId: string,
    now: number,
  ): Promise<boolean> {
    try {
      await RunEndNoticeClaimModel.create({
        _id: id(orgId, runId),
        orgId,
        runId,
        userId,
        createdAt: new Date(now),
      });
      return true;
    } catch (error) {
      if (isDuplicate(error)) return false;
      throw error;
    }
  }

  async watch(lease: {
    orgId: string;
    userId: string;
    sessionId: string;
    watcherId: string;
    until: number;
  }): Promise<void> {
    await RunWatchLeaseModel.updateOne(
      { _id: id(lease.orgId, lease.watcherId) },
      {
        $set: {
          orgId: lease.orgId,
          userId: lease.userId,
          sessionId: lease.sessionId,
          until: lease.until,
          expiresAt: new Date(lease.until),
        },
      },
      { upsert: true },
    );
  }

  async unwatch(orgId: string, watcherId: string): Promise<void> {
    await RunWatchLeaseModel.deleteOne({ _id: id(orgId, watcherId), orgId });
  }

  async watching(
    orgId: string,
    userId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean> {
    return (
      (await RunWatchLeaseModel.exists({
        orgId,
        userId,
        sessionId,
        until: { $gt: at },
      })) !== null
    );
  }

  async eraseUser(orgId: string, userId: string): Promise<void> {
    await Promise.all([
      InboxPreferencesModel.deleteOne({ _id: id(orgId, userId), orgId }),
      RunEndNoticeClaimModel.deleteMany({ orgId, userId }),
      RunWatchLeaseModel.deleteMany({ orgId, userId }),
    ]);
  }

  async eraseOrg(orgId: string): Promise<void> {
    await Promise.all([
      InboxPreferencesModel.deleteMany({ orgId }),
      RunEndNoticeClaimModel.deleteMany({ orgId }),
      RunWatchLeaseModel.deleteMany({ orgId }),
    ]);
  }

  private async update(
    orgId: string,
    userId: string,
    now: number,
    pipeline: Array<Record<string, unknown>>,
  ): Promise<InboxPreferences> {
    const doc = await InboxPreferencesModel.findOneAndUpdate(
      { _id: id(orgId, userId) },
      [
        {
          $set: {
            orgId,
            userId,
            mutedSchedules: { $ifNull: ['$mutedSchedules', []] },
            pausedUntil: { $ifNull: ['$pausedUntil', null] },
            pausedUntilResumed: { $ifNull: ['$pausedUntilResumed', false] },
            runNoticeEmail: { $ifNull: ['$runNoticeEmail', false] },
            runNoticeSlack: { $ifNull: ['$runNoticeSlack', false] },
            updatedAt: now,
          },
        },
        ...pipeline,
      ],
      { upsert: true, returnDocument: 'after', updatePipeline: true },
    ).lean<StoredInboxPreferences>();
    return preferencesOf(doc);
  }
}
