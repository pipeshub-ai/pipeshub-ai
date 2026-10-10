import { randomUUID } from 'crypto';
import { hostname } from 'os';
import { ClientSession, Types } from 'mongoose';
import { Logger } from '../../../../../libs/services/logger.service';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import {
  CONVERSATION_STATUS,
  ConversationStatus,
} from '../../../constants/constants';
import { ChatSession } from '../../../schema/chat.session.schema';
import { IChatSessionActiveRun } from '../../../types/conversation.interfaces';
import {
  ConversationBusyError,
  ConversationNotFoundError,
} from '../domain/errors';
import { Caller } from '../domain/types';
import {
  HEARTBEAT_INTERVAL_MS,
  LEASE_TTL_MS,
  leaseFree,
  leaseOwned,
  leaseRenewUpdate,
} from './lease-filters';
import {
  IRunLeaseManager,
  LeaseHandle,
  NewSessionLease,
  ReleaseOptions,
  SessionFilter,
} from './lease.types';

export interface RunLeaseManagerDeps {
  users?: IUserDirectory;
  clock?: IClock;
  logger?: Pick<Logger, 'warn'>;
  instanceId?: string;
  heartbeatMs?: number;
}

const defaultLogger = Logger.getInstance({ service: 'RunLeaseManager' });

/** A miss can race a release between the update and the re-read; one retry settles it. */
const MAX_ACQUIRE_ATTEMPTS = 2;

interface PriorState {
  status?: string;
  failReason?: string;
}

/** A run left `Inprogress` (a crash) or a never-run `None` is not put back: the session is idle. */
const restorableStatus = (
  status: string | undefined,
): ConversationStatus | undefined =>
  status === CONVERSATION_STATUS.COMPLETE ||
  status === CONVERSATION_STATUS.FAILED ||
  status === CONVERSATION_STATUS.STOPPED
    ? status
    : undefined;

class MongoLeaseHandle implements LeaseHandle {
  private timer: NodeJS.Timeout | undefined;
  private releasing: Promise<void> | undefined;

  constructor(
    readonly sessionId: string,
    readonly runId: string,
    readonly userId: string,
    readonly orgId: string,
    private readonly clock: IClock,
    private readonly logger: Pick<Logger, 'warn'>,
    private readonly heartbeatMs: number,
    private readonly before?: PriorState,
  ) {}

  async renew(dbSession?: ClientSession | null): Promise<boolean> {
    const result = await ChatSession.updateOne(
      { _id: new Types.ObjectId(this.sessionId), ...leaseOwned(this.runId) },
      leaseRenewUpdate(new Date(this.clock.now())),
      dbSession ? { session: dbSession } : undefined,
    );
    return result.matchedCount > 0;
  }

  release(
    status: ConversationStatus,
    options: ReleaseOptions = {},
  ): Promise<void> {
    this.stopHeartbeat();
    const restored = options.restorePrevious ? this.before : undefined;
    this.releasing ??= ChatSession.updateOne(this.ownershipFilter(), {
      $set: {
        activeRun: null,
        status: restorableStatus(restored?.status) ?? status,
        ...(restored?.failReason !== undefined && {
          failReason: restored.failReason,
        }),
      },
      $inc: { rev: 1 },
    })
      .then(() => undefined)
      .catch((error: unknown) => {
        this.releasing = undefined;
        throw error;
      });
    return this.releasing;
  }

  ownershipFilter(): SessionFilter {
    return {
      _id: new Types.ObjectId(this.sessionId),
      ...leaseOwned(this.runId),
    };
  }

  startHeartbeat(onLost: () => void): void {
    if (this.timer) return;
    let inFlight = false;
    this.timer = setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      void this.renew()
        .catch((error: unknown) => {
          this.logger.warn('Lease heartbeat failed', {
            sessionId: this.sessionId,
            runId: this.runId,
            error: error instanceof Error ? error.message : String(error),
          });
          return false;
        })
        .then((held) => {
          inFlight = false;
          if (held || !this.timer) return;
          this.stopHeartbeat();
          onLost();
        });
    }, this.heartbeatMs);
    this.timer.unref();
  }

  stopHeartbeat(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }
}

export class MongoRunLeaseManager implements IRunLeaseManager {
  private readonly clock: IClock;
  private readonly logger: Pick<Logger, 'warn'>;
  private readonly instanceId: string;
  private readonly heartbeatMs: number;

  constructor(private readonly deps: RunLeaseManagerDeps = {}) {
    this.clock = deps.clock ?? systemClock;
    this.logger = deps.logger ?? defaultLogger;
    this.instanceId = deps.instanceId ?? hostname();
    this.heartbeatMs = deps.heartbeatMs ?? HEARTBEAT_INTERVAL_MS;
  }

  async acquire(
    sessionId: string,
    caller: Caller,
    writeFilter: SessionFilter,
  ): Promise<LeaseHandle> {
    const _id = new Types.ObjectId(sessionId);
    const orgId = new Types.ObjectId(caller.orgId);
    for (let attempt = 1; ; attempt += 1) {
      const now = new Date(this.clock.now());
      const activeRun = this.newActiveRun(caller.userId, now);
      // The state before the update, so a turn that never starts can put it back.
      const before = await ChatSession.findOneAndUpdate(
        {
          _id,
          orgId,
          isDeleted: false,
          $and: [writeFilter, leaseFree(now)],
        },
        {
          $set: { activeRun, status: CONVERSATION_STATUS.INPROGRESS },
          $unset: { failReason: 1 },
          $inc: { rev: 1 },
        },
        { projection: { status: 1, failReason: 1 } },
      ).lean<PriorState>();
      if (before)
        return this.handleFor(
          sessionId,
          activeRun.runId,
          caller.userId,
          caller.orgId,
          {
            status: before.status,
            failReason: before.failReason,
          },
        );

      // Re-read under the caller's write access: a miss with no row is lost access (404 does not
      // reveal existence); a row with a live lease is a busy conversation.
      const current = await ChatSession.findOne({
        _id,
        orgId,
        isDeleted: false,
        $and: [writeFilter],
      })
        .select('activeRun')
        .lean();
      if (!current) throw new ConversationNotFoundError();
      const holder = current.activeRun;
      if (holder && holder.leaseExpiresAt.getTime() > this.clock.now()) {
        throw new ConversationBusyError({
          userId: holder.userId.toString(),
          displayName: await this.displayNameOf(
            caller.orgId,
            holder.userId.toString(),
          ),
          startedAt: holder.startedAt,
        });
      }
      if (attempt >= MAX_ACQUIRE_ATTEMPTS) {
        throw new ConversationBusyError({
          userId: caller.userId,
          startedAt: new Date(this.clock.now()),
        });
      }
    }
  }

  forNewSession(userId: string, orgId: string): NewSessionLease {
    const activeRun = this.newActiveRun(userId, new Date(this.clock.now()));
    return {
      activeRun,
      bind: (sessionId) =>
        this.handleFor(sessionId, activeRun.runId, userId, orgId),
    };
  }

  private newActiveRun(userId: string, now: Date): IChatSessionActiveRun {
    return {
      runId: randomUUID(),
      userId: new Types.ObjectId(userId),
      instanceId: this.instanceId,
      startedAt: now,
      leaseExpiresAt: new Date(now.getTime() + LEASE_TTL_MS),
    };
  }

  private handleFor(
    sessionId: string,
    runId: string,
    userId: string,
    orgId: string,
    before?: PriorState,
  ): LeaseHandle {
    return new MongoLeaseHandle(
      sessionId,
      runId,
      userId,
      orgId,
      this.clock,
      this.logger,
      this.heartbeatMs,
      before,
    );
  }

  private async displayNameOf(
    orgId: string,
    userId: string,
  ): Promise<string | undefined> {
    if (!this.deps.users) return undefined;
    try {
      const names = await this.deps.users.displayNames(orgId, [userId]);
      const name = names.get(userId);
      return name === undefined || name === '' ? undefined : name;
    } catch (error) {
      this.logger.warn('Could not resolve the lease holder name', {
        error: error instanceof Error ? error.message : String(error),
      });
      return undefined;
    }
  }
}
