/**
 * MA1 QA37-X4: the activity panel's header names the model the run called,
 * under its configured display name; a run that names none shows the header
 * as before.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ActivityView, AvailableLlmModel, DurableAgentRunHandle } from '@/chat/types';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() }, streamSSEGet: vi.fn() }));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { reduceActivity, toActivityView } from '@/chat/activity';
import type { RunActivityState } from '@/chat/hooks/use-run-activity';
import { useChatStore } from '@/chat/store';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const initialChat = useChatStore.getState();
const HAIKU = { provider: 'anthropic', model: 'claude-haiku-4-5' };

const snapshot = (patch: Record<string, unknown> = {}) => ({
  runId: RUN.runId,
  status: 'running',
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

/** The rendered markup without React's generated ids, which differ from one mount to the next (`«r1»` before React 19.2, `_r_1_` since). */
const markup = (container: HTMLElement): string =>
  container.innerHTML.replace(/«r[0-9a-z]+»|_r_[0-9a-z]+_/g, '«id»');

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useChatStore.setState((state) => ({
    settings: {
      ...state.settings,
      availableModels: {
        'agent-1': {
          models: [
            { modelKey: 'k1', provider: 'anthropic', modelName: 'claude-haiku-4-5', modelFriendlyName: 'Claude Haiku 4.5' } as AvailableLlmModel,
          ],
          fetchedAt: 0,
        },
      },
    },
  }));
});
afterEach(cleanup);

describe('QA37-X4: the activity panel names the run’s model', () => {
  it('QA37-X4: the header shows the model under its configured display name', () => {
    renderPanel(toActivityView(snapshot({ model: HAIKU })));

    const panel = within(screen.getByRole('complementary', { name: 'Activity' }));
    expect(panel.getByTestId('activity-model').textContent).toBe('Model: Claude Haiku 4.5');
  });

  it('QA37-X4: a model the catalog does not know is shown by its own name', () => {
    renderPanel(toActivityView(snapshot({ model: { provider: 'openai', model: 'gpt-5.4-mini' } })));

    expect(screen.getByTestId('activity-model').textContent).toBe('Model: gpt-5.4-mini');
  });

  it('QA37-X4: a run that names no model, or a malformed one, renders the header as before', () => {
    const { container } = renderPanel(toActivityView(snapshot()));
    const before = markup(container);
    expect(screen.queryByTestId('activity-model')).toBeNull();
    expect(screen.queryByText(/^Model:/)).toBeNull();
    cleanup();

    const malformed = toActivityView(snapshot({ model: { provider: 'anthropic', model: 7 } }));
    expect(malformed).not.toHaveProperty('model');
    expect(markup(renderPanel(malformed).container)).toBe(before);
  });

  it('QA37-X4: the view keeps only the provider and model, and live events do not drop it', () => {
    const view = toActivityView(snapshot({ model: { ...HAIKU, apiKey: 'sk-secret' } }));
    expect(view?.model).toEqual(HAIKU);

    const next = reduceActivity(view, {
      type: 'command',
      command: {
        commandId: 'cmd-1',
        source: 'run_code',
        command: 'pytest -q',
        status: 'running',
        exitCode: null,
        startedAt: 1,
        durationMs: null,
        stdout: '',
        stderr: '',
        truncated: false,
        liveDropped: 0,
        hasLog: false,
      },
    });
    expect(next?.model).toEqual(HAIKU);
  });
});
