import { SASLOptions } from 'kafkajs';

export interface KafkaConfig {
  clientId?: string;
  brokers: string[];
  groupId?: string;
  sasl?: SASLOptions;
  maxRetries?: number;
  initialRetryTime?: number;
  maxRetryTime?: number;
}

export interface KafkaMessage<T> {
  key: string;
  value: T;
  headers?: Record<string, string>;
}

export interface IKafkaConnection {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
}

export interface IKafkaProducer<T = any> {
  publish(topic: string, message: KafkaMessage<T>): Promise<void>;
  publishBatch(topic: string, messages: KafkaMessage<T>[]): Promise<void>;
}

export interface IKafkaConsumer<T = any> {
  subscribe(topics: string[]): Promise<void>;
  consume(handler: (message: KafkaMessage<T>) => Promise<void>): Promise<void>;
  pause(topics: string[]): void;
  resume(topics: string[]): void;
}
