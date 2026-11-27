/**
 * P9 CA-5: the shipped canary definition is a request Node's agent route
 * accepts, and the routes the canary script calls are the ones the session
 * router mounts (the script is standard-library Python; this keeps it honest).
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createSessionRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const REPO = join(__dirname, '../../../../../..');
const definition = JSON.parse(
  readFileSync(join(REPO, 'deployment/canary/engine-canary.json'), 'utf-8'),
) as { request: Record<string, unknown>; expect: string; timeoutS: number };

describe('canary definition (P9 CA-5)', () => {
  it('its request body passes the agent run admission schema', () => {
    const parsed = createSessionRunSchema.parse({
      params: { agentKey: 'agent-canary' },
      body: { ...definition.request, idempotencyKey: 'canary-0123456789abcdef' },
    });
    expect(parsed.body.query).to.include(definition.expect);
    expect(parsed.body.deadlineMs).to.be.at.least(definition.timeoutS * 1000);
  });

  it('the script calls the routes the session router mounts', () => {
    const script = readFileSync(join(REPO, 'scripts/engines/canary.py'), 'utf-8');
    const routes = readFileSync(
      join(REPO, 'backend/nodejs/apps/src/modules/agent-harness/transport/session-routes.ts'),
      'utf-8',
    );
    expect(script).to.include('/api/v1/agents/{agent_key}/runs');
    expect(script).to.include('/api/v1/agents/{agent_key}/conversations/{report.sessionId}/runs/{report.runId}');
    expect(routes).to.include("'/:agentKey/runs'");
    expect(routes).to.include("'/:agentKey/conversations/:sessionId'");
    for (const tail of ['/runs/:runId`', '/runs/:runId/timeline`', '/runs/:runId/cancel`']) {
      expect(routes).to.include(`\${sessionPath}${tail}`);
    }
  });
});
