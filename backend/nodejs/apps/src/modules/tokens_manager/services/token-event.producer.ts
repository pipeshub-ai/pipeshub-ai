import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { ITokenEvent } from '../schema/token-reference.schema';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';

@injectable()
export class TokenEventProducer extends BaseKafkaProducerConnection {
  private readonly topic = 'token-events';

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

  async publishTokenEvent(event: ITokenEvent): Promise<void> {
    const message: KafkaMessage<ITokenEvent> = {
      key: `${event.tokenReferenceId}-${event.serviceType}`,
      value: event,
      // headers: {
      //   eventType: Buffer.from(event.eventType),
      //   timestamp: Buffer.from(event.timestamp.toString()),
      // },
    };

    await this.publish(this.topic, message);
  }
}
