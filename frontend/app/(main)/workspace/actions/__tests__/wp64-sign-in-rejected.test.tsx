import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

const toolsets = vi.hoisted(() => ({
  getToolsetRegistrySchema: vi.fn(),
  getToolRules: vi.fn(),
  updateToolRules: vi.fn(),
}));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: toolsets,
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));
vi.mock('@/lib/api', () => ({ apiClient: { post: vi.fn(() => new Promise(() => {})) }, isProcessedError: () => false }));
vi.mock('@/app/components/ui', () => ({ ConnectorIcon: () => null }));
// The card opens whichever rules dialog the edition exports; these tests are about the editor.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  ToolsetToolRulesDialog: (await import('../components/toolset-tool-rules-dialog')).ToolsetToolRulesDialog,
}));

import { ToolsetInstanceRowCard } from '../components/toolset-instance-row-card';
import { rejectedTime } from '@/app/(main)/chat/connect-cards';
import { mapToSidebar } from '@/app/(main)/toolsets/api';

/**
 * WP-64: a sign-in the harness withdrew because the service rejected it shows on My Actions with why and
 * when, so the person knows to sign in again; a sign-in that is there, or one never made, says nothing more.
 */
beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

const base = {
  name: 'github',
  displayName: 'GitHub',
  instanceId: 'gh-1',
  instanceName: 'GitHub (Eng)',
  toolsetType: 'github',
  authType: 'API_TOKEN',
  tools: [],
} as unknown as BuilderSidebarToolset;

describe('WP-64 a rejected sign-in on My Actions', () => {
  it('WP64-FE-7: says the service rejected the saved sign-in and when', () => {
    const at = Date.parse('2026-11-02T00:29:19.000Z');
    renderInTheme(
      <ToolsetInstanceRowCard scope="personal" instance={{ ...base, isAuthenticated: false, deauthReason: 'sign_in_rejected', deauthAt: at }} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />,
    );
    expect(screen.getByTestId('toolset-sign-in-rejected').textContent).toBe(
      `GitHub rejected the saved sign-in at ${rejectedTime(new Date(at).toISOString())}. Sign in again to use it.`,
    );
  });

  it('WP64-FE-8: a sign-in that is there, or never made, shows no reason', () => {
    renderInTheme(<ToolsetInstanceRowCard scope="personal" instance={{ ...base, isAuthenticated: true }} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />);
    renderInTheme(<ToolsetInstanceRowCard scope="personal" instance={{ ...base, isAuthenticated: false }} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />);
    expect(screen.queryByTestId('toolset-sign-in-rejected')).toBeNull();
  });

  it('WP64-FE-9: the list carries the reason and its time from the server', () => {
    const row = mapToSidebar({ toolsetType: 'github', instanceId: 'gh-1', isAuthenticated: false, deauthReason: 'sign_in_rejected', deauthAt: 7 });
    expect(row).toMatchObject({ isAuthenticated: false, deauthReason: 'sign_in_rejected', deauthAt: 7 });
    expect(mapToSidebar({ toolsetType: 'github', isAuthenticated: true })).not.toHaveProperty('deauthReason');
  });
});
