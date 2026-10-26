import { Client } from 'pg';
import { Logger } from '../services/logger.service';

/**
 * The one LISTEN connection of this process (per URL). LISTEN needs a session, so it never goes through the unit of work or a
 * transaction pooler: the URL must reach PostgreSQL directly (`PG_LISTEN_URL`, or the node URL when `PG_POOLER=none`). Every channel
 * and every subscriber shares it, so the key-value store, the queue consumers and the scheduler cost one of the server's
 * `max_connections`, not one each. It reconnects with backoff and, after every (re)connect, calls each subscriber's `onReconnect`,
 * because notifications sent while it was away are lost.
 */
interface Subscription {
  channel: string;
  onNotify: (payload: string) => void;
  onReconnect?: () => void;
  onLost?: () => void;
}

export class ListenHub {
  private static hubs = new Map<string, ListenHub>();
  private client: Client | undefined;
  private subs = new Set<Subscription>();
  private channels = new Map<string, number>(); // channel -> subscribers
  private timer: NodeJS.Timeout | undefined;
  private attempt = 0;
  private lastOk = 0;
  private beat: NodeJS.Timeout | undefined;
  private connecting: Promise<void> | undefined;
  private readonly logger = Logger.getInstance({ service: 'ListenHub' });

  static forUrl(url: string): ListenHub {
    let hub = ListenHub.hubs.get(url);
    if (hub === undefined) {
      hub = new ListenHub(url);
      ListenHub.hubs.set(url, hub);
    }
    return hub;
  }

  private constructor(private readonly url: string) {}

  /** 1 while the connection is up. */
  get connections(): number {
    return this.client === undefined ? 0 : 1;
  }

  async subscribe(
    channel: string,
    onNotify: (payload: string) => void,
    onReconnect?: () => void,
    onLost?: () => void,
  ): Promise<() => Promise<void>> {
    const sub: Subscription = { channel, onNotify, onReconnect, onLost };
    this.subs.add(sub);
    const n = this.channels.get(channel) ?? 0;
    this.channels.set(channel, n + 1);
    if (this.client === undefined) {
      if (this.timer) clearTimeout(this.timer); // connecting now supersedes the pending backoff
      this.timer = undefined;
      await this.connect();
    } else if (n === 0) await this.listen(channel);
    else onReconnect?.(); // joining a live connection: re-read what may have been missed before this subscriber existed
    return async () => {
      if (!this.subs.delete(sub)) return;
      const left = (this.channels.get(channel) ?? 1) - 1;
      if (left <= 0) {
        this.channels.delete(channel);
        if (this.client)
          await this.client.query(`UNLISTEN ${channel}`).catch(() => undefined);
      } else this.channels.set(channel, left);
      if (this.subs.size === 0) await this.close(); // nothing listens any more: hold no connection
    };
  }

  private async listen(channel: string): Promise<void> {
    // channel names are constants of ours, never user input
    await this.client?.query(`LISTEN ${channel}`);
  }

  private connect(): Promise<void> {
    if (this.connecting) return this.connecting;
    this.connecting = this.doConnect().finally(() => {
      this.connecting = undefined;
    });
    return this.connecting;
  }

  private async doConnect(): Promise<void> {
    // a retry timer armed by an earlier loss can fire after an on-demand connect has already made a client: keep that one
    if (this.subs.size === 0 || this.client !== undefined) return;
    const client = new Client({
      connectionString: this.url,
      application_name: 'pipeshub-listen',
    });
    client.on('notification', (m) => {
      this.lastOk = Date.now();
      for (const s of [...this.subs]) {
        if (s.channel !== m.channel) continue;
        try {
          s.onNotify(m.payload ?? '');
        } catch (e) {
          this.logger.warn('listener callback failed', {
            error: (e as Error).message,
          });
        }
      }
    });
    client.on('error', () => {
      this.lost(client);
    });
    client.on('end', () => {
      this.lost(client);
    });
    try {
      await client.connect();
      for (const ch of this.channels.keys()) await client.query(`LISTEN ${ch}`);
    } catch (e) {
      client.removeAllListeners();
      client.on('error', () => undefined);
      await client.end().catch(() => undefined);
      this.logger.warn('LISTEN failed', { error: (e as Error).message });
      this.schedule();
      return;
    }
    if (this.subs.size === 0) {
      // the last subscriber left while this connection was being made: hold nothing
      client.removeAllListeners();
      client.on('error', () => undefined);
      await client.end().catch(() => undefined);
      return;
    }
    this.client = client;
    this.attempt = 0;
    this.lastOk = Date.now();
    this.beatSoon(client);
    for (const s of [...this.subs]) s.onReconnect?.();
  }

  private async close(): Promise<void> {
    const c = this.client;
    this.client = undefined;
    if (this.beat) clearTimeout(this.beat);
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    if (c) {
      c.removeAllListeners();
      c.on('error', () => undefined);
      await c.end().catch(() => undefined);
    }
  }

  private lost(client: Client): void {
    if (this.client !== client) return;
    this.client = undefined;
    if (this.beat) clearTimeout(this.beat);
    for (const s of [...this.subs]) s.onLost?.(); // subscribers that sleep until a notification must not trust one now
    client.removeAllListeners();
    client.on('error', () => undefined);
    void client.end().catch(() => undefined);
    this.schedule();
  }

  /**
   * A half-open connection (a NAT that forgot it, a dropped route) never says it is gone: ask it. One that does not answer is
   * dropped, which reconnects, re-LISTENs and tells every subscriber to re-read what it may have missed.
   */
  private beatSoon(client: Client): void {
    const every = Number(process.env.PG_LISTEN_PING_S ?? 10) * 1000;
    const limit = Number(process.env.PG_LISTEN_PING_TIMEOUT_S ?? 5) * 1000;
    this.beat = setTimeout(() => {
      if (this.client !== client) return;
      const timeout = new Promise<never>((_, reject) => {
        setTimeout(() => {
          reject(new Error('no answer'));
        }, limit).unref();
      });
      void Promise.race([client.query('SELECT 1'), timeout]).then(
        () => {
          this.lastOk = Date.now();
          this.beatSoon(client);
        },
        (e: unknown) => {
          this.logger.warn(
            'the LISTEN connection stopped answering; reconnecting',
            { error: (e as Error).message },
          );
          // a dead socket may never close by itself
          (
            client as unknown as {
              connection?: { stream?: { destroy(): void } };
            }
          ).connection?.stream?.destroy();
          this.lost(client);
        },
      );
    }, every);
    this.beat.unref();
  }

  /** True while the connection is up and answered (or delivered) within three pings. Trust a notification to invalidate something only then. */
  healthy(): boolean {
    const every = Number(process.env.PG_LISTEN_PING_S ?? 10) * 1000;
    const limit = Number(process.env.PG_LISTEN_PING_TIMEOUT_S ?? 5) * 1000;
    return (
      this.client !== undefined && Date.now() - this.lastOk <= 3 * every + limit
    );
  }

  private schedule(): void {
    if (this.timer || this.subs.size === 0) return;
    const delay =
      Math.min(500 * 2 ** this.attempt++, 30_000) * (0.5 + Math.random());
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.connect();
    }, delay);
    this.timer.unref();
  }
}

/** LISTEN on one channel through the shared connection. Same shape as before: `start()` then `stop()`. */
export class PgListener {
  private unsubscribe: (() => Promise<void>) | undefined;
  private starting: Promise<void> | undefined;

  constructor(
    private readonly url: string,
    private readonly channel: string,
    private readonly onNotify: (payload: string) => void,
    private readonly onReconnect?: () => void,
    private readonly onLost?: () => void,
  ) {}

  start(): Promise<void> {
    this.starting = this.subscribe();
    return this.starting;
  }

  private async subscribe(): Promise<void> {
    this.unsubscribe = await ListenHub.forUrl(this.url).subscribe(
      this.channel,
      this.onNotify,
      this.onReconnect,
      this.onLost,
    );
  }

  /** The shared connection is up and answering: a notification can be trusted to arrive. */
  healthy(): boolean {
    return ListenHub.forUrl(this.url).healthy();
  }

  async stop(): Promise<void> {
    // A stop that arrives while start() is still subscribing must wait for it: the unsubscribe function exists only afterwards, and
    // without it the closed listener would stay subscribed on the shared connection for ever.
    await this.starting?.catch(() => undefined);
    const u = this.unsubscribe;
    this.unsubscribe = undefined;
    await u?.();
  }
}

/** A direct address for LISTEN, or undefined when only a transaction pooler is configured. */
export function listenUrl(): string | undefined {
  if (process.env.PG_LISTEN_URL) return process.env.PG_LISTEN_URL;
  return process.env.PG_POOLER === 'none' ? process.env.PG_NODE_URL : undefined;
}
