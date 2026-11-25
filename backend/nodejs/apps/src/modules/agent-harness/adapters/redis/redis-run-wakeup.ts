import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { RunWakeup } from '../../ports/run-wakeup';

export const WAKE_PREFIX = 'harness:interaction-notify:';

export const wakeChannel = (runId: string, namespace = ''): string =>
  `${namespace ? `${namespace}:` : ''}${WAKE_PREFIX}${runId}`;

/** Publishes the wake; a failure is the caller's to log, never a failed resolution. */
export class RedisRunWakeup implements RunWakeup {
  constructor(private readonly provider: IRedisConnectionProvider) {}

  async wake(runId: string): Promise<void> {
    await this.provider
      .getClient()
      .publish(wakeChannel(runId, this.provider.keyNamespace), '');
  }
}
