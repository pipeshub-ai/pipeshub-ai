import { Logger } from '../../../../../libs/services/logger.service';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { TtlCache } from '../../../../../libs/utils/ttl-cache';
import { OwnerStatusUnavailableError } from '../domain/errors';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';

export const OWNER_ACTIVITY_TTL_MS = 60_000;
const MAX_ENTRIES = 5_000;

/** D11: whether a conversation owner is still an enabled, undeleted user. Cached per `org:user`. */
export class OwnerActivity {
  private readonly cache: TtlCache<boolean>;

  constructor(
    private readonly users: IUserDirectory,
    private readonly logger: Pick<Logger, 'warn'>,
    clock: IClock = systemClock,
  ) {
    this.cache = new TtlCache(OWNER_ACTIVITY_TTL_MS, MAX_ENTRIES, clock);
  }

  /** Fails closed: a directory outage throws 503 rather than let a send through on an owner that may be disabled; failures are not cached. */
  async isActive(orgId: string, ownerId: string): Promise<boolean> {
    const key = `${orgId}:${ownerId}`;
    const hit = this.cache.get(key);
    if (hit !== undefined) {
      return hit;
    }
    try {
      const [owner] = await this.users.findByIds(orgId, [ownerId]);
      const active = owner !== undefined && !owner.isDisabled;
      this.cache.set(key, active);
      return active;
    } catch (error) {
      this.logger.warn('Owner activity lookup failed; refusing the request', {
        error: error instanceof Error ? error.message : String(error),
      });
      throw new OwnerStatusUnavailableError();
    }
  }
}
