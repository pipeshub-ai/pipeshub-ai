/**
 * QA18-7 (VS-10): an open tab (this frontend) reading a stream from a newer Node.
 *
 * Node's run streams are additive: a newer release may send frame types, CUSTOM
 * names and fields this tab does not know. Each golden stream Node projected
 * (`contracts/harness/vectors/journals/*.frames.json`) is played as recorded and
 * again with such additions; the tab must call back exactly as it did for the
 * recorded stream (the added field set aside where a known frame passes it on).
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import type { SSEEvent } from '@/lib/api';
import { completedRun, markedRestartRun, restartedRun, type DurableFrame } from './durable-frames';

const JOURNALS = join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const ADDED = 'addedInNPlus1';

interface Recorded {
  id: string;
  frame: Record<string, unknown>;
}

type Stored = Recorded | Record<string, unknown>;

/** The vectors hold `{id, frame}` (with the SSE id Node sent) or the bare frame (ids then by position). */
const recorded = (stored: Stored, index: number): Recorded =>
  'frame' in stored && typeof stored.frame === 'object'
    ? (stored as Recorded)
    : { id: `j:${index + 1}.1/0.0.0`, frame: stored as Record<string, unknown> };

const built = (frames: DurableFrame[]): Recorded[] =>
  frames.map(({ id, data }) => ({ id, frame: data as Record<string, unknown> }));

// Restart scenarios (F-8) the vectors do not hold: the tab's replacement of an ended message spans two frames.
const scenarios = [
  { name: 'f8_completed', frames: built(completedRun()) },
  { name: 'f8_restarted', frames: built(restartedRun()) },
  ...(['adjacent', 'gap', 'no_end'] as const).map((shape) => ({
    name: `f8_marked_restart_${shape}`,
    frames: built(markedRestartRun(shape)),
  })),
];

const vectors = readdirSync(JOURNALS)
  .filter((name) => name.endsWith('.frames.json'))
  .sort()
  .map((name) => ({
    name: name.replace('.frames.json', ''),
    frames: (JSON.parse(readFileSync(join(JOURNALS, name), 'utf8')) as { frames: Stored[] }).frames.map(recorded),
  }));

const golden = [...vectors, ...scenarios];

const event = ({ id, frame }: Recorded): SSEEvent => ({ id, event: String(frame.type), data: frame }) as SSEEvent;

const withoutAdded = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(withoutAdded);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([key]) => key !== ADDED)
        .map(([key, inner]) => [key, withoutAdded(inner)]),
    );
  }
  return value;
};

/** Every callback the tab makes, in order, with what it passed. */
function play(frames: Recorded[]): unknown[] {
  const calls: unknown[] = [];
  const callbacks = new Proxy({} as StreamMessageCallbacks, {
    get: (_target, name) => (...args: unknown[]) => {
      calls.push([String(name), withoutAdded(JSON.parse(JSON.stringify(args ?? null)))]);
    },
  });
  const handle = createAGUIEventHandler(callbacks, undefined, { replaceRestartedMessages: true });
  frames.map(event).forEach(handle);
  return calls;
}

const addField = (value: unknown): unknown =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? { ...(value as Record<string, unknown>), [ADDED]: { note: 'newer' } }
    : value;

/** A newer Node's fields on every frame, and on every CUSTOM value and STATE_SNAPSHOT snapshot. */
const newerFields = (frames: Recorded[]): Recorded[] =>
  frames.map(({ id, frame }) => {
    const out = addField(frame) as Record<string, unknown>;
    if ('value' in frame) out.value = addField(frame.value);
    if ('snapshot' in frame) out.snapshot = addField(frame.snapshot);
    return { id, frame: out };
  });

/** A newer Node's frames after every recorded one: a frame type and a CUSTOM name this tab does not know. */
const newerFrames = (frames: Recorded[]): Recorded[] =>
  frames.flatMap((recorded, index) => [
    recorded,
    { id: `${recorded.id}+t${index}`, frame: { type: 'ACTIVITY_DELTA', runId: 'r', delta: [{ op: 'add' }] } },
    { id: `${recorded.id}+c${index}`, frame: { type: 'CUSTOM', name: 'x_newer_panel', value: { open: true } } },
  ]);

describe('QA18-7: this tab on a newer Node\'s stream', () => {
  it('has golden streams to play', () => {
    expect(vectors.length).toBeGreaterThanOrEqual(4);
  });

  describe.each(golden)('$name', ({ frames }) => {
    const asRecorded = play(frames);

    it('ignores fields it does not know on every frame', () => {
      expect(play(newerFields(frames))).toEqual(asRecorded);
    });

    it('ignores frame types and CUSTOM names it does not know, wherever they arrive', () => {
      expect(play(newerFrames(frames))).toEqual(asRecorded);
    });
  });
});
