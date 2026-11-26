/**
 * The AG-UI handler on durable streams (S7): a message the server restarts
 * under a new id replaces the ended one (F-8), and a checkpoint taken at any
 * frame resumes to the same result as an uninterrupted stream (F-2, F-7).
 */
import { describe, it, expect, vi } from 'vitest';
import {
  createAGUIEventHandler,
  type AGUIHandlerCheckpoint,
  type AGUIHandlerOptions,
} from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import type { MessagePart, SSEAnswerChunkEvent } from '../types';
import { ANSWER, completedRun, markedRestartRun, restartedRun, type DurableFrame } from './durable-frames';

interface View {
  parts: MessagePart[];
  accumulated: string;
  completed: number;
}

function viewer(): { callbacks: StreamMessageCallbacks; view: View } {
  const view: View = { parts: [], accumulated: '', completed: 0 };
  const callbacks: StreamMessageCallbacks = {
    onParts: (parts) => {
      view.parts = JSON.parse(JSON.stringify(parts)) as MessagePart[];
    },
    onChunk: (data: SSEAnswerChunkEvent) => {
      view.accumulated = data.accumulated;
    },
    onComplete: () => {
      view.completed += 1;
    },
  };
  return { callbacks, view };
}

function play(frames: DurableFrame[], options: AGUIHandlerOptions = {}): View {
  const { callbacks, view } = viewer();
  const handle = createAGUIEventHandler(callbacks, undefined, { replaceRestartedMessages: true, ...options });
  frames.forEach(handle);
  return view;
}

const rootText = (parts: MessagePart[]) =>
  parts.filter((part) => part.type === 'text').map((part) => part.content);

describe('durable AG-UI handler — message restarted under a new id (F-8)', () => {
  it('replaces the abandoned message instead of keeping it as narration', () => {
    const onMessageRestarted = vi.fn();
    const view = play(restartedRun(), { onMessageRestarted });

    expect(rootText(view.parts)).toEqual(['Let me search.', ANSWER]);
    expect(view.parts.filter((part) => part.settled)).toHaveLength(1);
    expect(view.accumulated).toBe(ANSWER);
    expect(onMessageRestarted).toHaveBeenCalledWith('msg_b', 'msg_b_restart', 'order');
    expect(view.completed).toBe(1);
  });

  it('ends in the same view as a run that never restarted', () => {
    const restarted = play(restartedRun());
    const clean = play(completedRun());
    expect(restarted.parts).toEqual(clean.parts);
    expect(restarted.accumulated).toBe(clean.accumulated);
  });

  it('still settles narration when a tool call follows the ended message', () => {
    const onMessageRestarted = vi.fn();
    play(completedRun(), { onMessageRestarted });
    expect(onMessageRestarted).not.toHaveBeenCalled();
  });

  it('keeps the legacy behaviour when replacement is off', () => {
    const { callbacks, view } = viewer();
    const handle = createAGUIEventHandler(callbacks);
    restartedRun().forEach(handle);
    expect(rootText(view.parts)).toEqual(['Let me search.', 'The answer is 4', ANSWER]);
  });
});

describe('durable AG-UI handler — restart marked by the server (MR-2, PA-11)', () => {
  for (const shape of ['adjacent', 'gap', 'no_end'] as const) {
    it(`replaces the message the marker names (${shape})`, () => {
      const onMessageRestarted = vi.fn();
      const view = play(markedRestartRun(shape), { onMessageRestarted });
      const clean = play(completedRun());

      expect(rootText(view.parts)).toEqual(['Let me search.', ANSWER]);
      expect(view.parts).toEqual(clean.parts);
      expect(view.accumulated).toBe(ANSWER);
      expect(onMessageRestarted).toHaveBeenCalledTimes(1);
      expect(onMessageRestarted).toHaveBeenCalledWith('msg_b', 'msg_b_restart', 'marker');
    });
  }

  it('falls back to frame order for a stream without the marker', () => {
    const onMessageRestarted = vi.fn();
    const view = play(restartedRun(), { onMessageRestarted });
    expect(rootText(view.parts)).toEqual(['Let me search.', ANSWER]);
    expect(onMessageRestarted).toHaveBeenCalledWith('msg_b', 'msg_b_restart', 'order');
  });
});

describe('durable AG-UI handler — checkpoint and resume (F-2, F-7)', () => {
  const runs: Array<[string, () => DurableFrame[]]> = [
    ['completed run', completedRun],
    ['restarted run', restartedRun],
    ['marked restart with a frame between', () => markedRestartRun('gap')],
    ['marked restart without an end', () => markedRestartRun('no_end')],
  ];

  for (const [name, frames] of runs) {
    it(`resumes a ${name} from a checkpoint at every frame with no duplicate or missing text`, () => {
      const all = frames();
      const expected = play(all);
      for (let cut = 0; cut < all.length; cut += 1) {
        const first = viewer();
        const before = createAGUIEventHandler(first.callbacks, undefined, { replaceRestartedMessages: true });
        all.slice(0, cut).forEach(before);
        const saved = JSON.parse(JSON.stringify(before.checkpoint())) as AGUIHandlerCheckpoint;

        const second = viewer();
        const after = createAGUIEventHandler(second.callbacks, undefined, {
          replaceRestartedMessages: true,
          resumeFrom: saved,
        });
        after.publishView();
        if (cut > 0) {
          expect(second.view.parts, `restored parts at cut ${cut}`).toEqual(first.view.parts);
        }
        all.slice(cut).forEach(after);

        expect(second.view.parts, `parts at cut ${cut}`).toEqual(expected.parts);
        expect(second.view.accumulated, `answer at cut ${cut}`).toBe(expected.accumulated);
        expect(second.view.completed, `completion at cut ${cut}`).toBe(1);
      }
    });
  }

  it('closes a tool call that was running when the checkpoint was taken', () => {
    const all = completedRun();
    const cut = all.findIndex((frame) => frame.event === 'TOOL_CALL_END');
    const first = viewer();
    const before = createAGUIEventHandler(first.callbacks, undefined, { replaceRestartedMessages: true });
    all.slice(0, cut).forEach(before);
    expect(first.view.parts.find((part) => part.type === 'tool_call')?.status).toBe('running');

    const second = viewer();
    const after = createAGUIEventHandler(second.callbacks, undefined, {
      replaceRestartedMessages: true,
      resumeFrom: JSON.parse(JSON.stringify(before.checkpoint())) as AGUIHandlerCheckpoint,
    });
    all.slice(cut).forEach(after);
    expect(second.view.parts.find((part) => part.type === 'tool_call')?.status).toBe('success');
  });
});
