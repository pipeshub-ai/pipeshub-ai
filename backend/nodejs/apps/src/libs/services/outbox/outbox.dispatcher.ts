import { spawnDetached } from '../../context/orgContext';
import { Logger } from '../logger.service';
import { IMessageProducer, StreamMessage } from '../../types/messaging.types';
import { getOutboxRepository } from '../../repositories/outbox';
import type { OutboxEventRow, OutboxStatus } from '../../repositories/outbox';

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
    // the timer outlives the caller: a tick must not carry whichever org was bound when it was created
    this.timer = spawnDetached(() =>
      setInterval(() => {
        void this.drain();
      }, this.pollIntervalMs),
    );
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
  private claimNext(): Promise<OutboxEventRow | null> {
    return getOutboxRepository().claimNext({
      leaseMs: CLAIM_LEASE_MS,
      candidateBatch: CANDIDATE_BATCH,
      maxScan: MAX_CANDIDATE_SCAN,
    });
  }

  private async deliver(event: OutboxEventRow): Promise<boolean> {
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

    await getOutboxRepository().markPublished(event._id);
    return true;
  }

  private async recordFailure(
    event: OutboxEventRow,
    error: unknown,
  ): Promise<void> {
    const attempts = event.attempts + 1;
    const message = error instanceof Error ? error.message : String(error);

    await getOutboxRepository().markFailed(event._id, {
      attempts,
      nextAttemptAt: backoffFor(attempts),
      lastError: message,
    });

    // Quiet while it still looks like a blip, loud once it does not. This is
    // the visibility the old code lacked: a failure here is on the record and
    // can be alerted on, rather than swallowed behind a success.
    const detail = {
      outboxId: event._id,
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
export function outboxBacklog(): Promise<Record<OutboxCountKey, number>> {
  return getOutboxRepository().statusCounts();
}

export type OutboxCountKey = OutboxStatus;
