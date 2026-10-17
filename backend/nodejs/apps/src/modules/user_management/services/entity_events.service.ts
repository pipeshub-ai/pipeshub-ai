import { injectable, inject } from 'inversify';
import { ClientSession } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { OutboxEvent } from '../../../libs/services/outbox/outbox.schema';
import { IMessageProducer } from '../../../libs/types/messaging.types';

export enum AccountType {
  Individual = 'individual',
  Business = 'business',
}

export enum SyncAction {
  None = 'none',
  Immediate = 'immediate',
  Scheduled = 'scheduled',
}

export enum EventType {
  OrgCreatedEvent = 'orgCreated',
  OrgUpdatedEvent = 'orgUpdated',
  OrgDeletedEvent = 'orgDeleted',
  NewUserEvent = 'userAdded',
  UpdateUserEvent = 'userUpdated',
  DeleteUserEvent = 'userDeleted',
}

export interface Event {
  eventType: EventType;
  timestamp: number;
  payload:
    | OrgAddedEvent
    | OrgDeletedEvent
    | OrgUpdatedEvent
    | UserAddedEvent
    | UserDeletedEvent
    | UserUpdatedEvent;
}

export interface OrgAddedEvent {
  orgId: string;
  accountType: AccountType;
  registeredName: string;
  userId?: string;
}
export interface OrgUpdatedEvent {
  orgId: string;
  registeredName: string;
}

export interface OrgDeletedEvent {
  orgId: string;
}

export interface UserAddedEvent {
  orgId: string;
  userId: string;
  fullName?: string;
  firstName?: string;
  middleName?: string;
  lastName?: string;
  email: string;
  designation?: string;
  syncAction: SyncAction;
}

export interface UserDeletedEvent {
  orgId: string;
  userId: string;
  email: string;
}

export interface UserUpdatedEvent {
  orgId: string;
  userId: string;
  firstName?: string;
  middleName?: string;
  lastName?: string;
  fullName?: string;
  designation?: string;
  email: string;
}

/**
 * What an event is about, so the dispatcher can keep one entity's events in
 * the order they happened without holding up everyone else's.
 *
 * A user's own history is the sequence that must not be reordered — added,
 * updated, deleted — so the key is the user where there is one, and the
 * organisation otherwise. Anything without either falls back to a single
 * shared key, which is stricter than necessary and never wrong.
 */
export function orderingKeyFor(event: Event): string {
  const payload = event.payload as {
    orgId?: unknown;
    userId?: unknown;
    email?: unknown;
  };
  const org = payload.orgId == null ? '' : String(payload.orgId);
  const user =
    payload.userId == null
      ? payload.email == null
        ? ''
        : String(payload.email)
      : String(payload.userId);

  if (user !== '') return `user:${org}:${user}`;
  if (org !== '') return `org:${org}`;
  return 'global';
}

@injectable()
export class EntitiesEventProducer {
  private readonly topic = 'entity-events';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  /**
   * Kept so the many callers that bracket a publish with start/stop need no
   * change. Neither does anything now: events are written to the outbox and
   * the dispatcher owns the broker connection, along with the producer's
   * lifecycle. Disconnecting here used to be actively harmful, since the
   * producer is a single instance shared with the notification producer.
   */
  async start(): Promise<void> {
    return Promise.resolve();
  }

  async stop(): Promise<void> {
    return Promise.resolve();
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  /**
   * Records an event for delivery. It is written to the outbox rather than
   * sent, and the dispatcher delivers it.
   *
   * This used to publish directly and swallow broker failures, which meant a
   * caller could write a user, fail to tell the permission graph, and report
   * success — leaving someone who could sign in but see nothing, with nothing
   * explaining why. Writing it down instead makes delivery late rather than
   * lost.
   *
   * Pass the `session` of the transaction that made the change, where the
   * caller has one. The event is then stored by the same commit that stored
   * the change, so the two cannot disagree. Without a session the write still
   * happens, just not atomically: the remaining gap is the microseconds
   * between two database writes, rather than the length of a broker outage.
   *
   * Unlike the old version this throws. Failing to write to the database is
   * not a broker hiccup to be logged past; it means the caller's own operation
   * has not fully happened, and the caller should hear about it.
   */
  async publishEvent(event: Event, session?: ClientSession): Promise<void> {
    const doc = {
      topic: this.topic,
      key: event.eventType,
      orderingKey: orderingKeyFor(event),
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
      status: 'pending' as const,
      attempts: 0,
      nextAttemptAt: new Date(),
    };

    await OutboxEvent.create([doc], session ? { session } : {});

    this.logger.debug('Event queued for delivery', {
      eventType: event.eventType,
      topic: this.topic,
      transactional: Boolean(session),
    });
  }
}
