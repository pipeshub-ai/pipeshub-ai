import mongoose, { Schema, Document, Model } from 'mongoose';

/**
 * A domain event that has been decided but not yet handed to the broker.
 *
 * Several flows write to MongoDB and then publish an event that the Python
 * services consume to build the permission graph. Publishing straight from the
 * request meant a broker failure was invisible: the record was written, the
 * graph was never told, and the caller was told it had worked. A user could
 * exist, sign in, and see nothing, with nothing in the product explaining why.
 *
 * Writing the event here instead makes the intent durable. The row is created
 * with the change that caused it — in the same transaction where the
 * deployment supports one — and a dispatcher delivers it afterwards, retrying
 * until it succeeds. Delivery becomes late rather than lost.
 */
export type OutboxStatus = 'pending' | 'publishing' | 'published' | 'failed';

export interface IOutboxEvent extends Document {
  topic: string;
  key: string;
  /**
   * What this event is *about* — an organisation and, where there is one, a
   * user. Events sharing a key are delivered in the order they were written,
   * because they describe a sequence of changes to the same thing: added,
   * then updated, then deleted. Delivering those out of order would leave the
   * graph wrong, not merely late.
   *
   * Deliberately not the broker key, which is the event type and so groups
   * unrelated entities together while separating one entity's own history.
   */
  orderingKey: string;
  value: string;
  headers?: Record<string, string>;
  status: OutboxStatus;
  attempts: number;
  /** Earliest the dispatcher may try again; how backoff is expressed. */
  nextAttemptAt: Date;
  /** When a dispatcher took this row, so a stalled claim can be reclaimed. */
  claimedAt?: Date;
  lastError?: string;
  publishedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

const OutboxEventSchema = new Schema<IOutboxEvent>(
  {
    topic: { type: String, required: true },
    key: { type: String, required: true },
    orderingKey: { type: String, required: true, default: 'global' },
    value: { type: String, required: true },
    headers: { type: Schema.Types.Mixed },
    status: {
      type: String,
      enum: ['pending', 'publishing', 'published', 'failed'],
      default: 'pending',
      required: true,
    },
    attempts: { type: Number, default: 0 },
    nextAttemptAt: { type: Date, default: () => new Date(), required: true },
    claimedAt: { type: Date },
    lastError: { type: String },
    publishedAt: { type: Date },
  },
  { timestamps: true },
);

// How the dispatcher finds its next row: the due ones, oldest first.
OutboxEventSchema.index({ status: 1, nextAttemptAt: 1, createdAt: 1 });

// The same query sorts by createdAt while ranging over nextAttemptAt, which
// the index above cannot serve in order — Mongo would fall back to a blocking
// sort and, on a large enough backlog, exceed its sort-memory limit and fail
// the claim outright. This one produces createdAt order directly.
OutboxEventSchema.index({ status: 1, createdAt: 1 });

// Answers "is there anything older about this entity still undelivered?",
// which is what holds one entity's events in sequence.
OutboxEventSchema.index({ orderingKey: 1, status: 1, createdAt: 1 });

// Delivered rows are kept briefly so a delivery can be traced, then removed on
// their own rather than growing without limit. Only rows with publishedAt set
// are affected, so pending and failed events are never swept away.
OutboxEventSchema.index(
  { publishedAt: 1 },
  {
    expireAfterSeconds: 7 * 24 * 60 * 60,
    partialFilterExpression: { status: 'published' },
  },
);

// Reuses the model when this module is loaded twice, which the test runner
// does; registering the same name twice throws.
export const OutboxEvent: Model<IOutboxEvent> =
  (mongoose.models['outbox_events'] as Model<IOutboxEvent> | undefined) ??
  mongoose.model<IOutboxEvent>(
    'outbox_events',
    OutboxEventSchema,
    'outbox_events',
  );
