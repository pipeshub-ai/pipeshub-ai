import { useChatStore } from '@/app/(main)/chat/store';
import type { FailedLoad } from '@/app/(main)/chat/types';

/** How long after a load that failed the next one is tried, by how many failed in a row; the last wait from then on. */
export const LOAD_RETRY_MS = [1_000, 3_000, 6_000, 15_000, 30_000];
/** However often the window gets focus or the browser comes online, the next try waits this long after a failure. */
export const LOAD_RETRY_GAP_MS = 1_000;

/**
 * Runs `retry` once, after the wait for a load that failed (±20%, so many tabs that failed together do not try together),
 * or as soon as the window has focus or the browser is online again. Returns the function that cancels it.
 */
export function retryLater({ count, at }: FailedLoad, retry: () => void): () => void {
  const wait = LOAD_RETRY_MS[Math.min(count, LOAD_RETRY_MS.length) - 1] * (0.8 + Math.random() * 0.4);
  let timer = setTimeout(run, Math.max(0, at + wait - Date.now()));
  function stop() {
    clearTimeout(timer);
    window.removeEventListener('focus', soon);
    window.removeEventListener('online', soon);
  }
  function run() {
    stop();
    retry();
  }
  function soon() {
    clearTimeout(timer);
    timer = setTimeout(run, Math.max(0, at + LOAD_RETRY_GAP_MS - Date.now()));
  }
  window.addEventListener('focus', soon);
  window.addEventListener('online', soon);
  return stop;
}

/**
 * Tries a failed load of the slot's history again, now: nothing while one is under way, nor while an answer is written
 * here, which the load would replace (`useLoadRetry` tries once it has ended).
 */
export function loadAgain(slotId: string): void {
  const { slots, updateSlot } = useChatStore.getState();
  const slot = slots[slotId];
  if (!slot?.failedLoad || slot.failedLoad.trying || slot.isStreaming || slot.stopping) return;
  updateSlot(slotId, { failedLoad: { ...slot.failedLoad, trying: true } });
}
