import 'reflect-metadata';
import { expect } from 'chai';

import { extractScheduledTriggersFromFlow } from '../../../../src/modules/enterprise_search/utils/agent_schedule.util';

describe('enterprise_search/utils/agent_schedule.util', () => {
  it('extracts connected and enabled scheduled triggers', () => {
    const flow = {
      nodes: [
        {
          id: 'sched-1',
          data: {
            type: 'scheduled-input',
            config: {
              enabled: true,
              cronExpression: '0 8 * * 1-5',
              timezone: 'Europe/Berlin',
              input: 'Create a daily summary',
            },
          },
        },
        {
          id: 'agent-1',
          data: { type: 'agent-core', config: {} },
        },
      ],
      edges: [
        {
          source: 'sched-1',
          target: 'agent-1',
          sourceHandle: 'message',
          targetHandle: 'input',
        },
      ],
    };

    const triggers = extractScheduledTriggersFromFlow(flow);

    expect(triggers).to.deep.equal([
      {
        triggerId: 'sched-1',
        cronExpression: '0 8 * * 1-5',
        timezone: 'Europe/Berlin',
        input: 'Create a daily summary',
      },
    ]);
  });

  it('ignores disconnected scheduled nodes', () => {
    const flow = {
      nodes: [
        {
          id: 'sched-1',
          data: {
            type: 'scheduled-input',
            config: {
              enabled: true,
              cronExpression: '0 8 * * 1-5',
              timezone: 'UTC',
              input: 'Run report',
            },
          },
        },
        { id: 'agent-1', data: { type: 'agent-core', config: {} } },
      ],
      edges: [],
    };

    const triggers = extractScheduledTriggersFromFlow(flow);
    expect(triggers).to.have.length(0);
  });

  it('ignores disabled or invalid entries', () => {
    const flow = {
      nodes: [
        {
          id: 'sched-disabled',
          data: {
            type: 'scheduled-input',
            config: {
              enabled: false,
              cronExpression: '0 8 * * 1-5',
              timezone: 'UTC',
              input: 'Run report',
            },
          },
        },
        {
          id: 'sched-invalid-cron',
          data: {
            type: 'scheduled-input',
            config: {
              enabled: true,
              cronExpression: 'invalid',
              timezone: 'UTC',
              input: 'Run report',
            },
          },
        },
        {
          id: 'sched-missing-input',
          data: {
            type: 'scheduled-input',
            config: {
              enabled: true,
              cronExpression: '0 8 * * 1-5',
              timezone: 'UTC',
              input: '   ',
            },
          },
        },
        { id: 'agent-1', data: { type: 'agent-core', config: {} } },
      ],
      edges: [
        { source: 'sched-disabled', target: 'agent-1', targetHandle: 'input' },
        { source: 'sched-invalid-cron', target: 'agent-1', targetHandle: 'input' },
        { source: 'sched-missing-input', target: 'agent-1', targetHandle: 'input' },
      ],
    };

    const triggers = extractScheduledTriggersFromFlow(flow);
    expect(triggers).to.have.length(0);
  });
});
