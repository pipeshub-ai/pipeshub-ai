import { injectable, inject } from 'inversify';
import { FilterQuery } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { EntitiesEventProducer, ModelWithPendingEvents, Event } from './entity_events.service';
import { Users } from '../schema/users.schema';
import { Org } from '../schema/org.schema';

@injectable()
export class EntitiesEventDispatcher {
  private timer: NodeJS.Timeout | null = null;
  private readonly MAX_RETRIES = 5;

  constructor(
    @inject('Logger') private readonly logger: Logger,
    @inject('EntitiesEventProducer') private readonly eventProducer: EntitiesEventProducer
  ) {}

  start(intervalMs: number = 10000): void {
    if (this.timer) {
      return;
    }
    this.timer = setInterval(() => {
      this.pollAndDispatch().catch(err => {
        this.logger.error('Error in EntitiesEventDispatcher poll cycle', err);
      });
    }, intervalMs);
    this.logger.info('EntitiesEventDispatcher started');
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      this.logger.info('EntitiesEventDispatcher stopped');
    }
  }

  private async pollAndDispatch(): Promise<void> {
    await this.processModel(Users, 'Users');
    await this.processModel(Org, 'Orgs');
  }

  private async processModel(model: ModelWithPendingEvents, modelName: string): Promise<void> {
    // 1. Recover stale 'processing' events (older than 5 minutes)
    const staleThreshold = new Date(Date.now() - 5 * 60 * 1000);
    const staleDocs = await model.find({
      pendingEvents: { $elemMatch: { status: 'processing', claimedAt: { $lt: staleThreshold } } }
    }).limit(100).exec();

    for (const doc of staleDocs) {
      for (const event of doc.pendingEvents) {
        if (event.status === 'processing' && event.claimedAt && new Date(event.claimedAt) < staleThreshold) {
          this.logger.warn(`Recovering stuck processing event ${event.eventId} for ${modelName} ${doc._id}`);
          const query: FilterQuery<ModelWithPendingEvents> = { _id: doc._id, 'pendingEvents.eventId': event.eventId, 'pendingEvents.status': 'processing' };
          if (event.claimToken) {
            query['pendingEvents.claimToken'] = event.claimToken;
          }
          await model.updateOne(
            query,
            { 
              $set: { 'pendingEvents.$.status': 'pending' },
              $inc: { 'pendingEvents.$.retries': 1 }
            }
          );
        }
      }
    }

    // 2. Normal pending event processing
    const docs = await model.find({
      pendingEvents: { $elemMatch: { status: 'pending' } }
    }).limit(100).exec();

    for (const doc of docs) {
      for (const event of doc.pendingEvents) {
        if (event.status === 'pending') {
          if (event.retries >= this.MAX_RETRIES) {
            // Dead-letter
            this.logger.error(`Dead-lettering event ${event.eventId} for ${modelName} ${doc._id} after ${this.MAX_RETRIES} retries`);
            await model.updateOne(
              { _id: doc._id, 'pendingEvents.eventId': event.eventId },
              { $set: { 'pendingEvents.$.status': 'failed' } }
            );
            continue;
          }

          // Use the producer to claim and publish
          await this.eventProducer.dispatchInline(
            model,
            doc._id.toString(),
            event.eventId,
            {
              eventType: event.eventType,
              timestamp: event.timestamp,
              payload: event.payload
            } as Event
          );
        }
      }
    }
  }
}
