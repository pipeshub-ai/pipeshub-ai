/**
 * MCPUSE-1, ADR-OWD-07 D-DISC-2: a job's Brief names the connected sources it could reach, as its task names them to
 * the engine: a Codex job in a manual test was granted Atlassian Rovo's tools and neither it nor the person was told the server
 * was there. Each source by its name with what it covers, under "What it was given"; none for a job handed none.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18n from '@/lib/__tests__/test-i18n';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/api', () => ({
  apiClient: { get: (...args: unknown[]) => api.get(...args), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSEGet: () => new Promise<void>(() => undefined),
}));

import { JobBriefBody } from '../job-brief';
import { briefView } from '@/lib/harness-jobs/job-brief';

const REQUEST = "Prepare a risk report for 'SAAS launch'";
type Sources = NonNullable<NonNullable<JobBrief['task']>['sources']>;
const ROVO: Sources = [
  { name: 'Atlassian Rovo', kind: 'mcp', summary: 'Jira, Confluence, Bitbucket, and more via the Atlassian Rovo MCP Server.', tools: 32 },
  { name: 'jira', kind: 'toolset', summary: '', tools: 2 },
];

const brief = (sources?: Sources): JobBrief => ({
  runId: 'job-1',
  parentRunId: 'run-1',
  sessionId: 'conv-1',
  agent: { kind: 'engine', engine: 'codex' },
  goal: REQUEST,
  task: { request: REQUEST, notes: '', earlier: '', fileName: null, dueAt: null, maxCostUsd: null, carried: [], ...(sources ? { sources } : {}) },
  sent: { files: [], sources: [], sourcesComplete: true, knowledge: { apps: [], collections: [] }, skills: [], continuesRunId: null },
  withheld: [{ what: 'conversation', reason: 'task_only' }],
  reach: { internet: 'none', sandbox: true, tools: [], allowedWithoutAsking: 0 },
});

const words = (key: string, vars?: Record<string, unknown>) => i18n.t(key, vars) as string;

async function show(value: JobBrief) {
  api.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/brief')) return { data: value };
    if (url.endsWith('/brief/history')) return { data: { runId: 'job-1', startedAs: REQUEST, changes: [] } };
    return { data: {} };
  });
  render(
    <Theme>
      <JobBriefBody run={{ sessionId: 'conv-1', runId: 'job-1' } as never} />
    </Theme>,
  );
  await waitFor(() => screen.getByTestId('job-brief'));
  fireEvent.click(screen.getByRole('button', { name: 'What it was given' }));
  return screen.getByTestId('job-brief-given');
}

beforeEach(() => {
  api.get.mockReset();
  void i18n.changeLanguage('en-US');
});
afterEach(cleanup);

describe('MCPUSE-1: the Brief names the connected sources', () => {
  it('lists each source by its name with what it covers, under Connected sources', async () => {
    const given = await show(brief(ROVO));
    expect(given.textContent).toContain('Connected sources');
    expect(given.textContent).toContain('Atlassian Rovo');
    expect(given.textContent).toContain('Jira, Confluence, Bitbucket, and more via the Atlassian Rovo MCP Server.');
    expect(briefView(brief(ROVO), words).sources).toEqual([
      { icon: 'hub', text: 'Atlassian Rovo', note: ROVO[0].summary },
      { icon: 'apps', text: 'jira' },
    ]);
  });

  it('a job handed no sources, or kept before they were, shows no such section', async () => {
    const given = await show(brief());
    expect(given.textContent).not.toContain('Connected sources');
    expect(briefView({ ...brief(), task: null }, words).sources).toEqual([]);
  });

  it('every locale has the label', () => {
    for (const locale of ['en-US', 'en-IN', 'de-DE', 'es-ES', 'cs-CZ', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW']) {
      expect(i18n.exists('jobPanel.connectedSources', { lng: locale }), locale).toBe(true);
    }
  });
});
