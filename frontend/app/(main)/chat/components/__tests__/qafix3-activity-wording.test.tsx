/**
 * QAFIX3 W1 and W2: the Activity drawer of a person's `/run` says they ran it ("What you ran …") and names no model,
 * though a model may ride the snapshot; sizes read with a proper plural ("403 bytes", not "403 byte").
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ActivityView, DurableAgentRunHandle } from '@/chat/types';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, streamSSEGet: vi.fn() }));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { formatBytes, toActivityView } from '@/chat/activity';
import type { RunActivityState } from '@/chat/hooks/use-run-activity';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const MODEL = { provider: 'openai', model: 'gpt-6-luna' };

const snapshot = (patch: Record<string, unknown> = {}) => ({
  runId: RUN.runId,
  status: 'completed',
  engine: false,
  commands: [],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [],
  followups: [],
  ...patch,
});

function renderPanel(view: ActivityView | null) {
  const state: RunActivityState = { view, connection: 'open' };
  return render(
    <Theme>
      <ActivityPanelView run={RUN} state={state} variant="pane" onClose={() => {}} />
    </Theme>,
  );
}

afterEach(cleanup);

describe('QAFIX3 W1: the Activity drawer of a /run', () => {
  it('says the person ran it and names no model', () => {
    const view = toActivityView(snapshot({ personRan: true, model: MODEL }));
    expect(view).toMatchObject({ personRan: true });
    expect(view).not.toHaveProperty('model');
    renderPanel(view);
    expect(screen.getByTestId('activity-subtitle').textContent).toBe("What you ran in this chat's sandbox. Read-only.");
    expect(screen.queryByTestId('activity-model')).toBeNull();
  });

  it("an agent's run keeps its wording and its model", () => {
    renderPanel(toActivityView(snapshot({ model: MODEL })));
    expect(screen.getByTestId('activity-subtitle').textContent).toBe('What the agent ran in its sandbox. Read-only.');
    expect(screen.getByTestId('activity-model').textContent).toBe('Model: gpt-6-luna');
  });
});

describe('QAFIX3 W2: sizes', () => {
  it('a byte count has its plural in the reader\'s locale; larger sizes keep their short units', () => {
    expect(formatBytes(1, 'en-US')).toBe('1 byte');
    expect(formatBytes(403, 'en-US')).toBe('403 bytes');
    expect(formatBytes(63, 'en-IN')).toBe('63 bytes');
    expect(formatBytes(403, 'cs-CZ')).toBe('403 bajtů');
    expect(formatBytes(12 * 1_024, 'en-US')).toBe('12 kB');
    expect(formatBytes(1.5 * 1_048_576, 'en-US')).toBe('1.5 MB');
  });
});
