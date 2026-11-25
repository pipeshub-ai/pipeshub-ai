'use client';

import { useEffect, useState, type RefCallback } from 'react';
import { followJob, readSettled, useJobsStore, type JobExtra, type JobStreamState, type SessionRoute } from './data';

/**
 * A job card's live data (CLASSIC-JOBS D13): the job's own stream while it runs, shared with every other place that
 * shows it and capped at `MAX_FOLLOWED` open streams; no timeline poll. A job that already ended when the card
 * mounted is not replayed: its part says how it ended, and one timeline read says whether an admin stopped it.
 */
export function useJobStream(
  session: SessionRoute | null,
  runId: string | undefined,
  options: { live: boolean; stoppedEarlier: boolean; owner: boolean },
): { stream?: JobStreamState; extra?: JobExtra } {
  const stream = useJobsStore((s) => (runId ? s.streams[runId] : undefined));
  const extra = useJobsStore((s) => (runId ? s.extras[runId] : undefined));
  const agentId = session?.agentId;
  const sessionId = session?.sessionId;

  useEffect(() => {
    if (!sessionId || !runId || !options.live) return undefined;
    // A shared viewer follows the stream (D14) but may not read the job's timeline: no settle read for them.
    return followJob({ ...(agentId ? { agentId } : {}), sessionId }, runId, { settle: options.owner });
  }, [agentId, sessionId, runId, options.live, options.owner]);

  useEffect(() => {
    if (!sessionId || !runId || options.live || !options.stoppedEarlier || !options.owner) return;
    void readSettled({ ...(agentId ? { agentId } : {}), sessionId }, runId);
  }, [agentId, sessionId, runId, options.live, options.stoppedEarlier, options.owner]);

  return { ...(stream ? { stream } : {}), ...(extra ? { extra } : {}) };
}

/** True while the element is on screen; true where there is no IntersectionObserver (it may tick). */
export function useOnScreen<T extends Element>(): [RefCallback<T>, boolean] {
  const [node, setNode] = useState<T | null>(null);
  const [shown, setShown] = useState(true);
  useEffect(() => {
    if (!node || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => setShown(entries.some((e) => e.isIntersecting)));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, shown];
}

/** The time now, once a second while `ticking`; the clock stands still otherwise. */
export function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}
