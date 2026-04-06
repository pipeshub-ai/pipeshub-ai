import { SASLOptions } from 'kafkajs';
import { MessageBrokerType, StreamMessage } from './messaging.types';

export interface KafkaConfig {
  type: MessageBrokerType.KAFKA;
  clientId?: string;
  brokers: string[];
  groupId?: string;
  sasl?: SASLOptions;
  ssl?: boolean;
  maxRetries?: number;
  initialRetryTime?: number;
  maxRetryTime?: number;
}

/** @deprecated Use StreamMessage from messaging.types instead */
export type KafkaMessage<T> = StreamMessage<T>;

export interface IKafkaConnection {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
}

export interface IKafkaProducer<T = any> {
  publish(topic: string, message: StreamMessage<T>): Promise<void>;
  publishBatch(topic: string, messages: StreamMessage<T>[]): Promise<void>;
}

export interface IKafkaConsumer<T = any> {
  subscribe(topics: string[]): Promise<void>;
  consume(handler: (message: StreamMessage<T>) => Promise<void>): Promise<void>;
  pause(topics: string[]): void;
  resume(topics: string[]): void;
}
