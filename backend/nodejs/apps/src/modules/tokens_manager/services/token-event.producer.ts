import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ITokenEvent } from '../schema/token-reference.schema';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';

@injectable()
export class TokenEventProducer {
  private readonly topic = 'token-events';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  async start(): Promise<void> {
    if (!this.producer.isConnected()) {
      await this.producer.connect();
      this.logger.info('TokenEventProducer connected');
    }
  }

  async stop(): Promise<void> {
    if (this.producer.isConnected()) {
      await this.producer.disconnect();
      this.logger.info('TokenEventProducer disconnected');
    }
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  async healthCheck(): Promise<boolean> {
    return this.producer.healthCheck();
  }

  async publishTokenEvent(event: ITokenEvent): Promise<void> {
    const message: StreamMessage<ITokenEvent> = {
      key: `${event.tokenReferenceId}-${event.serviceType}`,
      value: event,
    };

    await this.producer.publish(this.topic, message);
  }
}
