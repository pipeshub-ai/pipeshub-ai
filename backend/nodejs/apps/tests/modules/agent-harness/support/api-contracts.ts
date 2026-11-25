import type { ZodTypeAny } from 'zod/v3';
import { codingEngineViewSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/coding-engine-view';
import { modelAccessViewSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/model-access-view';
import { pendingApprovalsSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/pending-approvals';
import { inboxCountsSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-counts';
import { inboxFeedSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-feed';
import { inboxNoticesResultSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-notices-result';
import { inboxPreferencesSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-preferences';
import { jobBriefHistorySchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/job-brief-history';
import { requestJobsSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/request-jobs';

/**
 * CPD0-27: the bodies the approvals and AI-access routes answer with are the
 * schemas the frontend's types are generated from
 * (contracts/harness/ext/pipeshub/api). The HTTP tests of those routes pass
 * every successful answer through `assertAnswersContract`.
 */
export const API_CONTRACTS: ReadonlyArray<{
  method: string;
  path: RegExp;
  schema: ZodTypeAny;
}> = [
  { method: 'GET', path: /\/agent-harness\/approvals\/pending$/, schema: pendingApprovalsSchema },
  { method: 'GET', path: /\/agent-harness\/model-accounts\/access$/, schema: modelAccessViewSchema },
  { method: 'GET', path: /\/agent-harness\/model-accounts\/coding-engine$/, schema: codingEngineViewSchema },
  { method: 'PUT', path: /\/agent-harness\/model-accounts\/coding-engine$/, schema: codingEngineViewSchema },
  { method: 'PUT', path: /\/agent-harness\/admin\/coding-engine$/, schema: codingEngineViewSchema },
  // G2 (ADR-INBOX-01): the Inbox.
  { method: 'GET', path: /\/agent-harness\/inbox\/?$/, schema: inboxFeedSchema },
  { method: 'GET', path: /\/agent-harness\/inbox\/count$/, schema: inboxCountsSchema },
  { method: 'PATCH', path: /\/agent-harness\/inbox\/notices$/, schema: inboxNoticesResultSchema },
  { method: 'POST', path: /\/agent-harness\/inbox\/notices\/delete$/, schema: inboxNoticesResultSchema },
  { method: 'GET', path: /\/agent-harness\/inbox\/preferences$/, schema: inboxPreferencesSchema },
  { method: 'PATCH', path: /\/agent-harness\/inbox\/preferences$/, schema: inboxPreferencesSchema },
  // UI-B5: a job's goal history and one request's jobs.
  { method: 'GET', path: /\/conversations\/[^/]+\/runs\/[^/]+\/brief\/history$/, schema: jobBriefHistorySchema },
  { method: 'GET', path: /\/conversations\/[^/]+\/runs\/[^/]+\/jobs$/, schema: requestJobsSchema },
];

/** Throws naming the route and the first issues when a successful answer is not its contract. */
export function assertAnswersContract(method: string, url: string, status: number, body: unknown): void {
  if (status < 200 || status >= 300 || status === 204) return;
  const path = new URL(url, 'http://local').pathname;
  const route = API_CONTRACTS.find((r) => r.method === method.toUpperCase() && r.path.test(path));
  if (route === undefined) return;
  const parsed = route.schema.safeParse(body);
  if (!parsed.success) {
    throw new Error(`${method} ${path} answered outside its contract: ${JSON.stringify(parsed.error.issues.slice(0, 3))}`);
  }
}
