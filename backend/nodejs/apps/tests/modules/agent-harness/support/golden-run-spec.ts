import { SpecSubject } from '../../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition } from './agent-definition';

/** The subject of `contracts/harness/vectors/pinned-run-spec.json` (P-4 golden). */
export const goldenSpecSubject = (): SpecSubject => ({
  run: {
    runId: 'run-1',
    orgId: 'org-1',
    sessionId: 'session-1',
    actorId: 'user-1',
    agentKey: 'agent-1',
    userMessageKey: 'message-1',
    idempotencyKey: 'idem-1',
    payloadHash: 'hash',
    harnessMajor: 2,
    profile: 'dispatched',
    status: 'queued',
    outcome: null,
    revision: 0,
    fence: 0,
    attemptId: null,
    workerId: null,
    leaseExpiresAt: null,
    absoluteDeadlineAt: Date.UTC(2026, 9, 1, 12),
    checkpoint: null,
    input: {
      query: 'q',
      recordIds: [],
      filters: { apps: ['app-drive'] },
      attachments: [],
      tools: ['jira.search_issues', 'mcp_github_list_prs', 'web_search'],
      model: { chatMode: 'react', modelKey: 'model-2' },
    },
    createdAt: Date.UTC(2026, 9, 1, 11),
    updatedAt: Date.UTC(2026, 9, 1, 11),
  },
  agentDefinition: agentDefinition(),
  via: 'pat',
});
