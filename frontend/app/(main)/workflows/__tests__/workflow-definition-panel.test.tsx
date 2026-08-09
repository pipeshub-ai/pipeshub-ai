import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { WorkflowDefinitionPanel } from '../components/workflow-definition-panel';
import type { Workflow } from '../types';

// i18n stub
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

afterEach(() => cleanup());

const h = React.createElement;

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    workflowId: 'wf-1',
    orgId: 'org-1',
    kind: 'agent_task',
    name: 'My Workflow',
    description: 'Summarize Slack messages every morning',
    status: 'active',
    executionKind: 'agent_task',
    requiredScopes: [],
    createdByUserId: 'u-1',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    triggers: [],
    toolNames: ['slack_send_message', 'gmail_read'],
    connectorIds: ['slack-connector-1'],
    collectionIds: ['kb-1'],
    maxTurns: 10,
    timeoutSeconds: 300,
    ...overrides,
  };
}

describe('WorkflowDefinitionPanel', () => {
  it('shows the workflow description', () => {
    render(h(Theme, null, h(WorkflowDefinitionPanel, { workflow: makeWorkflow() })));
    expect(screen.getByText('Summarize Slack messages every morning')).toBeTruthy();
  });

  it('shows tool names as badges', () => {
    render(h(Theme, null, h(WorkflowDefinitionPanel, { workflow: makeWorkflow() })));
    expect(screen.getByText('slack_send_message')).toBeTruthy();
    expect(screen.getByText('gmail_read')).toBeTruthy();
  });

  it('shows connector IDs as badges', () => {
    render(h(Theme, null, h(WorkflowDefinitionPanel, { workflow: makeWorkflow() })));
    expect(screen.getByText('slack-connector-1')).toBeTruthy();
  });

  it('shows knowledge base IDs as badges', () => {
    render(h(Theme, null, h(WorkflowDefinitionPanel, { workflow: makeWorkflow() })));
    expect(screen.getByText('kb-1')).toBeTruthy();
  });

  it('shows max turns and timeout', () => {
    render(h(Theme, null, h(WorkflowDefinitionPanel, { workflow: makeWorkflow() })));
    expect(screen.getByText('10')).toBeTruthy();
    expect(screen.getByText('300s')).toBeTruthy();
  });

  it('shows no-description placeholder when description is absent', () => {
    render(
      h(Theme, null,
        h(WorkflowDefinitionPanel, { workflow: makeWorkflow({ description: undefined }) }),
      ),
    );
    expect(screen.getByText('No description provided.')).toBeTruthy();
  });

  it('does not render tool section when there are no tools', () => {
    render(
      h(Theme, null,
        h(WorkflowDefinitionPanel, { workflow: makeWorkflow({ toolNames: [] }) }),
      ),
    );
    expect(screen.queryByText('Allowed Tools')).toBeNull();
  });
});
