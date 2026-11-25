import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { DeploymentCapabilities } from '../../domain/capabilities';
import { CapabilityProbe } from '../../ports/capability-probe';

/**
 * `livebus` (REPORT 11) from the Redis the deployment already runs, whatever
 * its message broker: reachable Redis streams tokens live; otherwise runs
 * stream at message granularity from the journal (`inproc`).
 */
export class LiveBusCapabilityProbe implements CapabilityProbe {
  readonly name = 'livebus';
  readonly provides = ['livebus'] as const;

  constructor(private readonly provider: IRedisConnectionProvider) {}

  async probe(): Promise<Partial<DeploymentCapabilities>> {
    let reachable: boolean;
    try {
      reachable = await this.provider.ping();
    } catch {
      reachable = false;
    }
    return { livebus: reachable ? 'redis' : 'inproc' };
  }
}
