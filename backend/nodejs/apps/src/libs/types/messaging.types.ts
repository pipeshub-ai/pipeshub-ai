export type MessageBrokerType = 'kafka' | 'redis';

/**
 * Canonical broker topics used across Kafka and Redis Streams in this codebase.
 */
export type BrokerTopic =
  | 'record-events'
  | 'entity-events'
  | 'sync-events'
  | 'health-check'
  | 'token-events'
  | 'notification';

/**
 * Topic -> payload mapping.
 * Notes:
 * - `record-events` / `entity-events` / `sync-events` are currently published as JSON strings.
 * - Token/notification payloads remain broad until all producers/consumers are migrated to shared event contracts.
 */
export interface BrokerTopicPayloadMap {
  'record-events': string;
  'entity-events': string;
  'sync-events': string;
  'health-check': { type: string; timestamp: number };
  'token-events': Record<string, unknown>;
  notification: Record<string, unknown>;
}

export interface MessageBrokerConfig {
  type: MessageBrokerType;
  clientId?: string;
  groupId?: string;
  maxRetries?: number;
  initialRetryTime?: number;
  maxRetryTime?: number;
}

export interface KafkaBrokerConfig extends MessageBrokerConfig {
  type: 'kafka';
  brokers: string[];
  sasl?: {
    mechanism: string;
    username: string;
    password: string;
  };
  ssl?: boolean;
}

export interface RedisConfig {
  host: string;
  port: number;
  username?: string;
  password?: string;
  tls?: boolean;
  db?: number;
}

export interface RedisBrokerConfig extends MessageBrokerConfig {
  type: 'redis';
  host: string;
  port: number;
  password?: string;
  db?: number;
  maxLen?: number;
  keyPrefix?: string;
}

export interface StreamMessage<T> {
  key: string;
  value: T;
  headers?: Record<string, string>;
}

export type StreamMessageForTopic<TTopic extends BrokerTopic> = StreamMessage<
  BrokerTopicPayloadMap[TTopic]
>;

export interface TopicDefinition {
  topic: string;
  numPartitions?: number;
  replicationFactor?: number;
}

export interface IMessageProducer {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
  publish<T>(topic: string, message: StreamMessage<T>): Promise<void>;
  publishBatch<T>(topic: string, messages: StreamMessage<T>[]): Promise<void>;
  healthCheck(): Promise<boolean>;
}

export interface IMessageConsumer {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
  subscribe(topics: string[], fromBeginning?: boolean): Promise<void>;
  consume<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void>;
  pause(topics: string[]): void;
  resume(topics: string[]): void;
  healthCheck(): Promise<boolean>;
}

export interface IMessageAdmin {
  ensureTopicsExist(topics?: TopicDefinition[]): Promise<void>;
  listTopics(): Promise<string[]>;
}
