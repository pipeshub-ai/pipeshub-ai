import test from 'node:test';
import * as assert from 'node:assert/strict';
import { BatchDispatcher } from '../transport/batch-dispatcher';
import type { WatchEvent } from '../watcher/replay-event-expander';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function evt(path: string): WatchEvent {
  return { type: 'CREATED', path, timestamp: Date.now(), isDirectory: false };
}

test('flush() coalesces concurrent calls onto the same in-flight promise', async () => {
  // Slow dispatch: 50ms per batch. Two concurrent flush() calls must return
  // the same promise object — without coalescing, watcher.stop() would await
  // a no-op while a different flush was mid-drain and return before the
  // buffer is empty.
  let dispatchCalls = 0;
  const d = new BatchDispatcher(async () => {
    dispatchCalls += 1;
    await sleep(50);
  }, { flushIntervalMs: 1000 });

  d.push([evt('a.txt')]);
  const p1 = d.flush();
  const p2 = d.flush();

  assert.strictEqual(p1, p2, 'concurrent flush() calls must return the same in-flight promise');
  await Promise.all([p1, p2]);
  assert.equal(dispatchCalls, 1, `expected 1 dispatch call, got ${dispatchCalls}`);
});

test('flush() drains all buffered chunks once started', async () => {
  // With my fix, _flushInner splices the buffer at start-of-flush. Anything
  // pushed during the in-flight flush goes into a new buffer cycle and gets
  // a follow-up scheduleFlush. Verify both batches eventually land.
  let dispatched: string[][] = [];
  const d = new BatchDispatcher(async (events: WatchEvent[]) => {
    dispatched.push(events.map((e) => e.path));
    await sleep(20);
  }, { flushIntervalMs: 1000 });

  d.push([evt('first.txt')]);
  const p = d.flush();
  // Push during the in-flight flush.
  await sleep(5);
  d.push([evt('second.txt')]);
  await p;

  // The first flush dispatches first.txt only. second.txt was added after the
  // splice, so it sits in buffer until the follow-up scheduleFlush fires.
  assert.deepEqual(dispatched, [['first.txt']]);
  // Wait long enough for the follow-up flush timer to fire.
  await sleep(1100);
  assert.deepEqual(
    dispatched,
    [['first.txt'], ['second.txt']],
    `second batch must land in a follow-up flush, got ${JSON.stringify(dispatched)}`,
  );
});

test('flush() returns immediately when paused or buffer is empty', async () => {
  const d = new BatchDispatcher(async () => {
    throw new Error('should not dispatch');
  }, { flushIntervalMs: 1000 });

  // Empty buffer → resolves with no dispatch.
  await d.flush();

  // Paused with content → buffer holds, no dispatch.
  d.push([evt('held.txt')]);
  d.pause();
  await d.flush();
  assert.equal(d.pending, 1, 'paused flush must keep events buffered');
});
