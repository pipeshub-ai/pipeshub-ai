import type { FeedPage } from '../collaboration-types';

const CHANNEL_PREFIX = 'ph-conv-sync:';
const HEARTBEAT_MS = 5_000;
const HEARTBEAT_TIMEOUT_MS = 12_000;

type Message = { type: 'feed'; page: FeedPage; afterSeq?: number } | { type: 'lost' } | { type: 'heartbeat'; from: string };

export interface SyncLeadership {
  /** Sends a page fetched from `afterSeq` to the other tabs. A no-op without `BroadcastChannel`. */
  publish(page: FeedPage, afterSeq: number): void;
  /** Tells the other tabs this one lost access, so they stop waiting for a leader that has quit. */
  publishLost(): void;
  stop(): void;
}

export interface SyncLeadershipOptions {
  /** Called when this tab becomes the one that polls; returns the stop function. */
  onLead(): () => void;
  /** Called for each page another tab fetched; `afterSeq` is the cursor it was fetched from, when known. */
  onPage(page: FeedPage, afterSeq: number | undefined): void;
  /** Called when another tab learned that access is gone. */
  onLost(): void;
}

function openChannel(name: string): BroadcastChannel | null {
  try {
    return typeof BroadcastChannel === 'function' ? new BroadcastChannel(name) : null;
  } catch {
    return null;
  }
}

/**
 * Elects one polling tab per conversation. `navigator.locks` decides when it exists (the lock
 * frees itself when the tab dies); otherwise tabs elect through a `BroadcastChannel` heartbeat.
 * With neither, every tab leads, which only costs extra requests.
 */
export function joinSyncLeadership(conversationId: string, options: SyncLeadershipOptions): SyncLeadership {
  const name = CHANNEL_PREFIX + conversationId;
  const channel = openChannel(name);
  const me = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let stopLead: (() => void) | null = null;
  let stopped = false;

  const lead = () => {
    if (stopped || stopLead) return;
    stopLead = options.onLead();
  };
  const unlead = () => {
    stopLead?.();
    stopLead = null;
  };

  const cleanups: Array<() => void> = [];
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;

  if (channel) {
    channel.onmessage = (event: MessageEvent<Message>) => {
      const msg = event.data;
      if (msg?.type === 'feed') options.onPage(msg.page, msg.afterSeq);
      else if (msg?.type === 'lost') options.onLost();
    };
  }

  if (locks?.request) {
    const abort = new AbortController();
    let release: (() => void) | null = null;
    void locks
      .request(name, { signal: abort.signal }, () => {
        if (stopped) return undefined;
        lead();
        return new Promise<void>((resolve) => {
          release = resolve;
        });
      })
      .catch(() => undefined);
    cleanups.push(() => {
      abort.abort();
      release?.();
    });
  } else if (channel) {
    let lastHeartbeat = 0;
    const onMessage = (event: MessageEvent<Message>) => {
      const msg = event.data;
      if (msg?.type !== 'heartbeat' || msg.from === me) return;
      lastHeartbeat = Date.now();
      if (stopLead && msg.from < me) unlead();
    };
    channel.addEventListener('message', onMessage as EventListener);
    const timer = setInterval(() => {
      if (stopLead) {
        channel.postMessage({ type: 'heartbeat', from: me } satisfies Message);
      } else if (Date.now() - lastHeartbeat > HEARTBEAT_TIMEOUT_MS) {
        lead();
      }
    }, HEARTBEAT_MS);
    const first = setTimeout(() => {
      if (!stopLead && Date.now() - lastHeartbeat > HEARTBEAT_TIMEOUT_MS) {
        lead();
        channel.postMessage({ type: 'heartbeat', from: me } satisfies Message);
      }
    }, 300 + Math.random() * 300);
    cleanups.push(() => {
      clearInterval(timer);
      clearTimeout(first);
      channel.removeEventListener('message', onMessage as EventListener);
    });
  } else {
    lead();
  }

  return {
    publish(page, afterSeq) {
      try {
        channel?.postMessage({ type: 'feed', page, afterSeq } satisfies Message);
      } catch {
        // A page the browser cannot clone is simply not shared.
      }
    },
    publishLost() {
      try {
        channel?.postMessage({ type: 'lost' } satisfies Message);
      } catch {
        // The other tabs find out on their own poll.
      }
    },
    stop() {
      stopped = true;
      for (const c of cleanups) c();
      unlead();
      channel?.close();
    },
  };
}
