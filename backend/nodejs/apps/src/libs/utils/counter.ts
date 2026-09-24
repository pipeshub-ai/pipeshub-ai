import mongoose, { Schema, Model } from 'mongoose';
import slug from 'slug';
import { Logger } from '../services/logger.service';

const logger = Logger.getInstance();

interface CounterDocument {
  _id: string;
  name?: string;
  seq: number;
}

const counterSchema = new Schema<CounterDocument>({
  _id: { type: String, required: true },
  // Unique so two creates racing on a fresh database cannot each insert their
  // own counter for the same name. Without it the upsert below filtered on a
  // non-unique field, so on an empty collection both inserts succeeded, both
  // returned seq 1, and the callers built the same slug — which then collided
  // on the unique slug index and returned HTTP 500 on the second create.
  name: { type: String, unique: true },
  seq: { type: Number, default: 1000 },
});

export const Counter: Model<CounterDocument> =
  mongoose.models.Counter ??
  mongoose.model<CounterDocument>('Counter', counterSchema);

const isDuplicateKeyError = (err: unknown): boolean =>
  typeof err === 'object' &&
  err !== null &&
  (err as { code?: number }).code === 11000;

const MAX_ATTEMPTS = 5;

// Mongoose builds schema indexes in the background once connected, so requests
// can be served before `name` is unique — and until it is, two concurrent
// upserts on a fresh database can each insert a counter, which is the collision
// this index exists to stop. Wait for the build rather than rely on that race.
// A build that fails, which an existing database already holding duplicate
// counter names would, is logged once rather than raised: slug creation keeps
// working exactly as it did before, and the retry below still resolves what it
// can.
let missingIndexWarned = false;

const ensureNameIndexBuilt = async (): Promise<void> => {
  try {
    // Idempotent and cheap: mongoose caches the build and hands back the same
    // promise on every call.
    await Counter.init();
  } catch (err) {
    if (!missingIndexWarned) {
      missingIndexWarned = true;
      logger.warn(
        'Counter "name" index could not be built; concurrent creates on a fresh database may still collide',
        { error: err instanceof Error ? err.message : String(err) },
      );
    }
  }
};

const getNextSequence = async (name: string): Promise<number> => {
  await ensureNameIndexBuilt();
  // With `name` unique, the loser of the fresh-database insert race gets a
  // duplicate-key error here instead of a second counter document. Retrying
  // then finds the counter the winner just inserted and increments it, so the
  // two callers receive different sequence numbers rather than the same one.
  for (let attempt = 1; ; attempt++) {
    try {
      const counter = await Counter.findOneAndUpdate(
        { name },
        { $inc: { seq: 1 } },
        { new: true, upsert: true },
      );
      return counter.seq;
    } catch (err) {
      if (isDuplicateKeyError(err) && attempt < MAX_ATTEMPTS) {
        continue;
      }
      throw err;
    }
  }
};

export const generateUniqueSlug = async (name: string): Promise<string> => {
  const counter = await getNextSequence(name);
  return slug(`${name}-${String(counter)}`);
};
