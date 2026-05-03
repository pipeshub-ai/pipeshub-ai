import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';

export enum EventType {
  LLMConfiguredEvent = 'llmConfigured',
  EmbeddingModelConfiguredEvent = 'embeddingModelConfigured',
  ConnectorPublicUrlChangedEvent = 'connectorPublicUrlChanged',
  GmailUpdatesEnabledEvent = 'gmailUpdatesEnabledEvent',
  GmailUpdatesDisabledEvent = 'gmailUpdatesDisabledEvent',
  AppEnabledEvent = 'appEnabled',
  AppDisabledEvent = 'appDisabled',
}

export enum SyncAction {
  None = 'none',
  Immediate = 'immediate',
  Scheduled = 'scheduled',
}

export interface AppEnabledEvent {
  orgId: string;
  appGroup: string;
  appGroupId: string;
  credentialsRoute?: string;
  refreshTokenRoute?: string;
  apps: string[];
  syncAction: SyncAction;
}
export interface AppDisabledEvent {
  orgId: string;
  appGroup: string;
  appGroupId: string;
  apps: string[];
}

export interface SyncConnectorEvent {
  orgId: string;
  apps: string[];
  syncAction: SyncAction;
  credentialsRoute?: string;
  refreshTokenRoute?: string;
}

export interface Event {
  eventType: EventType;
  timestamp: number;
  payload:
    | LLMConfiguredEvent
    | EmbeddingModelConfiguredEvent
    | ConnectorPublicUrlChangedEvent
    | GmailUpdatesEnabledEvent
    | GmailUpdatesDisabledEvent
    | AppEnabledEvent
    | AppDisabledEvent
    | SyncConnectorEvent;
}

export interface LLMConfiguredEvent {
  credentialsRoute: string;
}

export interface EmbeddingModelConfiguredEvent {
  credentialsRoute: string;
}

export interface ConnectorPublicUrlChangedEvent {
  orgId: string;
  url: string;
}

export interface GmailUpdatesEnabledEvent {
  orgId: string;
  topicName: string;
}
export interface GmailUpdatesDisabledEvent {
  orgId: string;
}

@injectable()
export class SyncEventProducer {
  private readonly topic = 'sync-events';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  async start(): Promise<void> {
    if (!this.producer.isConnected()) {
      await this.producer.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.producer.isConnected()) {
      await this.producer.disconnect();
    }
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  async publishEvent(event: Event): Promise<void> {
    const message: StreamMessage<string> = {
      key: event.eventType,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}

@injectable()
export class EntitiesEventProducer {
  private readonly topic = 'entity-events';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  async start(): Promise<void> {
    if (!this.producer.isConnected()) {
      await this.producer.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.producer.isConnected()) {
      await this.producer.disconnect();
    }
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  async publishEvent(event: Event): Promise<void> {
    const message: StreamMessage<string> = {
      key: event.eventType,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}
