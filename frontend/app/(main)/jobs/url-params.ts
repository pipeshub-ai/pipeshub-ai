import { buildChatHref } from '@/chat/build-chat-url';

/** The Jobs page's two tabs, named in its URL (D6): `?view=needs-you` and `?view=all`. */
export type JobsView = 'needs-you' | 'all';

export const JOBS_HREF: Record<'needsYou' | 'all', string> = {
  needsYou: '/jobs/?view=needs-you',
  all: '/jobs/?view=all',
};

/** The tab the URL names; with none, Needs you while something waits, else Jobs (D6). */
export function jobsViewOf(params: Pick<URLSearchParams, 'get'> | null, waiting: number): JobsView {
  const view = params?.get('view');
  if (view === 'needs-you' || view === 'all') return view;
  return waiting > 0 ? 'needs-you' : 'all';
}

export const jobsViewHref = (view: JobsView): string => (view === 'needs-you' ? JOBS_HREF.needsYou : JOBS_HREF.all);

/**
 * A chat, opened on one of its jobs (`job`, D5) and, from Needs you, at one ask (`ask`, D9). `job` is only named
 * for a run known to be a job: a chat's own answer has no job panel to open, its ask is found in the chat itself.
 */
export function chatAtHref(target: { conversationId: string; agentKey?: string | null; jobRunId?: string | null; askId?: string | null }): string {
  const base = buildChatHref({ agentId: target.agentKey ?? null, conversationId: target.conversationId });
  const q = new URLSearchParams();
  if (target.jobRunId) q.set('job', target.jobRunId);
  if (target.askId) q.set('ask', target.askId);
  const extra = q.toString();
  return extra ? `${base}&${extra}` : base;
}
