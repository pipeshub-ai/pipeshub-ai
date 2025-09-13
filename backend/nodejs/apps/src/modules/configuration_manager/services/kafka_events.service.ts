import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';

export enum EventType {
  LLMConfiguredEvent = 'llmConfigured',
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
export class SyncEventProducer extends BaseKafkaProducerConnection {
  private readonly topic = 'sync-events';

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
