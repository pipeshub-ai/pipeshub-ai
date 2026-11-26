import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../application/harness-log';
import { RunReadyNotice, RunReadyNotifier } from '../../ports/run-ready';
import { dependencyHealth } from '../../ops/dependency-health';

export const RUN_READY_CHANNEL = 'harness:run-ready';

export const runReadyChannel = (namespace = ''): string =>
  `${namespace ? `${namespace}:` : ''}${RUN_READY_CHANNEL}`;

/** One message per notice, in order: `{orgId, runId, kind}`. */
export class RedisRunReadyNotifier implements RunReadyNotifier {
  constructor(
    private readonly provider: IRedisConnectionProvider,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async notify(notices: readonly RunReadyNotice[]): Promise<void> {
    const channel = runReadyChannel(this.provider.keyNamespace);
    for (const notice of notices) {
      try {
        await this.provider.getClient().publish(
          channel,
          JSON.stringify({
            orgId: notice.orgId,
            runId: notice.runId,
            kind: notice.kind,
          }),
        );
      } catch (error) {
        dependencyHealth.report('kv');
        this.logger.warn(
          'run_ready.publish_failed',
          harnessLogMeta('run_ready.publish_failed', {
            orgId: notice.orgId,
            runId: notice.runId,
            kind: notice.kind,
            errorType: error instanceof Error ? error.name : typeof error,
            choice: 'poll_backstop',
          }),
        );
      }
    }
  }
}
