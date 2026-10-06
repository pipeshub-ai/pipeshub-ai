import { inject, injectable } from 'inversify';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { IClock, systemClock } from '../../../libs/types/clock';
import { Logger } from '../../../libs/services/logger.service';
import { PLATFORM_FEATURE_FLAGS } from '../constants/constants';
import { getPlatformSettingsFromStore } from '../utils/util';

export { COLLAB_FLAG_KEYS } from '../constants/constants';

export interface IFeatureFlags {
  /** Never throws. */
  isEnabled(key: string): Promise<boolean>;
}

const logger = Logger.getInstance({ service: 'PlatformFeatureFlags' });

@injectable()
export class PlatformFeatureFlags implements IFeatureFlags {
  private lastGood: Record<string, boolean> | null = null;
  private fetchedAt = 0;
  private inflight: Promise<Record<string, boolean> | null> | null = null;

  constructor(
    @inject('KeyValueStoreService')
    private readonly kv: KeyValueStoreService,
    private readonly clock: IClock = systemClock,
    private readonly ttlMs = 10_000,
  ) {}

  async isEnabled(key: string): Promise<boolean> {
    try {
      const flags = await this.load();
      if (flags && typeof flags[key] === 'boolean') {
        return flags[key];
      }
    } catch (error) {
      logger.warn('Feature flag lookup failed; using default', { key, error });
    }
    return (
      PLATFORM_FEATURE_FLAGS.find((d) => d.key === key)?.defaultEnabled ?? false
    );
  }

  private async load(): Promise<Record<string, boolean> | null> {
    if (this.lastGood && this.clock.now() - this.fetchedAt < this.ttlMs) {
      return this.lastGood;
    }
    if (!this.inflight) {
      this.inflight = this.refresh().finally(() => {
        this.inflight = null;
      });
    }
    return this.inflight;
  }

  private async refresh(): Promise<Record<string, boolean> | null> {
    try {
      const { featureFlags } = await getPlatformSettingsFromStore(this.kv);
      this.lastGood = featureFlags;
      this.fetchedAt = this.clock.now();
    } catch (error) {
      logger.warn('Platform settings read failed; serving last good value', {
        error,
      });
    }
    return this.lastGood;
  }
}
