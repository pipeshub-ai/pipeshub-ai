/**
 * WP-52 C4.08: "Export configuration…" is in the agent
 * builder's overflow menu, for anyone who can open the agent, delete or not;
 * a new agent (nothing saved yet) has nothing to export.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds: vi.fn(async () => []) } }));

import { AgentBuilderHeader } from '../agent-builder-header';

beforeAll(async () => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
  await i18n.changeLanguage('en-US');
});
afterEach(cleanup);

const header = (extra: Partial<React.ComponentProps<typeof AgentBuilderHeader>>) =>
  render(
    <Theme>
      <AgentBuilderHeader
        agentName="Support triage"
        onAgentNameChange={vi.fn()}
        saving={false}
        onSave={vi.fn()}
        shareWithOrg={false}
        onShareWithOrgChange={vi.fn()}
        isFlowStructureLocked
        canPersist={false}
        isServiceAccount={false}
        editing
        {...extra}
      />
    </Theme>,
  );

const openMenu = () => {
  const trigger = screen.getByRole('button', { name: i18n.t('agentBuilder.agentActionsMenuAria') });
  fireEvent.pointerDown(trigger, { button: 0, pointerType: 'mouse' });
  fireEvent.keyDown(trigger, { key: 'Enter' });
};

describe('Agent builder overflow menu: Export configuration', () => {
  it('BX-1: someone who can only open the agent still gets Export in the menu, and it opens the export', async () => {
    const onExport = vi.fn();
    header({ onExport });
    openMenu();
    const item = await screen.findByText('Export configuration…');
    expect(screen.queryByText(i18n.t('chat.deleteAgent'))).toBeNull();
    fireEvent.click(item);
    expect(onExport).toHaveBeenCalled();
  });

  it('BX-2: a new agent has no menu to export from', () => {
    header({ editing: false, onExport: vi.fn() });
    expect(screen.queryByRole('button', { name: i18n.t('agentBuilder.agentActionsMenuAria') })).toBeNull();
  });
});
