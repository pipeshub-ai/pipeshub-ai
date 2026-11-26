import { bindOrgContext } from '../../../../libs/context/orgContext';
import type { Redis } from 'ioredis';
import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { carries, CHAT_LIVE_CHANNEL, LiveDelta } from '../../projection/frames';
import {
  LiveBus,
  LiveBusSubscription,
  LiveChannel,
  LiveFrame,
  RetainedDeltas,
} from '../../ports/live-bus';
import { LiveBusEraser } from '../../ports/session-erasure';

/**
 * The LiveBus on Redis Streams, reading what `app/harness/livebus.py`
 * publishes: one stream per run and channel at
 * `[<namespace>:]harness:<channel>:<runId>` (the chat's `live`, the activity
 * panel's `activity`), fields pinned by
 * `contracts/harness/vectors/livebus-frames.json` and `activity-frames.json`.
 *
 * Followers are served by one poller per process (non-blocking `XREAD` per
 * followed run every `POLL_MS`), so a thousand open streams cost no
 * connections of their own and work the same on a Redis Cluster. The
 * publisher also sends an empty message on the run's notify channel after
 * each frame; one `SUBSCRIBE` connection per process listens to the followed
 * runs' channels and reads a notified run at once, so a delta does not wait
 * for the poll. The poll stays, for a lost message or subscriber.
 */

const WIRE_VERSION = '1';
const POLL_MS = 50;
const BATCH = 500;
const START = '0-0';

type Entry = [id: string, fields: string[]];

const namespaced = (namespace: string): string =>
  namespace ? `${namespace}:` : '';

export const channelStreamKey = (
  channel: LiveChannel<LiveFrame>,
  runId: string,
  namespace = '',
): string => `${namespaced(namespace)}harness:${channel.name}:${runId}`;

export const channelNotifyChannel = (
  channel: LiveChannel<LiveFrame>,
  runId: string,
  namespace = '',
): string => `${namespaced(namespace)}harness:${channel.name}-notify:${runId}`;

export const liveStreamKey = (runId: string, namespace = ''): string =>
  channelStreamKey(CHAT_LIVE_CHANNEL, runId, namespace);

export const liveNotifyChannel = (runId: string, namespace = ''): string =>
  channelNotifyChannel(CHAT_LIVE_CHANNEL, runId, namespace);

/** A frame of `channel`, or null for another wire version, a kind it does not carry, or a malformed field. */
export function decodeChannelFields<F extends LiveFrame>(
  channel: LiveChannel<F>,
  runId: string,
  flat: readonly string[] | Record<string, string>,
): F | null {
  const fields: Record<string, string> = Array.isArray(flat)
    ? Object.fromEntries(
        Array.from({ length: flat.length / 2 }, (_, i) => [
          flat[2 * i] as string,
          flat[2 * i + 1] as string,
        ]),
      )
    : (flat as Record<string, string>);
  const kind = fields['k'] ?? '';
  if (fields['v'] !== WIRE_VERSION || !carries(channel, kind)) return null;
  const fence = Number(fields['f']);
  const modelAttempt = Number(fields['a']);
  const n = Number(fields['n']);
  if (![fence, modelAttempt, n].every((v) => Number.isInteger(v) && v >= 0)) {
    return null;
  }
  let data: unknown;
  try {
    data = JSON.parse(fields['d'] ?? '{}');
  } catch {
    return null;
  }
  if (data === null || typeof data !== 'object' || Array.isArray(data))
    return null;
  const frame: LiveFrame = {
    runId,
    fence,
    modelAttempt,
    n,
    kind,
    data: data as Record<string, unknown>,
  };
  return frame as F;
}

export const decodeLiveFields = (
  runId: string,
  flat: readonly string[] | Record<string, string>,
): LiveDelta | null => decodeChannelFields(CHAT_LIVE_CHANNEL, runId, flat);

interface Follower<F> {
  runId: string;
  cursor: string;
  onDeltas: (deltas: F[]) => void;
  onError: (error: unknown) => void;
  /** An `XREAD` is in flight; `again` asks for one more when it returns. */
  reading: boolean;
  again: boolean;
}

export class RedisLiveBus<F extends LiveFrame = LiveDelta>
  implements LiveBus<F>, LiveBusEraser
{
  private readonly followers = new Set<Follower<F>>();
  private readonly channel: LiveChannel<F>;
  private timer: NodeJS.Timeout | null = null;
  private polling = false;
  /** Null until the first follow; `false` once it could not be opened. */
  private subscriber: Redis | null | false = null;

  /** Without `channel`: the chat's token deltas, which is what `F` defaults to. */
  constructor(
    private readonly provider: IRedisConnectionProvider,
    private readonly pollMs = POLL_MS,
    channel?: LiveChannel<F>,
  ) {
    this.channel =
      channel ??
      (CHAT_LIVE_CHANNEL as LiveChannel<LiveFrame> as LiveChannel<F>);
  }

  private key(runId: string): string {
    return channelStreamKey(this.channel, runId, this.provider.keyNamespace);
  }

  private decode(runId: string, entries: Entry[]): F[] {
    const deltas: F[] = [];
    for (const [, fields] of entries) {
      const delta = decodeChannelFields(this.channel, runId, fields);
      if (delta !== null) deltas.push(delta);
    }
    return deltas;
  }

  /** One `DEL` per stream: keys of different runs may live in different cluster slots. */
  async erase(runIds: readonly string[]): Promise<number> {
    const client = this.provider.getClient();
    let erased = 0;
    for (const runId of runIds) {
      erased += await client.del(this.key(runId));
    }
    return erased;
  }

  async read(runId: string): Promise<RetainedDeltas<F>> {
    const entries = (await this.provider
      .getClient()
      .xrange(this.key(runId), '-', '+')) as Entry[];
    const last = entries[entries.length - 1];
    return {
      deltas: this.decode(runId, entries),
      cursor: last !== undefined ? last[0] : START,
    };
  }

  follow(
    runId: string,
    cursor: string,
    onDeltas: (deltas: F[]) => void,
    onError: (error: unknown) => void,
  ): LiveBusSubscription {
    // Told in the context it was registered in (the viewer's request), never in the poller's or publisher's.
    const told = bindOrgContext(onDeltas);
    const failed = bindOrgContext(onError);
    const follower: Follower<F> = {
      runId,
      cursor: cursor || START,
      onDeltas: told,
      onError: failed,
      reading: false,
      again: false,
    };
    const listening = this.followed(runId);
    this.followers.add(follower);
    if (!listening) this.listen(runId, true);
    this.schedule();
    return {
      close: () => {
        this.followers.delete(follower);
        if (!this.followed(runId)) this.listen(runId, false);
      },
    };
  }

  private followed(runId: string): boolean {
    for (const follower of this.followers) {
      if (follower.runId === runId) return true;
    }
    return false;
  }

  /** Best effort: without a subscriber the poll alone serves the run. */
  private listen(runId: string, on: boolean): void {
    const subscriber = this.notifications();
    if (subscriber === null) return;
    const channel = channelNotifyChannel(
      this.channel,
      runId,
      this.provider.keyNamespace,
    );
    const done = on
      ? subscriber.subscribe(channel)
      : subscriber.unsubscribe(channel);
    void done.catch(() => undefined);
  }

  private notifications(): Redis | null {
    if (this.subscriber === false) return null;
    if (this.subscriber !== null) return this.subscriber;
    try {
      const subscriber = this.provider.createPubSubClient();
      subscriber.on('message', (channel: string) => {
        this.notified(channel);
      });
      subscriber.on('error', () => undefined);
      this.subscriber = subscriber;
      return subscriber;
    } catch {
      this.subscriber = false;
      return null;
    }
  }

  private notified(channel: string): void {
    const prefix = channelNotifyChannel(
      this.channel,
      '',
      this.provider.keyNamespace,
    );
    if (!channel.startsWith(prefix)) return;
    const runId = channel.slice(prefix.length);
    for (const follower of this.followers) {
      if (follower.runId === runId) void this.readFollower(follower);
    }
  }

  private schedule(): void {
    if (this.timer !== null || this.followers.size === 0) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.poll();
    }, this.pollMs);
    this.timer.unref();
  }

  private async poll(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    try {
      await Promise.all(
        [...this.followers].map((follower) => this.readFollower(follower)),
      );
    } finally {
      this.polling = false;
      this.schedule();
    }
  }

  /** One follower's new entries; reads never overlap, so the cursor only moves forward. */
  private async readFollower(follower: Follower<F>): Promise<void> {
    if (follower.reading) {
      follower.again = true;
      return;
    }
    follower.reading = true;
    try {
      do {
        follower.again = false;
        const result = await this.provider
          .getClient()
          .xread(
            'COUNT',
            BATCH,
            'STREAMS',
            this.key(follower.runId),
            follower.cursor,
          );
        const entries = result?.[0]?.[1] ?? [];
        const last = entries[entries.length - 1];
        if (last === undefined || !this.followers.has(follower)) continue;
        follower.cursor = last[0];
        follower.onDeltas(this.decode(follower.runId, entries));
        // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- a concurrent readFollower sets `again` while this one awaits XREAD
      } while (follower.again && this.followers.has(follower));
    } catch (error) {
      if (this.followers.delete(follower) && !this.followed(follower.runId)) {
        this.listen(follower.runId, false);
      }
      follower.onError(error);
    } finally {
      follower.reading = false;
    }
  }
}
