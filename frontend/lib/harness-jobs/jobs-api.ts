import { apiClient } from '@/lib/api';
import { ChatApi } from '@/chat/api';
import { durableSessionBase } from '@/chat/durable-run-stream';
import type { RunTimelineView } from '@/chat/types';
import type { ConversationGrantRevocation } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-grant-revocation';
import type { ConversationGrants } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-grants';
import type { ConversationSeen } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-seen';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { JobBriefHistory } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief-history';
import type { RequestJobs } from '@/lib/contracts/harness/generated/ext/pipeshub/api/request-jobs';
import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';

/**
 * The background-agents reads and writes the job screens make (UI-B1; ADR-UI-01, ADR-GRANT-01 D8, ADR-OPS-01
 * G11). The org and the person are always the token's on the server: nothing here names another person.
 */

export interface SessionRoute {
  agentId?: string;
  sessionId: string;
}

const run = (session: SessionRoute, runId: string) => `${durableSessionBase(session)}/runs/${encodeURIComponent(runId)}`;

/** The timeline as UI-B1 returns it: with the operator's stop (G11). */
export type JobTimeline = RunTimelineView & { operatorStop?: { scope: string; reason: string; at: number } | null };

export const JobsApi = {
  /** `GET /api/v1/agent-harness/runs?kind=job`: the caller's own jobs across their chats, newest first. */
  async personJobs(options: { cursor?: string | null; limit?: number } = {}): Promise<PersonRuns> {
    const { data } = await apiClient.get<PersonRuns>('/api/v1/agent-harness/runs', {
      params: { kind: 'job', limit: options.limit ?? 100, ...(options.cursor ? { cursor: options.cursor } : {}) },
      suppressErrorToast: true,
    });
    return data;
  },

  async brief(session: SessionRoute, runId: string): Promise<JobBrief> {
    const { data } = await apiClient.get<JobBrief>(`${run(session, runId)}/brief`, { suppressErrorToast: true });
    return data;
  },

  /** How its goal changed since it started (UI-B5, ADR-UI-01 D5): your messages, your choices, a hand-on. */
  async briefHistory(session: SessionRoute, runId: string): Promise<JobBriefHistory> {
    const { data } = await apiClient.get<JobBriefHistory>(`${run(session, runId)}/brief/history`, { suppressErrorToast: true });
    return data;
  },

  /** A request and the jobs it handed out, with how many are back (UI-B5, ADR-UI-01 D6). */
  async requestJobs(session: SessionRoute, requestRunId: string): Promise<RequestJobs> {
    const { data } = await apiClient.get<RequestJobs>(`${run(session, requestRunId)}/jobs`, { suppressErrorToast: true });
    return data;
  },

  /** Read once a job ended: its merge report, budget and an operator's stop, which its stream does not carry. */
  async timeline(session: SessionRoute, runId: string): Promise<JobTimeline> {
    return (await ChatApi.getRunTimeline({ ...session, runId })) as JobTimeline;
  },

  /** Stop, one click: the cancel route of that job's own run. */
  async stop(session: SessionRoute, runId: string): Promise<boolean> {
    return (await ChatApi.cancelCurrentDurableRun(session.agentId, session.sessionId, runId)) !== null;
  },

  async grants(session: SessionRoute): Promise<ConversationGrants> {
    const { data } = await apiClient.get<ConversationGrants>(`${durableSessionBase(session)}/grants`, { suppressErrorToast: true });
    return data;
  },

  async revokeGrant(session: SessionRoute, grantId: string): Promise<ConversationGrantRevocation> {
    const { data } = await apiClient.delete<ConversationGrantRevocation>(`${durableSessionBase(session)}/grants/${encodeURIComponent(grantId)}`, {
      suppressErrorToast: true,
    });
    return data;
  },

  /** The caller opened the chat: the server moves their mark to its own clock. */
  async markSeen(session: SessionRoute): Promise<ConversationSeen | null> {
    try {
      const { data } = await apiClient.put<ConversationSeen>(`${durableSessionBase(session)}/seen`, {}, { suppressErrorToast: true });
      return data;
    } catch {
      return null;
    }
  },
};

/** The job's own live stream route (ADR-UI-01 D4). */
export const jobStreamUrl = (session: SessionRoute, runId: string): string => `${run(session, runId)}/stream`;
