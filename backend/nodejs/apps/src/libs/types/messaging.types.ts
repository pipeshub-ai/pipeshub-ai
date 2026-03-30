export type MessageBrokerType = 'kafka' | 'redis';

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

export interface StreamMessage<T = any> {
  key: string;
  value: T;
  headers?: Record<string, string>;
}

export interface TopicDefinition {
  topic: string;
  numPartitions?: number;
  replicationFactor?: number;
}

export interface IMessageProducer<T = any> {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
  publish(topic: string, message: StreamMessage<T>): Promise<void>;
  publishBatch(topic: string, messages: StreamMessage<T>[]): Promise<void>;
  healthCheck(): Promise<boolean>;
}

export interface IMessageConsumer<T = any> {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
  subscribe(topics: string[], fromBeginning?: boolean): Promise<void>;
  consume(handler: (message: StreamMessage<T>) => Promise<void>): Promise<void>;
  pause(topics: string[]): void;
  resume(topics: string[]): void;
  healthCheck(): Promise<boolean>;
}

export interface IMessageAdmin {
  ensureTopicsExist(topics?: TopicDefinition[]): Promise<void>;
  listTopics(): Promise<string[]>;
}
