import { getMessageBrokerType } from '../../../../libs/services/message-broker.factory';
import { MessageBrokerType } from '../../../../libs/types/messaging.types';
import { AgentHarnessEnvironment } from '../../config';
import { DeploymentCapabilities } from '../../domain/capabilities';
import { CapabilityProbe } from '../../ports/capability-probe';
import { envFlag } from '../../../../libs/utils/env-flags';

/**
 * Dispatch and live-delta transport from what the deployment already runs:
 * a worker role behind the shared outbox, and the message broker. Redis
 * Streams as the broker proves Redis Streams for the LiveBus; on Kafka the
 * LiveBus stays in-process until its own probe exists (S6).
 */
export class DispatchCapabilityProbe implements CapabilityProbe {
  readonly name = 'dispatch';
  readonly provides = ['workers.dispatched', 'livebus'] as const;

  constructor(
    private readonly environment: AgentHarnessEnvironment = process.env,
    private readonly brokerType: () => MessageBrokerType = getMessageBrokerType,
  ) {}

  probe(): Promise<Partial<DeploymentCapabilities>> {
    const broker = this.brokerType();
    return Promise.resolve({
      'workers.dispatched':
        envFlag('AGENT_HARNESS_WORKER_ENABLED', this.environment) &&
        envFlag('AGENT_HARNESS_RELAY_ENABLED', this.environment),
      livebus: broker === MessageBrokerType.REDIS ? 'redis' : 'inproc',
    });
  }
}
