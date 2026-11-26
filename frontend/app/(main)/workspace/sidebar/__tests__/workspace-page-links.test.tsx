/**
 * Shared views link to a page a build adds only when the build names it in `workspacePageLinks`; this build names
 * none, so each such view shows its text without a link, never a link to a page that is not there.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { i18n } from '@/lib/i18n';
import { useUserStore } from '@/lib/store/user-store';
import { workspacePageLinks } from '@/config';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/chat/components/activity-panel/activity-panel-host', () => ({ ViewActivityButton: () => null }));

const { BuiltinResultRow } = await import('@/app/(main)/chat/components/message-area/builtin-result-rows');
const { CapabilityBadge } = await import('@/app/(main)/agents/agent-builder/components/capability-badge');
const { UnknownEffectCard } = await import('@/app/(main)/chat/components/message-area/unknown-effect-card');

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  useUserStore.setState({ profile: null } as never);
});

const asAdmin = () => useUserStore.setState({ profile: { isAdmin: true } } as never);
const show = (node: React.ReactElement) => render(<Theme>{node}</Theme>);

describe('workspacePageLinks', () => {
  it('names no page in this build', () => {
    expect(workspacePageLinks).toEqual({});
  });

  it("/permissions shows an admin no link to change the rules", () => {
    asAdmin();
    show(
      <BuiltinResultRow
        rowKey="slot-1"
        row={{
          id: 'r1',
          typed: '/permissions',
          afterPairs: 1,
          result: { kind: 'permissions', permissions: { approve: true, deny: true }, admin: true },
        }}
      />,
    );
    expect(screen.getByText('You can approve')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it("a builder badge of a tool waiting for an org's approval shows no link", () => {
    asAdmin();
    show(
      <CapabilityBadge
        item={{ installation: 'inst-1', item: 'jira', kind: 'toolset', granted: false, state: 'blocked', reason: 'not_approved' }}
      />,
    );
    expect(document.querySelector('[data-capability-state="waitingApproval"]')).not.toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('an unknown effect offers an admin no retry link and no note about one', () => {
    asAdmin();
    show(
      <UnknownEffectCard
        effect={{
          interactionId: 'ix.unknown.t1.0',
          toolCallId: 'call-1',
          toolName: 'slack__send_message',
          displayName: 'Send message',
          args: '',
          kind: 'unknown_effect',
          status: 'pending',
          errorCode: null,
        }}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByText(/an admin can try it again/)).toBeNull();
  });
});
