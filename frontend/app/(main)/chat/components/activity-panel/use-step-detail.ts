'use client';

import { useEffect, useMemo, useState } from 'react';
import { ChatApi } from '../../api';
import type { ActivityStepDetail, ActivityTool, DurableAgentRunHandle } from '../../types';

/**
 * An opened step's arguments and result (ACTRESULTS). The activity snapshot, sent on every journal advance,
 * carries only each step's line and the fingerprint of its detail; the detail is read once, when the step is
 * opened, and kept per step until its fingerprint changes (its result arrived or changed).
 */

const MAX_KEPT = 300;
const kept = new Map<string, ActivityStepDetail>();

const keyOf = (run: DurableAgentRunHandle, toolCallId: string, fingerprint: string): string =>
  `${run.agentId ?? ''}|${run.sessionId}|${run.runId}|${toolCallId}|${fingerprint}`;

function keep(key: string, detail: ActivityStepDetail): void {
  kept.delete(key);
  kept.set(key, detail);
  while (kept.size > MAX_KEPT) kept.delete(kept.keys().next().value as string);
}

/** Forgets every kept detail (tests). */
export function forgetStepDetails(): void {
  kept.clear();
}

export type StepDetailState = 'ready' | 'loading' | 'failed';

/** The step with its detail once read; a step that came with its detail (a child run's part) is ready as it is. */
export function useStepDetail(
  run: DurableAgentRunHandle,
  tool: ActivityTool,
  open: boolean,
): { tool: ActivityTool; state: StepDetailState } {
  const fingerprint = tool.detail;
  const key = fingerprint ? keyOf(run, tool.toolCallId, fingerprint) : null;
  const [, setRead] = useState(0);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const detail = key ? kept.get(key) : undefined;

  useEffect(() => {
    if (!open || !key || kept.has(key)) return;
    let alive = true;
    setFailedKey(null);
    ChatApi.getRunStep(run, tool.toolCallId)
      .then((read) => {
        if (!read) throw new Error('malformed step');
        // A step that moved on while it was read is newer than the snapshot asked for: shown now, and kept under
        // the fingerprint the next snapshot will name.
        keep(key, read);
        keep(keyOf(run, tool.toolCallId, read.fingerprint), read);
        if (alive) setRead((n) => n + 1);
      })
      .catch(() => {
        if (alive) setFailedKey(key);
      });
    return () => {
      alive = false;
    };
  }, [open, key, run, tool.toolCallId]);

  const merged = useMemo(() => {
    if (!detail) return tool;
    const { fingerprint: _fingerprint, toolCallId: _id, ...fields } = detail;
    return { ...tool, ...fields };
  }, [tool, detail]);

  if (!key) return { tool, state: 'ready' };
  if (detail) return { tool: merged, state: 'ready' };
  return { tool, state: failedKey === key ? 'failed' : 'loading' };
}
