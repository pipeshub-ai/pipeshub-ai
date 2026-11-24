/**
 * The durable consumer inbox (`platform.consumer_inbox`) for the Node consumers; the Python twin is
 * `app/services/messaging/inbox.py` and both write the same rows.
 *
 * A row is (org, `<len>:<group>:<event id>`): every read and write runs as the message's org, so row security keeps one
 * org from learning which ids another processed, and two groups keep their own rows. A consumer skips an event its group
 * already finished and records one only after its handler succeeded, so a redelivery (a lost acknowledgement, a rebalance,
 * a restart, an expired claim) or a replayed message runs once and a failed handler is retried. A handler whose effect is
 * one PostgreSQL unit of work records in that unit (`recordWith`), so the effect and the record commit together. Otherwise
 * the record follows the effect, and a handler that finished but whose record was lost runs again on redelivery.
 *
 * The TTL sweeper deletes rows older than the replay window as the jobs role.
 */
import { forOrg } from '../context/orgContext';
import { DuplicateEnvelope, Inbox, inboxFailed } from '../context/envelope';
import { Tx, withUow } from '../db';
import { mapPgStoreError } from '../repositories/pg';

const SEEN = `/* pipeshub:platform.consumer_inbox_seen */ SELECT EXISTS
  (SELECT 1 FROM platform.consumer_inbox WHERE org_id = $1 AND message_id = $2) AS seen`;
const RECORD = `/* pipeshub:platform.consumer_inbox_record */ INSERT INTO platform.consumer_inbox (org_id, message_id)
  VALUES ($1, $2) ON CONFLICT DO NOTHING`;

/** The row's id: the group's name carries its length, so no other (group, event id) pair can spell the same id. */
export function inboxMessageId(consumer: string, eventId: string): string {
  return `${consumer.length}:${consumer}:${eventId}`;
}

/** False when the row was already there. */
async function insert(
  tx: Tx,
  consumer: string,
  eventId: string,
  orgId: string,
): Promise<boolean> {
  const r = await tx.query(RECORD, [orgId, inboxMessageId(consumer, eventId)]);
  return r.rowCount === 1;
}

export class PgInbox implements Inbox {
  seen(consumer: string, eventId: string, orgId: string): Promise<boolean> {
    return forOrg(orgId, 'consumer-inbox', () =>
      withUow({}, async (tx) => {
        const r = await tx.query<{ seen: boolean }>(SEEN, [
          orgId,
          inboxMessageId(consumer, eventId),
        ]);
        return r.rows[0]?.seen === true;
      }),
    );
  }

  async record(
    consumer: string,
    eventId: string,
    orgId: string,
  ): Promise<void> {
    await forOrg(orgId, 'consumer-inbox', () =>
      withUow({ write: true }, (tx) => insert(tx, consumer, eventId, orgId)),
    );
  }

  /**
   * The record goes first: a second delivery of the event waits on its key until this unit ends, then finds it and rolls
   * back. A failed record (in a savepoint) leaves the effect to run unrecorded, as if there were no inbox. The unit is the
   * effect's own, so its failures are mapped as the store's unit of work maps them: a fence refusal or a timeout stays a
   * retryable store error, and the consumer leaves the message to be delivered again.
   */
  async recordWith<T>(
    consumer: string,
    eventId: string,
    orgId: string,
    fn: () => Promise<T>,
  ): Promise<T> {
    try {
      return await withUow({ write: true }, async (tx) => {
        let fresh = true;
        try {
          fresh = await tx.savepoint('consumer_inbox', () =>
            insert(tx, consumer, eventId, orgId),
          );
        } catch (e) {
          inboxFailed('record', consumer, e);
        }
        if (!fresh) throw new DuplicateEnvelope(eventId);
        return fn();
      });
    } catch (error) {
      throw mapPgStoreError(error);
    }
  }
}
