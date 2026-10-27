import { describe, expect, it, vi } from 'vitest';
import { createSerialSaver } from '../serial-saver';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('createSerialSaver', () => {
  it('sends one request at a time and ends on the latest value', async () => {
    const calls: Array<{ value: string[]; done: ReturnType<typeof deferred<string[]>> }> = [];
    const save = vi.fn((value: string[]) => {
      const done = deferred<string[]>();
      calls.push({ value, done });
      return done.promise;
    });
    const onSaved = vi.fn();
    const saver = createSerialSaver({ save, onSaved, onError: vi.fn() });

    const first = saver.push(['a']);
    saver.push(['a', 'b']);
    saver.push(['a', 'b', 'c']);
    expect(save).toHaveBeenCalledTimes(1);

    calls[0]!.done.resolve(['a']);
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(calls[1]!.value).toEqual(['a', 'b', 'c']);
    // The stale ['a'] response arrived while a newer value was waiting.
    expect(onSaved).not.toHaveBeenCalled();

    calls[1]!.done.resolve(['a', 'b', 'c']);
    await first;
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledWith(['a', 'b', 'c'], ['a', 'b', 'c']);
  });

  it('reports a failure only when no newer value is waiting', async () => {
    const save = vi
      .fn<(value: number) => Promise<number>>()
      .mockRejectedValueOnce(new Error('first failed'))
      .mockResolvedValueOnce(2);
    const onError = vi.fn();
    const onSaved = vi.fn();
    const saver = createSerialSaver({ save, onSaved, onError });

    const run = saver.push(1);
    saver.push(2);
    await run;
    expect(onError).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith(2, 2);

    save.mockRejectedValueOnce(new Error('last failed'));
    await saver.push(3);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![1]).toBe(3);
  });
});
