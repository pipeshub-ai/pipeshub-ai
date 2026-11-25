/**
 * CPD-11 U6: in a saved agent's builder, an item a run could not use gets a
 * badge saying why, linking to the page where the reader can fix it (never
 * a page they cannot open). No badge for ready items, a new agent, or when
 * the grantable list cannot be read.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import type { GrantableItem } from '../../../workspace/harness-ui/control-plane-api';
import { CapabilityBadge, skillInstallationId, useGrantable } from '../components/capability-badge';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
let admin = false;
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean }) => unknown) => selector({ isAdmin: admin }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
}));
vi.mock('next/link', () => ({
  default: ({ href, children, style }: { href: string; children: React.ReactNode; style?: React.CSSProperties }) => (
    <a href={href} style={style}>
      {children}
    </a>
  ),
}));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  admin = false;
});

const item = (over: Partial<GrantableItem>): GrantableItem => ({
  installation: 'inst-1',
  item: 'mcp:pipeshub/linear@3',
  kind: 'mcp',
  granted: false,
  state: 'unavailable',
  reason: null,
  ...over,
});

const badge = (value: GrantableItem | undefined) => {
  render(
    <Theme>
      <CapabilityBadge item={value} />
    </Theme>,
  );
  return document.querySelector('[data-capability-state]');
};

describe('U6 capability badges', () => {
  it('a missing connection links to the reader’s own connections page for its kind', () => {
    const mcp = badge(item({ reason: 'credential_missing' }));
    expect(mcp?.textContent).toContain('Needs your connection');
    expect(mcp?.querySelector('a')?.getAttribute('href')).toBe('/workspace/mcp-servers/personal');
    cleanup();
    expect(badge(item({ kind: 'toolset', reason: 'credential_expired' }))?.querySelector('a')?.getAttribute('href')).toBe(
      '/workspace/actions/personal',
    );
  });

  it('admin-only remedies link for an admin and are plain text for anyone else', () => {
    expect(badge(item({ reason: 'not_approved' }))?.querySelector('a')).toBeNull();
    cleanup();
    admin = true;
    expect(badge(item({ reason: 'not_approved' }))?.querySelector('a')?.getAttribute('href')).toBe('/workspace/mcp-servers/team');
    cleanup();
    expect(badge(item({ kind: 'skill', reason: 'policy_ceiling' }))?.querySelector('a')?.getAttribute('href')).toBe(
      '/workspace/ai-access',
    );
  });

  it('disabled and not shared say so without a link; ready and unknown items show nothing', () => {
    expect(badge(item({ reason: 'disabled' }))?.textContent).toContain('Disabled');
    cleanup();
    const hidden = badge(item({ reason: 'not_visible' }));
    expect(hidden?.textContent).toContain('Not shared with you');
    expect(hidden?.querySelector('a')).toBeNull();
    cleanup();
    expect(badge(item({ state: 'ready' }))).toBeNull();
    cleanup();
    expect(badge(undefined)).toBeNull();
  });

  it('reads grantable once for a saved agent; none for a new agent or when it cannot be read', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [{ installation: 'inst-1', item: 'x', kind: 'mcp', granted: true, state: 'ready', reason: null }] },
    });
    const saved = renderHook(() => useGrantable('a7'));
    await waitFor(() => expect(saved.result.current?.get('inst-1')?.state).toBe('ready'));
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/control-plane/agents/a7/grantable', { suppressErrorToast: true });
    expect(renderHook(() => useGrantable(null)).result.current).toBeNull();
    vi.mocked(apiClient.get).mockRejectedValueOnce({ statusCode: 503 });
    const off = renderHook(() => useGrantable('a8'));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(2));
    expect(off.result.current).toBeNull();
    expect(screen.queryByText('Needs your connection')).toBeNull();
  });

  it('names a skill’s installation as the control plane does', () => {
    expect(skillInstallationId('notes & todo')).toBe('skill_notes-todo');
  });
});
