/**
 * A page fetch the worker refused because the run was not given its address (after it read private content) says so
 * in plain words, not by the rule's id; other refusals keep their wording.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { ActivityTool } from '../../../types';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, streamSSERequest: vi.fn(), streamSSEGet: vi.fn() }));

const { StepList } = await import('../step-row');
const h = React.createElement;
const run = { sessionId: 'conv-1', runId: 'run-1' };

function refused(rule: string, status: ActivityTool['status'] = 'blocked'): ActivityTool {
  return { toolCallId: `call-${rule}`, name: 'dynamic__fetch_url', displayName: 'Open page', status, refusal: { rule } };
}

afterEach(cleanup);

describe('web address refusal', () => {
  it.each(['blocked', 'denied'] as const)('a %s fetch to an address nobody gave reads as plain words', (status) => {
    render(h(Theme, null, h(StepList, { tools: [refused('web_address', status)], run } as never)));
    const reason = screen.getByTestId('activity-tool-reason');
    expect(reason.textContent).toBe(i18n.t('activityPanel.tool.webAddressRefused'));
    expect(reason.textContent).not.toContain('web_address');
  });

  it('another rule keeps the rule wording', () => {
    render(h(Theme, null, h(StepList, { tools: [refused('policy')], run } as never)));
    expect(screen.getByTestId('activity-tool-reason').textContent).toBe(
      i18n.t('activityPanel.tool.blockedBy', { rule: 'policy' }),
    );
  });
});
