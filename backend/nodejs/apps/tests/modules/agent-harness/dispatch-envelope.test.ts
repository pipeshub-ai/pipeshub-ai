import { expect } from 'chai';
import { dispatchMessage } from '../../../src/modules/agent-harness/adapters/messaging/dispatch-envelope';

describe('agent run dispatch envelope', () => {
  it('is the identifier-only message the Python worker consumes', () => {
    const message = dispatchMessage({
      orgId: 'org-1',
      runId: 'run-1',
      commandId: 'dispatch:abc',
      kind: 'run_resume',
      createdAt: 1_700,
    });
    expect(message.topic).to.equal('agent-run-events');
    expect(message.key).to.equal('run-1');
    expect(message.orderingKey).to.equal('run-1');
    expect(message.headers).to.deep.equal({
      eventType: 'agent_run_dispatch_requested',
      schemaVersion: '1',
    });
    expect(JSON.parse(message.value)).to.deep.equal({
      eventType: 'agent_run_dispatch_requested',
      timestamp: 1_700,
      payload: {
        schemaVersion: 1,
        commandId: 'dispatch:abc',
        runId: 'run-1',
        orgId: 'org-1',
        kind: 'run_resume',
        createdAt: 1_700,
      },
    });
  });
});
