import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '@/lib/api';
import { createAGUIEventHandler, HIDDEN_STREAM_FRAMES } from '../agui-event-handler';
import { reduceActivity, toActivityEvent } from '../activity';
import type { StreamMessageCallbacks } from '../api';

/**
 * UX-01 (C10.01, WP-18): every frame the durable harness can send the chat (`ui-stream-events.json`, which Node
 * checks it sends nothing outside of) is either shown, feeds what is shown, or is hidden on purpose with a
 * reason. A new frame in the contract fails here until the chat handles it or names why it hides it.
 */

type Entry = { description: string; example?: Record<string, unknown> };
type Entries = Record<string, Entry>;
const VECTOR: {
  chat: { frames: Entries; custom: Entries; snapshotStatuses: Entries };
  activity: { events: Entries };
} = JSON.parse(
  readFileSync(
    join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'ui-stream-events.json'),
    'utf8',
  ),
);

const CALLBACKS = [
  'onConnected',
  'onStatus',
  'onChunk',
  'onComplete',
  'onArtifact',
  'onAskUserQuestion',
  'onAnswerFinal',
  'onReasoning',
  'onParts',
  'onError',
  'onApprovalRequested',
  'onApprovalResolved',
  'onInteractionWaiting',
  'onPlan',
  'onApprovalCounted',
  'onRunCost',
] as const;

/** What feeding one frame to a fresh chat handler did: the view callbacks it called, and whether its state moved. */
function feed(frame: Record<string, unknown>): { called: string[]; stateMoved: boolean } {
  const spies = Object.fromEntries(CALLBACKS.map((name) => [name, vi.fn()]));
  const handle = createAGUIEventHandler(spies as unknown as StreamMessageCallbacks);
  const before = JSON.stringify(handle.checkpoint());
  handle({ event: String(frame.type), data: frame } as SSEEvent);
  return {
    called: CALLBACKS.filter((name) => spies[name].mock.calls.length > 0),
    stateMoved: JSON.stringify(handle.checkpoint()) !== before,
  };
}

const withStatus = (status: string): Record<string, unknown> => {
  const example = VECTOR.chat.frames.STATE_SNAPSHOT.example as { snapshot: Record<string, unknown> };
  return { ...example, snapshot: { ...example.snapshot, status } };
};

/** Every chat stream frame of the contract, under the key HIDDEN_STREAM_FRAMES uses. */
const chatFrames = (): Array<[string, Record<string, unknown>]> => [
  ...Object.entries(VECTOR.chat.frames)
    .filter(([type]) => type !== 'CUSTOM')
    .map(([type, entry]): [string, Record<string, unknown>] => [type, entry.example ?? {}]),
  ...Object.entries(VECTOR.chat.custom).map(([name, entry]): [string, Record<string, unknown>] => [
    `CUSTOM:${name}`,
    entry.example ?? {},
  ]),
  ...Object.keys(VECTOR.chat.snapshotStatuses).map((status): [string, Record<string, unknown>] => [
    `STATE_SNAPSHOT:${status}`,
    withStatus(status),
  ]),
];

describe('UX-01: the chat renders every harness stream frame or hides it on purpose', () => {
  it('hides only frames of the contract, each with a reason', () => {
    const keys = new Set(chatFrames().map(([key]) => key));
    for (const [key, reason] of Object.entries(HIDDEN_STREAM_FRAMES)) {
      expect(keys.has(key), `${key} is not a frame the harness sends`).toBe(true);
      expect(reason.length, key).toBeGreaterThan(10);
    }
  });

  it.each(chatFrames())('%s is shown, feeds what is shown, or is hidden on purpose', (key, frame) => {
    const { called, stateMoved } = feed(frame);
    if (Object.prototype.hasOwnProperty.call(HIDDEN_STREAM_FRAMES, key)) {
      expect({ called, stateMoved }, `${key} is declared hidden but changed the chat`).toEqual({
        called: [],
        stateMoved: false,
      });
    } else {
      expect(called.length > 0 || stateMoved, `${key} is neither shown nor declared hidden`).toBe(true);
    }
  });

  it('the CUSTOM frame type itself is only a carrier for the named frames', () => {
    expect(VECTOR.chat.frames).toHaveProperty('CUSTOM');
    expect(Object.keys(VECTOR.chat.custom).length).toBeGreaterThan(10);
  });

  it.each(Object.entries(VECTOR.activity.events))('activity event %s changes the activity panel', (name, entry) => {
    const event = toActivityEvent(name, entry.example);
    expect(event, name).not.toBeNull();
    const snapshot = toActivityEvent('snapshot', VECTOR.activity.events.snapshot.example);
    const start = name === 'snapshot' ? null : reduceActivity(null, snapshot!);
    const next = reduceActivity(start, event!);
    expect(next, name).not.toBeNull();
    expect(JSON.stringify(next), name).not.toEqual(JSON.stringify(start));
  });
});
