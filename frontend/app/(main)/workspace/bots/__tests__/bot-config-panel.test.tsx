import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/(main)/workspace/components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  WorkspaceRightPanelBodyPortalContext: React.createContext<HTMLElement | null>(null),
}));
vi.mock('@/app/(main)/workspace/components/destructive-typed-confirmation-dialog', () => ({
  DestructiveTypedConfirmationDialog: () => null,
}));
vi.mock('@/lib/hooks/use-secret-reveal-available', () => ({
  useSecretRevealAvailable: () => false,
  useRevealScope: () => () => () => true,
  REVEAL_PARAMS: {},
}));
vi.mock('../api', () => ({
  BotsApi: {
    getSlackBotConfigs: vi.fn(),
    revealSlackBotConfig: vi.fn(),
    createSlackBotConfig: vi.fn(),
    updateSlackBotConfig: vi.fn(),
    deleteSlackBotConfig: vi.fn(),
  },
}));

import { BotConfigPanel } from '../components/bot-config-panel';
import { useBotsStore } from '../store';

const config = (agentId: string | null) => ({
  id: 'bot-1',
  name: 'testing BOT',
  botToken: 'xoxb-token',
  signingSecret: 'secret',
  agentId: agentId as string | undefined,
});

function openEditing(agentId: string | null, agents: { id: string; name: string }[], complete = true) {
  const store = useBotsStore.getState();
  store.setConfigs([config(agentId)]);
  store.setAgents(agents, complete);
  store.openPanel();
  store.setPanelView('slack-form');
  store.setEditingBot('bot-1');
  render(
    <Theme>
      <BotConfigPanel />
    </Theme>,
  );
}

describe('BotConfigPanel — the agent a saved bot is linked to', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    useBotsStore.getState().reset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
    useBotsStore.getState().reset();
  });

  it('shows the linked agent by name', () => {
    openEditing('agent-7', [{ id: 'agent-7', name: 'Support agent' }]);

    expect(screen.getByRole('combobox').textContent).toContain('Support agent');
  });

  it('shows the default assistant when the linked agent was deleted, not its id', () => {
    openEditing('agent-deleted', [{ id: 'agent-1', name: 'Other agent' }]);

    const text = screen.getByRole('combobox').textContent;
    expect(text).toContain('Default assistant');
    expect(text).not.toContain('agent-deleted');
  });

  it('keeps the linked agent selected while the list is incomplete: it is not known to be deleted', () => {
    openEditing('agent-7', [{ id: 'agent-1', name: 'Other agent' }], false);

    expect(screen.getByRole('combobox').textContent).toContain('agent-7');
  });

  it('keeps the linked agent selected before any list has arrived', () => {
    openEditing('agent-7', [], false);

    expect(screen.getByRole('combobox').textContent).toContain('agent-7');
  });

  it('shows the default assistant for a bot with no agent', () => {
    openEditing(null, [{ id: 'agent-1', name: 'Other agent' }]);

    expect(screen.getByRole('combobox').textContent).toContain('Default assistant');
  });
});
