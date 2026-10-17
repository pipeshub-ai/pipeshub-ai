import { Logger } from '../logger.service';
import { IMessageProducer, StreamMessage } from '../../types/messaging.types';
import { IOutboxEvent, OutboxEvent } from './outbox.schema';

const DEFAULT_POLL_INTERVAL_MS = 2000;

/** First retry waits this long; each further one doubles it. */
const BASE_BACKOFF_MS = 1000;

/**
 * Backoff stops growing here. A broker that has been down for an hour is no
 * less likely to come back in the next five minutes than in the next hour, and
 * an unbounded delay would mean an event effectively never arriving.
 */
const MAX_BACKOFF_MS = 5 * 60 * 1000;

/**
 * Nothing is ever given up on. An earlier version parked an event as `failed`
 * after a number of attempts, which was a silent drop wearing a different
 * hat — the claim query never looks at `failed` rows, so parking one deleted
 * it in all but name, which is the bug this whole mechanism exists to remove.
 *
 * The reason given for parking was head-of-line blocking, and it was wrong: a
 * failed event is already `pending` with a future retry time, so newer due
 * events are claimed in the same pass regardless. Nothing was being unblocked.
 *
 * `failed` remains in the schema as a state an operator can set by hand to
 * stop a genuinely undeliverable message. The dispatcher never sets it.
 */
const ALERT_AFTER_ATTEMPTS = 5;

/**
 * A claim older than this is treated as abandoned. The holder died mid-publish,
 * and without this the row would sit in `publishing` forever.
 */
const CLAIM_LEASE_MS = 60 * 1000;

/** How many due rows are read at a time while looking for an unblocked one. */
const CANDIDATE_BATCH = 20;

/**
 * How far to walk before giving up for this pass.
 *
 * Paging matters more than the cap. Blocked rows are themselves due — it is
 * their predecessor that is in backoff — so after an outage the front of the
 * queue is full of them, and reading a single batch would return nothing but
 * blocked rows and starve unrelated entities until those predecessors came
 * due. The walk continues past them; the cap only stops one tick turning into
 * a scan of an enormous backlog.
 */
const MAX_CANDIDATE_SCAN = 500;

function backoffFor(attempts: number): Date {
  const delay = Math.min(BASE_BACKOFF_MS * 2 ** attempts, MAX_BACKOFF_MS);
  return new Date(Date.now() + delay);
}

/**
 * Delivers outbox events to the broker, one at a time and oldest first.
 *
 * Sequential on purpose. These events describe a sequence of changes to the
 * same entities — a user added, then updated, then deleted — and publishing
 * them in parallel would let the graph receive them out of order. The volume is
 * low enough that doing one at a time costs nothing worth having.
 *
 * Claiming is a single atomic update, so several application instances can run
 * this at once without two of them sending the same event.
 */
export class OutboxDispatcher {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private stopped = false;

  constructor(
    private readonly producer: IMessageProducer,
    private readonly logger: Logger,
    private readonly pollIntervalMs: number = DEFAULT_POLL_INTERVAL_MS,
  ) {}

  start(): void {
    if (this.timer) return;
    this.stopped = false;
    this.timer = setInterval(() => {
      void this.drain();
    }, this.pollIntervalMs);
    // Long enough between ticks that holding the event loop open would delay
    // shutdown for no reason.
    this.timer.unref();
    this.logger.info('Outbox dispatcher started', {
      pollIntervalMs: this.pollIntervalMs,
    });
  }

  /**
   * Stops accepting work and waits for the pass in flight.
   *
   * The wait matters because the caller disposes the containers next, and one
   * of them disconnects the very producer a running pass is publishing
   * through. Returning early would turn an ordinary shutdown into a failed
   * publish and a retry on the next boot.
   */
  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    this.logger.info('Outbox dispatcher stopped');
  }

  /**
   * Publishes everything currently due. Exposed so a test can drive a tick
   * directly instead of waiting on the timer.
   */
  async drain(): Promise<number> {
    // One pass at a time: a slow broker must not have ticks pile up behind it.
    if (this.running) return 0;
    this.running = true;
    let delivered = 0;
    try {
      for (;;) {
        if (this.stopped) break;
        const event = await this.claimNext();
        if (!event) break;
        const sent = await this.deliver(event);
        if (sent) delivered += 1;
      }
    } catch (error) {
      // The loop itself failing (the database is unreachable, say) is not the
      // same as an event failing, and must not stop future ticks.
      this.logger.error('Outbox dispatcher pass failed', {
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.running = false;
    }
    return delivered;
  }

  /**
   * Takes the oldest due event that is free to go, and marks it as ours.
   * Atomic, so two instances cannot take the same row.
   *
   * "Free to go" means nothing older about the same entity is still
   * undelivered. Without that check the order this class claims to keep does
   * not survive a failure: a failed event goes back to `pending` with a future
   * retry time, and the next pass would happily deliver a later event about
   * the same user — an update, or a deletion — ahead of it.
   *
   * Candidates are considered oldest first and the first unblocked one is
   * taken, so one stuck entity delays only its own events.
   */
  private async claimNext(): Promise<IOutboxEvent | null> {
    const now = new Date();
    const due = {
      $or: [
        { status: 'pending', nextAttemptAt: { $lte: now } },
        {
          status: 'publishing',
          claimedAt: { $lte: new Date(now.getTime() - CLAIM_LEASE_MS) },
        },
      ],
    };

    for (let skip = 0; skip < MAX_CANDIDATE_SCAN; skip += CANDIDATE_BATCH) {
      const candidates = await OutboxEvent.find(due)
        .sort({ createdAt: 1 })
        .skip(skip)
        .limit(CANDIDATE_BATCH)
        .exec();

      if (candidates.length === 0) return null;

      for (const candidate of candidates) {
        const blocked = await OutboxEvent.exists({
          orderingKey: candidate.orderingKey,
          createdAt: { $lt: candidate.createdAt },
          status: { $in: ['pending', 'publishing', 'failed'] },
          _id: { $ne: candidate._id },
        });
        if (blocked) continue;

        // The whole due condition is re-asserted here, not just the id and
        // status. Matching on status alone would let two instances take the
        // same abandoned row: the first refreshes claimedAt but leaves the
        // status at `publishing`, so the second update would still match and
        // both would publish the same event.
        const claimed = await OutboxEvent.findOneAndUpdate(
          { _id: candidate._id, ...due },
          { $set: { status: 'publishing', claimedAt: now } },
          { new: true },
        ).exec();
        if (claimed) return claimed;
      }

      if (candidates.length < CANDIDATE_BATCH) return null;
    }
    return null;
  }

  private async deliver(event: IOutboxEvent): Promise<boolean> {
    const message: StreamMessage<string> = {
      key: event.key,
      value: event.value,
      headers: event.headers,
    };

    try {
      if (!this.producer.isConnected()) {
        await this.producer.connect();
      }
      await this.producer.publish(event.topic, message);
    } catch (error) {
      await this.recordFailure(event, error);
      return false;
    }

    await OutboxEvent.updateOne(
      { _id: event._id },
      {
        $set: { status: 'published', publishedAt: new Date() },
        $unset: { claimedAt: '', lastError: '' },
      },
    ).exec();
    return true;
  }

  private async recordFailure(
    event: IOutboxEvent,
    error: unknown,
  ): Promise<void> {
    const attempts = event.attempts + 1;
    const message = error instanceof Error ? error.message : String(error);

    await OutboxEvent.updateOne(
      { _id: event._id },
      {
        $set: {
          status: 'pending',
          attempts,
          nextAttemptAt: backoffFor(attempts),
          lastError: message,
        },
        $unset: { claimedAt: '' },
      },
    ).exec();

    // Quiet while it still looks like a blip, loud once it does not. This is
    // the visibility the old code lacked: a failure here is on the record and
    // can be alerted on, rather than swallowed behind a success.
    const detail = {
      outboxId: String(event._id),
      topic: event.topic,
      key: event.key,
      attempts,
      error: message,
    };
    if (attempts >= ALERT_AFTER_ATTEMPTS) {
      this.logger.error(
        'Outbox event still failing to publish; it will keep retrying',
        detail,
      );
    } else {
      this.logger.warn('Outbox event failed to publish; will retry', detail);
    }
  }
}

/** Counts by status, for a health endpoint or a metric. */
export async function outboxBacklog(): Promise<Record<OutboxCountKey, number>> {
  const rows = await OutboxEvent.aggregate<{ _id: string; count: number }>([
    { $group: { _id: '$status', count: { $sum: 1 } } },
  ]);
  const counts: Record<OutboxCountKey, number> = {
    pending: 0,
    publishing: 0,
    published: 0,
    failed: 0,
  };
  for (const row of rows) {
    if (row._id in counts) {
      counts[row._id as OutboxCountKey] = row.count;
    }
  }
  return counts;
}

export type OutboxCountKey = 'pending' | 'publishing' | 'published' | 'failed';
