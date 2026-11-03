import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims } from '../../__tests__/agent-builder-harness';

const viewport = vi.hoisted(() => ({ mobile: false, belowDesktop: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({
  useIsMobile: () => viewport.mobile,
  useIsBelowDesktop: () => viewport.belowDesktop,
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/config', () => ({ PermissionLockIcon: () => <span>Locked</span> }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds: vi.fn(async () => []) } }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AgentBuilderHeader } from '../agent-builder-header';

function renderHeader(overrides: Partial<React.ComponentProps<typeof AgentBuilderHeader>> = {}) {
  return render(
    <Theme>
      <AgentBuilderHeader
        agentName="Support bot"
        onAgentNameChange={() => {}}
        saving={false}
        onSave={() => {}}
        shareWithOrg={false}
        onShareWithOrgChange={() => {}}
        isFlowStructureLocked={false}
        canPersist
        isServiceAccount={false}
        editing
        onEnableServiceAccount={() => {}}
        {...overrides}
      />
    </Theme>
  );
}

beforeEach(() => installBrowserShims());

afterEach(() => {
  cleanup();
  viewport.mobile = false;
  viewport.belowDesktop = false;
});

describe('AgentBuilderHeader', () => {
  it('on a tablet, keeps the service-agent action in the … menu so nothing covers the name field', async () => {
    viewport.belowDesktop = true;
    renderHeader();

    expect(screen.queryByRole('button', { name: /convert to service agent/i })).toBeNull();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Agent menu' }), { key: 'Enter' });
    expect(await screen.findByRole('menuitem', { name: /convert to service agent/i })).toBeTruthy();
  });

  it('on a desktop, shows the service-agent action as a secondary button next to save', () => {
    renderHeader();

    const convert = screen.getByRole('button', { name: /convert to service agent/i });
    const save = screen.getByRole('button', { name: /save changes/i });
    expect(convert.className).toContain('rt-variant-soft');
    // In the right-hand action group (after the share control, before save), not under the name field.
    const share = screen.getByText('Share with everyone');
    expect(share.compareDocumentPosition(convert) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(save.compareDocumentPosition(convert) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('while the agent loads, says "Save changes", disables actions and the name field', () => {
    renderHeader({ editing: true, loading: true, isDirty: true, agentName: '' });

    expect(screen.queryByText(/create agent/i)).toBeNull();
    expect((screen.getByRole('button', { name: /save changes/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /convert to service agent/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true);
  });
});
