import { injectable, unmanaged } from 'inversify';
import { Kafka, Producer, Consumer } from 'kafkajs';

import { KafkaError } from '../errors/kafka.errors';
import { Logger } from './logger.service';
import {
  KafkaConfig,
  IKafkaConnection,
  IKafkaProducer,
  KafkaMessage,
  IKafkaConsumer,
} from '../types/kafka.types';
import { BadRequestError } from '../errors/http.errors';

@injectable()
export abstract class BaseKafkaConnection implements IKafkaConnection {
  protected kafka!: Kafka;
  protected isInitialized = false;

  constructor(
    @unmanaged() protected readonly config: KafkaConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.initializeKafka();
  }

  private initializeKafka(): void {
    try {
      this.kafka = new Kafka({
        clientId: this.config.clientId,
        brokers: this.config.brokers,
        // ssl: this.config.ssl,
        sasl: this.config.sasl,
      });
    } catch (error) {
      throw new KafkaError('Failed to initialize Kafka', {
        clientId: this.config.clientId,
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  abstract connect(): Promise<void>;
  abstract disconnect(): Promise<void>;
  abstract healthCheck(): Promise<boolean>;

  isConnected(): boolean {
    return this.isInitialized;
  }

  protected async ensureConnection(): Promise<void> {
    if (!this.isConnected()) {
      await this.connect();
    }
  }
}

@injectable()
export abstract class BaseKafkaProducerConnection
  extends BaseKafkaConnection
  implements IKafkaProducer
{
  protected producer: Producer;

  constructor(@unmanaged() config: KafkaConfig, @unmanaged() logger: Logger) {
    super(config, logger);
    this.producer = this.kafka.producer({
      allowAutoTopicCreation: true,
      transactionTimeout: 30000,
    });
  }

  async connect(): Promise<void> {
    try {
      if (!this.isInitialized) {
        await this.producer.connect();
        this.isInitialized = true;
        this.logger.info('Successfully connected Kafka producer');
      }
    } catch (error) {
      this.isInitialized = false;
      throw new KafkaError('Failed to connect Kafka producer', {
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async disconnect(): Promise<void> {
    try {
      if (this.isInitialized) {
        await this.producer.disconnect();
        this.isInitialized = false;
        this.logger.info('Successfully disconnected Kafka producer');
      }
    } catch (error) {
      this.logger.error('Error disconnecting Kafka producer', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async publish<T>(topic: string, message: KafkaMessage<T>): Promise<void> {
    await this.ensureConnection();
    await this.sendToKafka(topic, [this.formatMessage(message)]);
  }

  async publishBatch<T>(
    topic: string,
    messages: KafkaMessage<T>[],
  ): Promise<void> {
    await this.ensureConnection();
    await this.sendToKafka(
      topic,
      messages.map((msg) => this.formatMessage(msg)),
    );
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.ensureConnection();
      await this.publish('health-check', {
        key: 'health-check',
        value: {
          type: 'HEALTH_CHECK',
          timestamp: Date.now(),
        },
      });
      return true;
    } catch (error) {
      this.logger.error('Kafka producer health check failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return false;
    }
  }

  protected formatMessage<T>(message: KafkaMessage<T>) {
    return {
      key: message.key,
      value: JSON.stringify(message.value),
      headers: message.headers,
    };
  }

  private async sendToKafka(topic: string, messages: any[]): Promise<void> {
    try {
      await this.producer.send({
        topic,
        messages,
      });
      this.logger.debug('Successfully published to Kafka', {
        topic,
        messageCount: messages.length,
      });
    } catch (error) {
      throw new KafkaError(`Error publishing to Kafka topic ${topic}`, {
        topic,
        messageCount: messages.length,
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }
}

@injectable()
export abstract class BaseKafkaConsumerConnection
  extends BaseKafkaConnection
  implements IKafkaConsumer
{
  protected consumer: Consumer;

  constructor(@unmanaged() config: KafkaConfig, @unmanaged() logger: Logger) {
    super(config, logger);
    this.consumer = this.kafka.consumer({
      groupId: config.groupId || `${config.clientId}-group`,
      maxWaitTimeInMs: 5000,
      retry: {
        initialRetryTime: config.initialRetryTime || 100,
        maxRetryTime: config.maxRetryTime || 30000,
        retries: config.maxRetries || 8,
      },
    });
  }

  async connect(): Promise<void> {
    try {
      if (!this.isInitialized) {
        await this.consumer.connect();
        this.isInitialized = true;
        this.logger.info('Successfully connected Kafka consumer');
      }
    } catch (error) {
      this.isInitialized = false;
      throw new KafkaError('Failed to connect Kafka consumer', {
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async disconnect(): Promise<void> {
    try {
      if (this.isInitialized) {
        await this.consumer.disconnect();
        this.isInitialized = false;
        this.logger.info('Successfully disconnected Kafka consumer');
      }
    } catch (error) {
      this.logger.error('Error disconnecting Kafka consumer', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async subscribe(topics: string[], fromBeginning = false): Promise<void> {
    await this.ensureConnection();
    try {
      await Promise.all(
        topics.map((topic) =>
          this.consumer.subscribe({ topic, fromBeginning }),
        ),
      );
      this.logger.info('Successfully subscribed to topics', { topics });
    } catch (error) {
      throw new KafkaError('Failed to subscribe to topics', {
        topics,
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async consume<T>(
    handler: (message: KafkaMessage<T>) => Promise<void>,
  ): Promise<void> {
    await this.ensureConnection();
    try {
      await this.consumer.run({
        eachMessage: async ({ topic, partition, message }) => {
          try {
            if (!message.value) {
              throw new BadRequestError('Empty message value');
            }

            const parsedMessage: KafkaMessage<T> = {
              key: message.key?.toString() || '',
              value: JSON.parse(message.value.toString()),
              // headers: message.headers as Record<string, string | Buffer>,
            };

            await handler(parsedMessage);
          } catch (error) {
            this.logger.error('Error processing message', {
              topic,
              partition,
              messageKey: message.key?.toString(),
              error: error instanceof Error ? error.message : 'Unknown error',
            });
          }
        },
      });
    } catch (error) {
      throw new KafkaError('Failed to start message consumption', {
        details: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  pause(topics: string[]): void {
    topics.forEach((topic) => {
      this.consumer.pause([{ topic }]);
      this.logger.debug('Paused consumption', { topic });
    });
  }

  resume(topics: string[]): void {
    topics.forEach((topic) => {
      this.consumer.resume([{ topic }]);
      this.logger.debug('Resumed consumption', { topic });
    });
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.ensureConnection();
      return true;
    } catch (error) {
      this.logger.error('Kafka consumer health check failed', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return false;
    }
  }
}
