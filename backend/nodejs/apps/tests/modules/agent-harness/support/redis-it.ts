/**
 * Real Redis for the harness itests (REDIS_IT_URL). Each rig gets a key
 * namespace of its own, the deployment's tenant prefix, so a shared server
 * keeps other runs' keys apart and cleanup deletes only this rig's keys.
 */
import { randomUUID } from 'node:crypto';
import net from 'node:net';
import { AddressInfo } from 'node:net';
import { Redis } from 'ioredis';
import { RedisConnectionProviderFactory } from '../../../../src/libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../../src/libs/services/redis/connectionConfig';
import { IRedisConnectionProvider } from '../../../../src/libs/services/redis/connectionProvider.interface';

export const redisItUrl = (): URL => {
  const raw = process.env.REDIS_IT_URL;
  if (!raw) throw new Error('REDIS_IT_URL must be set for the harness Redis itests');
  return new URL(raw);
};

const providerAt = (host: string, port: number, keyNamespace: string): IRedisConnectionProvider =>
  RedisConnectionProviderFactory.create(
    { ...redisConnectionConfigFromHostPort({ host, port }), keyNamespace },
    'standalone',
  );

export interface RedisItRig {
  provider: IRedisConnectionProvider;
  /** Deletes every key under the rig's namespace and closes its connections. */
  dispose(): Promise<void>;
}

export function redisItRig(label: string): RedisItRig {
  const url = redisItUrl();
  const namespace = `pxh-it-${label}-${randomUUID()}`;
  const provider = providerAt(url.hostname, Number(url.port || 6379), namespace);
  return {
    provider,
    dispose: async () => {
      const keys: string[] = [];
      for await (const key of provider.scanKeys(`${namespace}:*`)) keys.push(key);
      if (keys.length > 0) await provider.getClient().del(...keys);
      await provider.close();
    },
  };
}

/**
 * A Redis the adapter cannot reach: the server drops every connection, and the
 * clients give up instead of reconnecting forever, so a command fails at once.
 * Only what the harness adapters call (`getClient`, `createPubSubClient`,
 * `keyNamespace`) is served.
 */
export async function unreachableRedis(): Promise<RedisItRig> {
  const server = net.createServer((socket) => socket.destroy());
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const clients: Redis[] = [];
  const client = (): Redis => {
    const created = new Redis({ host: '127.0.0.1', port, retryStrategy: () => null, maxRetriesPerRequest: 0 });
    created.on('error', () => undefined);
    clients.push(created);
    return created;
  };
  const shared = client();
  const provider = {
    keyNamespace: `pxh-it-down-${randomUUID()}`,
    getClient: () => shared,
    createPubSubClient: client,
  } as unknown as IRedisConnectionProvider;
  return {
    provider,
    dispose: async () => {
      for (const created of clients) created.disconnect();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

const END_OF_RECORDING = '\u0000end-of-recording';

export interface ChannelRecorder {
  /** Every message published on the channel so far, in order; waits for none. */
  drain(): Promise<string[]>;
  close(): void;
}

/**
 * What was published on `channel` through `provider`'s own client. Draining
 * publishes a marker on that same connection, which Redis delivers after
 * everything published before it, so a count needs no waiting on time.
 */
export async function recordChannel(
  provider: IRedisConnectionProvider,
  channel: string,
): Promise<ChannelRecorder> {
  const subscriber: Redis = provider.createPubSubClient();
  const seen: string[] = [];
  let ended: (() => void) | null = null;
  subscriber.on('message', (from: string, message: string) => {
    if (from !== channel) return;
    if (message === END_OF_RECORDING) {
      ended?.();
      return;
    }
    seen.push(message);
  });
  await subscriber.subscribe(channel);
  return {
    drain: async () => {
      const marker = new Promise<void>((resolve) => {
        ended = resolve;
      });
      await provider.getClient().publish(channel, END_OF_RECORDING);
      await marker;
      return [...seen];
    },
    close: () => subscriber.disconnect(),
  };
}
