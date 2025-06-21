import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';

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

@injectable()
export class EntitiesEventProducer extends BaseKafkaProducerConnection {
  private readonly topic = 'entity-events';

  constructor(
    @inject('KafkaConfig') config: KafkaConfig,
    @inject('Logger') logger: Logger,
  ) {
    super(config, logger);
  }

  async start(): Promise<void> {
    if (!this.isConnected) {
      await this.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.isConnected()) {
      await this.disconnect();
    }
  }

  async publishEvent(event: Event): Promise<void> {
    const message: KafkaMessage<string> = {
      key: event.eventType,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}
