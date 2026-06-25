import { Container } from 'inversify';
import { AppConfig } from '../config/config';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageProducer } from '../../../libs/types/messaging.types';
import {
  createMessageProducer,
  resolveMessageBrokerConfig,
} from '../../../libs/services/message-broker.factory';
import { RecordsEventProducer } from '../../knowledge_base/services/records_events.service';
import { SyncEventProducer } from '../../knowledge_base/services/sync_events.service';

const loggerConfig = {
  service: 'Connector Container',
};

export class ConnectorContainer {
  private static instance: Container;
  private static logger: Logger = Logger.getInstance(loggerConfig);

  static async initialize(appConfig: AppConfig): Promise<Container> {
    const container = new Container();
    container.bind<Logger>('Logger').toConstantValue(this.logger);

    await this.initializeServices(container, appConfig);

    this.instance = container;
    return container;
  }

  private static async initializeServices(
    container: Container,
    appConfig: AppConfig,
  ): Promise<void> {
    try {
      const brokerConfig = resolveMessageBrokerConfig(appConfig);
      const messageProducer = createMessageProducer(
        brokerConfig,
        container.get('Logger'),
      );
      await messageProducer.connect();

      container
        .bind<IMessageProducer>('MessageProducer')
        .toConstantValue(messageProducer);

      const recordsEventProducer = new RecordsEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      await recordsEventProducer.start();
      container
        .bind<RecordsEventProducer>('RecordsEventProducer')
        .toConstantValue(recordsEventProducer);

      const syncEventProducer = new SyncEventProducer(
        messageProducer,
        container.get('Logger'),
      );
      await syncEventProducer.start();
      container
        .bind<SyncEventProducer>('SyncEventProducer')
        .toConstantValue(syncEventProducer);
    } catch (error) {
      this.logger.error('Failed to initialize Connector services', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  static getInstance(): Container {
    if (!this.instance) {
      throw new Error('Connector container not initialized');
    }
    return this.instance;
  }

  static async dispose(): Promise<void> {
    if (this.instance) {
      try {
        const messageProducer = this.instance.isBound('MessageProducer')
          ? this.instance.get<IMessageProducer>('MessageProducer')
          : null;

        if (messageProducer && messageProducer.isConnected()) {
          await messageProducer.disconnect();
        }

        this.logger.info('Connector services disconnected successfully');
      } catch (error) {
        this.logger.error('Error while disconnecting Connector services', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      } finally {
        this.instance = null!;
      }
    }
  }
}
