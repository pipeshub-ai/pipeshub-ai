'use client';

import { useEffect } from 'react';
import { requestAskFocus } from '../../hooks/use-interaction-focus';
import { jobPanelSectionOf, openJobOf, useActivityPanelStore, type JobPanelSection } from './activity-panel-store';

/** The query a job panel adds to `/chat` (D5): `job=<runId>[&section=…]`, and `ask=<interactionId>` from Needs you. */
export interface JobPanelQuery {
  job: string | null;
  section: JobPanelSection | null;
  ask: string | null;
}

export function readJobPanelQuery(params: Pick<URLSearchParams, 'get'>): JobPanelQuery {
  const job = params.get('job')?.trim() || null;
  const section = jobPanelSectionOf(params.get('section'));
  const ask = params.get('ask')?.trim() || null;
  return { job, section: job ? section : null, ask };
}

/** The same URL with the job panel's part replaced; every other parameter is kept as it was. */
export function withJobPanelQuery(href: string, job: { runId: string; section: JobPanelSection | null } | null, dropAsk = false): string {
  const url = new URL(href, 'http://local');
  if (job) {
    url.searchParams.set('job', job.runId);
    if (job.section) url.searchParams.set('section', job.section);
    else url.searchParams.delete('section');
  } else {
    url.searchParams.delete('job');
    url.searchParams.delete('section');
  }
  if (dropAsk) url.searchParams.delete('ask');
  return `${url.pathname}${url.search}${url.hash}`;
}

function replaceUrl(next: string): void {
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) window.history.replaceState(window.history.state, '', next);
}

/**
 * The job panel and the URL kept as one (D5, `frontend/docs/url-driven-panel-state.md`): a link or a reload opens
 * the job named in the URL, opening or closing the panel writes it back, and an `ask` hands focus to the card that
 * answers it (D9). Both directions compare against the other side first, so neither feeds back into the other.
 *
 * Mounted once by the chat page, after its own effect that closes the panel when the conversation changes, so a
 * link into another chat's job opens after that close.
 */
export function useJobPanelUrl(
  params: Pick<URLSearchParams, 'get'>,
  session: { conversationId: string | null; agentId: string | null; isMobile: boolean },
): void {
  const query = readJobPanelQuery(params);
  const { ask, section } = query;
  const { conversationId, agentId, isMobile } = session;
  // On the phone the panel is a sheet over the chat: a link to an ask lands on its card, not behind a sheet (D9).
  const job = ask && isMobile ? null : query.job;

  // First, so the panel this link opens on mount (the effect below) is written back too: an old `section=files`
  // link stayed in the URL beside the Machine tab it opened.
  useEffect(
    () =>
      useActivityPanelStore.subscribe((state, prev) => {
        const now = openJobOf(state);
        const before = openJobOf(prev);
        if (now?.runId === before?.runId && now?.section === before?.section && now?.sessionId === before?.sessionId) return;
        const conv = new URLSearchParams(window.location.search).get('conversationId');
        const href = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (now) {
          if (now.sessionId === conv) replaceUrl(withJobPanelQuery(href, now));
          return;
        }
        // Only a close of this chat's job clears the URL; another chat's panel reset says nothing about this one.
        if (before && before.sessionId === conv) replaceUrl(withJobPanelQuery(href, null));
      }),
    [],
  );

  useEffect(() => {
    const store = useActivityPanelStore.getState();
    const open = openJobOf(store);
    if (job && conversationId) {
      if (open?.runId === job && open.sessionId === conversationId) {
        if (section && open.section !== section) store.jumpTo(section);
        return;
      }
      store.showJob({ agentId, sessionId: conversationId }, job, section);
      return;
    }
    if (!job && open && open.sessionId === conversationId) store.closeJob();
  }, [job, section, conversationId, agentId]);

  useEffect(() => {
    if (!ask) return;
    requestAskFocus(ask);
    replaceUrl(withJobPanelQuery(`${window.location.pathname}${window.location.search}`, job ? { runId: job, section } : null, true));
  }, [ask, job, section]);

}
