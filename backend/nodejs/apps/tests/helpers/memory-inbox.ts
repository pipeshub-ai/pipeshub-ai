import { DuplicateEnvelope, Inbox } from '../../src/libs/context/envelope';

/** A memory inbox with the PostgreSQL inbox's rules: rows per (org, group, event id); recordWith refuses a recorded event. */
export class MemInbox implements Inbox {
  rows = new Set<string>();
  failSeen = false;
  failRecord = false;
  calls: string[] = [];
  private key = (c: string, e: string, o: string): string => `${o}|${c}|${e}`;

  async seen(c: string, e: string, o: string): Promise<boolean> {
    this.calls.push('seen');
    if (this.failSeen) throw new Error('inbox down');
    return this.rows.has(this.key(c, e, o));
  }

  async record(c: string, e: string, o: string): Promise<void> {
    this.calls.push('record');
    if (this.failRecord) throw new Error('inbox down');
    this.rows.add(this.key(c, e, o));
  }

  async recordWith<R>(c: string, e: string, o: string, fn: () => Promise<R>): Promise<R> {
    this.calls.push('recordWith');
    const k = this.key(c, e, o);
    if (this.rows.has(k)) throw new DuplicateEnvelope(e);
    this.rows.add(k);
    try {
      return await fn();
    } catch (err) {
      // PostgreSQL rolls the record back with the failed effect, so a retry must not be skipped
      this.rows.delete(k);
      throw err;
    }
  }
}
