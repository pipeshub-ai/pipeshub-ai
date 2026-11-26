import type {
  IMessageConsumer,
  StreamMessage,
} from '../../../../libs/types/messaging.types';
import {
  directoryEventOf,
  ENTITY_EVENTS_TOPIC,
  type DirectoryEvent,
} from '../../domain/directory-events';
import type { ControlPlaneLog } from '../../ports/log';

/** What a directory event invalidates: the caches of `application/directory-cache.ts`. */
export interface DirectoryInvalidation {
  teamMembershipChanged(
    orgId: string,
    teamId: string,
    userIds: readonly string[],
  ): void;
  agentChanged(orgId: string, agentKey: string): void;
}

/**
 * Follows `entity-events` in a consumer group of its own process, so every
 * Node process drops its own cache entries (02-lld §8.5). A lost event costs
 * at most the caches' TTL.
 */
export class DirectoryEventsSubscriber {
  private consumer: IMessageConsumer | null = null;

  constructor(
    private readonly createConsumer: () => IMessageConsumer,
    private readonly membership: Pick<
      DirectoryInvalidation,
      'teamMembershipChanged'
    >,
    private readonly agents: Pick<DirectoryInvalidation, 'agentChanged'>,
    private readonly log: ControlPlaneLog,
  ) {}

  async start(): Promise<void> {
    if (this.consumer !== null) return;
    const consumer = this.createConsumer();
    await consumer.connect();
    await consumer.subscribe([ENTITY_EVENTS_TOPIC], false);
    await consumer.consume((message) => this.handle(message));
    this.consumer = consumer;
  }

  async stop(): Promise<void> {
    const consumer = this.consumer;
    this.consumer = null;
    if (consumer?.isConnected() === true) await consumer.disconnect();
  }

  handle(message: StreamMessage<unknown>): Promise<void> {
    const event = directoryEventOf(message.value);
    if (event !== null) this.apply(event);
    return Promise.resolve();
  }

  apply(event: DirectoryEvent): void {
    if (event.type === 'teamMembershipChanged') {
      this.membership.teamMembershipChanged(
        event.orgId,
        event.teamId,
        event.userIds,
      );
    } else {
      this.agents.agentChanged(event.orgId, event.agentKey);
    }
    this.log.info('cp.directory.invalidated', {
      orgId: event.orgId,
      event: event.type,
    });
  }
}
