import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toast, useToastStore } from '../toast-store';

const find = (id: string) => useToastStore.getState().toasts.find((t) => t.id === id);

describe('toast updates', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('a loading toast turned into an error with a duration can be closed and goes away', () => {
    const id = toast.loading('Checking…');
    expect(find(id)?.showCloseButton).toBe(false);

    toast.update(id, { variant: 'error', title: 'Check failed', duration: 6000 });
    expect(find(id)?.showCloseButton).toBe(true);
    expect(find(id)?.duration).toBe(6000);

    vi.advanceTimersByTime(6000 + 300);
    expect(find(id)).toBeUndefined();
  });

  it('a variant change without a duration uses the variant default', () => {
    const id = toast.loading('Saving…');
    toast.update(id, { variant: 'success', title: 'Saved' });
    expect(find(id)?.showCloseButton).toBe(true);
    expect(find(id)?.duration).toBeGreaterThan(0);
  });

  it('a timed toast switched back to loading is not removed by its old timer', () => {
    const id = toast.error('Check failed', { duration: 6000 });
    toast.update(id, { variant: 'loading', title: 'Retrying…' });
    vi.advanceTimersByTime(60_000);
    expect(find(id)?.title).toBe('Retrying…');
  });

  it('a title-only update keeps a loading toast open', () => {
    const id = toast.loading('Checking 1 of 3');
    toast.update(id, { title: 'Checking 2 of 3' });
    vi.advanceTimersByTime(60_000);
    expect(find(id)?.title).toBe('Checking 2 of 3');
    expect(find(id)?.showCloseButton).toBe(false);
  });
});

describe('toast lifetimes', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ['success', 4000],
    ['info', 4000],
    ['warning', 4000],
    ['error', 6000],
  ] as const)('a %s toast closes after %i ms', (variant, ms) => {
    const id = toast[variant]('Done');
    expect(find(id)?.duration).toBe(ms);
    vi.advanceTimersByTime(ms - 1);
    expect(find(id)?.isExiting).toBeFalsy();
    vi.advanceTimersByTime(1 + 300);
    expect(find(id)).toBeUndefined();
  });

  it('a toast with an action closes on the same clock', () => {
    const id = toast.success('Archived', { action: { label: 'Undo', onClick: () => {} } });
    expect(find(id)?.duration).toBe(4000);
    vi.advanceTimersByTime(4000 + 300);
    expect(find(id)).toBeUndefined();
  });

  it('a new toast replaces the one showing', () => {
    const first = toast.info('First');
    const second = toast.info('Second');
    expect(find(first)?.isExiting).toBe(true);
    vi.advanceTimersByTime(300);
    expect(find(first)).toBeUndefined();
    expect(find(second)?.isExiting).toBeFalsy();
  });

  it('pausing keeps the time left, and resuming spends only that', () => {
    const id = toast.success('Saved');
    vi.advanceTimersByTime(2000);
    useToastStore.getState().setHovered(true);
    vi.advanceTimersByTime(10_000);
    expect(find(id)?.isExiting).toBeFalsy();
    useToastStore.getState().setHovered(false);
    vi.advanceTimersByTime(1999);
    expect(find(id)?.isExiting).toBeFalsy();
    vi.advanceTimersByTime(1);
    expect(find(id)?.isExiting).toBe(true);
  });

  it('ends the pause when the last toast leaves from under the pointer', () => {
    const id = toast.success('Saved');
    useToastStore.getState().setHovered(true);
    toast.dismiss(id);
    vi.advanceTimersByTime(300);
    expect(useToastStore.getState().isHovered).toBe(false);
    const next = toast.success('Again');
    vi.advanceTimersByTime(4000);
    expect(find(next)?.isExiting).toBe(true);
  });
});

describe('a loading toast replaced before its outcome', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const visible = () => useToastStore.getState().toasts.filter((t) => !t.isExiting);

  it('shows the success when the promise resolves after an unrelated toast took its place', async () => {
    let resolve!: (v: string) => void;
    const work = new Promise<string>((r) => {
      resolve = r;
    });
    const done = toast.promise(work, { loading: 'Saving…', success: 'Saved', error: 'Could not save' });
    toast.info('Copied link');
    vi.advanceTimersByTime(300);
    expect(visible().map((t) => t.title)).toEqual(['Copied link']);

    resolve('ok');
    await done;

    expect(visible().map((t) => t.title)).toEqual(['Saved']);
    expect(visible()[0].variant).toBe('success');
    expect(visible()[0].showCloseButton).toBe(true);
    expect(visible()[0].duration).toBe(4000);
  });

  it('shows the error when the promise rejects after an unrelated toast took its place', async () => {
    let reject!: (e: Error) => void;
    const work = new Promise<string>((_r, j) => {
      reject = j;
    });
    const done = toast.promise(work, { loading: 'Saving…', success: 'Saved', error: 'Could not save' });
    toast.info('Copied link');
    vi.advanceTimersByTime(300);

    reject(new Error('nope'));
    await expect(done).rejects.toThrow('nope');

    expect(visible().map((t) => t.title)).toEqual(['Could not save']);
    expect(visible()[0].variant).toBe('error');
    expect(visible()[0].duration).toBe(6000);
  });

  it('keeps progress updates to a replaced loading toast for its outcome, and follows the outcome\'s new id', () => {
    const id = toast.loading('Uploading 1 of 3');
    toast.info('Copied link');
    toast.update(id, { title: 'Uploading 2 of 3' });
    expect(visible().map((t) => t.title)).toEqual(['Copied link']);
    toast.update(id, { variant: 'success', title: 'Uploaded' });
    expect(visible().map((t) => t.title)).toEqual(['Uploaded']);
    toast.dismiss(id);
    expect(visible()).toHaveLength(0);
  });

  it('does not bring back an outcome the caller dismissed', () => {
    const id = toast.loading('Saving…');
    toast.info('Copied link');
    toast.dismiss(id);
    toast.update(id, { variant: 'success', title: 'Saved' });
    expect(visible().map((t) => t.title)).toEqual(['Copied link']);
  });
});

describe('the close button', () => {
  beforeEach(() => {
    useToastStore.getState().clearAll();
  });

  it.each([
    ['success', true],
    ['info', true],
    ['warning', true],
    ['error', true],
    ['loading', false],
  ] as const)('a %s toast has one by default: %s', (variant, expected) => {
    const id = toast[variant]('Done');
    expect(find(id)?.showCloseButton).toBe(expected);
  });

  it('an action toast has one too', () => {
    const id = toast.success('Archived', { action: { label: 'Undo', onClick: () => {} } });
    expect(find(id)?.showCloseButton).toBe(true);
  });

  it('a caller that passes false gets none, where the same toast would have had one', () => {
    expect(find(toast.success('Done'))?.showCloseButton).toBe(true);
    const id = toast.success('Done', { showCloseButton: false });
    expect(find(id)?.showCloseButton).toBe(false);
  });

  it('a loading toast that settles gains one, and loses it when it goes back to loading', () => {
    const id = toast.loading('Saving…');
    expect(find(id)?.showCloseButton).toBe(false);
    toast.update(id, { variant: 'success', title: 'Saved' });
    expect(find(id)?.showCloseButton).toBe(true);
    toast.update(id, { variant: 'loading', title: 'Saving again…' });
    expect(find(id)?.showCloseButton).toBe(false);
  });

  it('a settled toast keeps a caller\'s false, where it would have had one', () => {
    const plain = toast.loading('Saving…');
    toast.update(plain, { variant: 'info', title: 'Saved' });
    expect(find(plain)?.showCloseButton).toBe(true);
    const id = toast.loading('Saving…');
    toast.update(id, { variant: 'info', title: 'Saved', showCloseButton: false });
    expect(find(id)?.showCloseButton).toBe(false);
  });
});

describe('an Undo toast is never replaced', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const visible = () => useToastStore.getState().toasts.filter((t) => !t.isExiting && !t.isWaiting).map((t) => t.title);
  const undo = { label: 'Undo', onClick: () => {} };

  it('toasts raised while it shows wait, then come up in order, one at a time', () => {
    toast.success('Chat archived', { action: undo });
    toast.info('Copied link');
    toast.error('Could not load chats');
    expect(visible()).toEqual(['Chat archived']);

    vi.advanceTimersByTime(4000);
    expect(visible()).toEqual(['Copied link']);
    vi.advanceTimersByTime(4000);
    expect(visible()).toEqual(['Could not load chats']);
    vi.advanceTimersByTime(6000 + 300);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('an error raised meanwhile waits too, and the Undo still works', () => {
    const onClick = vi.fn();
    const id = toast.success('Chat archived', { action: { label: 'Undo', onClick } });
    toast.error('Could not load chats');
    expect(find(id)?.isExiting).toBeFalsy();
    find(id)?.action?.onClick?.();
    expect(onClick).toHaveBeenCalled();
  });

  it('the next toast comes up as soon as the Undo toast is dismissed', () => {
    const id = toast.success('Chat archived', { action: undo });
    toast.info('Copied link');
    toast.dismiss(id);
    expect(visible()).toEqual(['Copied link']);
  });

  it('a waiting toast can be updated or dismissed before its turn', () => {
    toast.success('Chat archived', { action: undo });
    const saving = toast.loading('Saving…');
    const copied = toast.info('Copied link');
    toast.update(saving, { variant: 'success', title: 'Saved' });
    toast.dismiss(copied);
    vi.advanceTimersByTime(4000);
    expect(visible()).toEqual(['Saved']);
    vi.advanceTimersByTime(4000);
    expect(visible()).toEqual([]);
  });

  it('a plain toast is still replaced by the next one', () => {
    toast.info('First');
    toast.info('Second');
    expect(visible()).toEqual(['Second']);
  });
});

describe('Undo toast timing with a toast waiting behind it', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const showing = () => useToastStore.getState().toasts.find((t) => !t.isExiting && !t.isWaiting);

  it.each([
    ['success', 4000],
    ['error', 6000],
  ] as const)('Undo stays for its full 4000ms, then the %s shows for its own %ims', (variant, ms) => {
    const onClick = vi.fn();
    toast.success('Chat archived', { action: { label: 'Undo', onClick } });
    vi.advanceTimersByTime(900);
    toast[variant]('Next');
    vi.advanceTimersByTime(4000 - 900 - 1);
    expect(showing()?.title).toBe('Chat archived');
    showing()?.action?.onClick?.();
    expect(onClick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(showing()?.title).toBe('Next');
    vi.advanceTimersByTime(ms - 1);
    expect(showing()?.title).toBe('Next');
    vi.advanceTimersByTime(1);
    expect(showing()).toBeUndefined();
  });

  it('pausing the Undo toast holds the queue too', () => {
    toast.success('Chat archived', { action: { label: 'Undo', onClick: () => {} } });
    toast.info('Next');
    useToastStore.getState().setHovered(true);
    vi.advanceTimersByTime(10_000);
    expect(showing()?.title).toBe('Chat archived');
    useToastStore.getState().setHovered(false);
    vi.advanceTimersByTime(4000);
    expect(showing()?.title).toBe('Next');
  });
});

describe('exit timing', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.getState().clearAll();
  });
  afterEach(() => {
    vi.useRealTimers();
    document.documentElement.style.removeProperty('--motion-surface');
  });

  it('removes a dismissed toast after the surface motion role', () => {
    document.documentElement.style.setProperty('--motion-surface', '240ms');
    const id = toast.info('Saved');
    toast.dismiss(id);
    vi.advanceTimersByTime(239);
    expect(find(id)).toBeDefined();
    vi.advanceTimersByTime(1);
    expect(find(id)).toBeUndefined();
  });

  it('removes it at once when the role is zero (reduced motion)', () => {
    document.documentElement.style.setProperty('--motion-surface', '0ms');
    const id = toast.info('Saved');
    toast.dismiss(id);
    vi.advanceTimersByTime(0);
    expect(find(id)).toBeUndefined();
  });
});
