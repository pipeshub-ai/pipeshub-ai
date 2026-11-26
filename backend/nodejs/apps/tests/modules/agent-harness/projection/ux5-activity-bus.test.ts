/**
 * UX5 EV-2, MU-4: Node reads the activity bus exactly as the Python publisher
 * writes it (the shared wire vector), on its own key, and the chat's LiveBus
 * never accepts an activity frame (nor the activity bus a chat delta).
 */
import fs from 'fs';
import path from 'path';
import { expect } from 'chai';
import type { IRedisConnectionProvider } from '../../../../src/libs/services/redis/connectionProvider.interface';
import {
  channelNotifyChannel,
  channelStreamKey,
  decodeChannelFields,
  decodeLiveFields,
  RedisLiveBus,
} from '../../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { InMemoryLiveBus } from '../../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import {
  ACTIVITY_LIVE_CHANNEL,
  ActivityFrame,
} from '../../../../src/modules/agent-harness/projection/activity';
import { CHAT_LIVE_CHANNEL, LiveDelta } from '../../../../src/modules/agent-harness/projection/frames';

const VECTORS = path.join(__dirname, '..', '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors');

interface Vector<F> {
  keys: Array<{ runId: string; namespace: string; key: string; notifyChannel: string }>;
  frames: Array<{ frame: F; fields: Record<string, string> }>;
  rejected: Array<{ fields: Record<string, string> }>;
}

const read = <F>(name: string): Vector<F> =>
  JSON.parse(fs.readFileSync(path.join(VECTORS, name), 'utf8')) as Vector<F>;
const activity = () => read<ActivityFrame>('activity-frames.json');
const chat = () => read<LiveDelta>('livebus-frames.json');
const flat = (fields: Record<string, string>): string[] => Object.entries(fields).flat();

/** Redis streams in memory: what `read` asks of them. */
function streams(entries: Record<string, Array<Record<string, string>>>): IRedisConnectionProvider {
  const client = {
    xrange: (key: string) =>
      Promise.resolve((entries[key] ?? []).map((fields, i) => [`${String(i + 1)}-0`, flat(fields)])),
  };
  return { getClient: () => client, keyNamespace: 'tenant-a' } as unknown as IRedisConnectionProvider;
}

describe('agent-harness activity bus (UX5 EV-2)', () => {
  it('names the run\'s activity stream and notify channel as the vector does, with and without a namespace', () => {
    const doc = activity();
    expect(doc.keys.map((k) => k.namespace)).to.include.members(['', 'tenant-a']);
    for (const key of doc.keys) {
      expect(channelStreamKey(ACTIVITY_LIVE_CHANNEL, key.runId, key.namespace)).to.equal(key.key);
      expect(channelNotifyChannel(ACTIVITY_LIVE_CHANNEL, key.runId, key.namespace)).to.equal(key.notifyChannel);
    }
  });

  it('decodes every frame of the vector, as flat Redis fields and as a record', () => {
    const doc = activity();
    expect(doc.frames.map((c) => c.frame.kind)).to.include.members(['command_started', 'command_output', 'command_finished']);
    for (const case_ of doc.frames) {
      expect(decodeChannelFields(ACTIVITY_LIVE_CHANNEL, case_.frame.runId, flat(case_.fields))).to.deep.equal(case_.frame);
      expect(decodeChannelFields(ACTIVITY_LIVE_CHANNEL, case_.frame.runId, case_.fields)).to.deep.equal(case_.frame);
    }
  });

  it('refuses every rejected entry of the vector', () => {
    const doc = activity();
    expect(doc.rejected).to.not.be.empty;
    for (const rejected of doc.rejected) {
      expect(decodeChannelFields(ACTIVITY_LIVE_CHANNEL, 'run_1', rejected.fields), JSON.stringify(rejected.fields)).to.equal(null);
    }
  });

  it('MU-4: the chat bus accepts no activity frame, and the activity bus no chat delta', () => {
    for (const case_ of activity().frames) {
      expect(decodeLiveFields(case_.frame.runId, case_.fields), case_.frame.kind).to.equal(null);
    }
    for (const case_ of chat().frames) {
      expect(decodeChannelFields(ACTIVITY_LIVE_CHANNEL, case_.frame.runId, case_.fields), case_.frame.kind).to.equal(null);
    }
  });

  it('MU-4: each Redis reader reads only its own key, and the chat key keeps its name', async () => {
    const [frame] = activity().frames;
    const [delta] = chat().frames;
    const runId = frame!.frame.runId;
    const provider = streams({
      [`tenant-a:harness:activity:${runId}`]: [frame!.fields, delta!.fields],
      [`tenant-a:harness:live:${runId}`]: [delta!.fields, frame!.fields],
    });
    const activityBus = new RedisLiveBus<ActivityFrame>(provider, 5, ACTIVITY_LIVE_CHANNEL);
    expect((await activityBus.read(runId)).deltas).to.deep.equal([frame!.frame]);
    const chatBus = new RedisLiveBus(provider, 5);
    expect((await chatBus.read(runId)).deltas.map((d) => d.kind)).to.deep.equal([delta!.frame.kind]);
    expect(channelStreamKey(CHAT_LIVE_CHANNEL, runId, 'tenant-a')).to.equal(`tenant-a:harness:live:${runId}`);
  });

  it('MU-4: the in-memory chat bus keeps no activity frame, and the in-memory activity bus no chat delta', async () => {
    const [frame] = activity().frames;
    const [delta] = chat().frames;
    const chatBus = new InMemoryLiveBus();
    chatBus.publish(frame!.frame as unknown as LiveDelta);
    expect((await chatBus.read(frame!.frame.runId)).deltas).to.deep.equal([]);
    const activityBus = new InMemoryLiveBus<ActivityFrame>(4_000, ACTIVITY_LIVE_CHANNEL);
    activityBus.publish(delta!.frame as unknown as ActivityFrame);
    activityBus.publish(frame!.frame);
    expect((await activityBus.read(frame!.frame.runId)).deltas).to.deep.equal([frame!.frame]);
  });
});
