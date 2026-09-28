import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { en } from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '../../../types';

const api = vi.hoisted(() => ({
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  discoverOAuthMetadata: vi.fn(),
  getOAuthConfig: vi.fn(),
  getInstanceTools: vi.fn(),
  authenticate: vi.fn(),
  updateOAuthConfig: vi.fn(),
}));
vi.mock('../../../api', () => ({ McpServersApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

const form = en.workspace.mcpServers.form;

function customStdioInstance(args: string[]): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'admin-1',
    name: 'My server',
    typeId: null,
    transport: 'stdio',
    authMode: 'none',
    useAdminAuth: false,
    command: 'npx',
    args,
    requiredEnv: ['API_KEY'],
    optionalEnv: [],
    scopes: [],
    isCustom: true,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [],
  };
}

function renderPanel(mode: 'create' | 'edit', editingInstance: McpMyServerEntry | null = null) {
  renderInTheme(
    <McpInstanceConfigPanel
      state={{ open: true, mode, editingInstance, prefillTemplate: null }}
      templates={[]}
      instances={editingInstance ? [editingInstance] : []}
      onOpenChange={vi.fn()}
      onSaved={vi.fn()}
      onRequestDelete={vi.fn()}
      busyInstanceId={null}
      onAuthenticate={vi.fn()}
      onReauthenticate={vi.fn()}
      onDisconnect={vi.fn()}
    />
  );
}

function saveButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
}

beforeEach(() => {
  installBrowserShims();
  api.updateInstance.mockImplementation(async (id: string) => ({ _id: id }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpInstanceConfigPanel custom STDIO', () => {
  it('warns that the command runs on the PipesHub server', () => {
    renderPanel('create');
    expect(screen.getByText(form.stdioWarningTitle)).toBeTruthy();
    expect(screen.getByText(form.stdioWarning)).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: form.stdioAcknowledge })).toBeTruthy();
  });

  it('requires the acknowledgement before saving and sends it', async () => {
    renderPanel('edit', customStdioInstance(['-y', 'pkg@1.2.3']));
    expect(saveButton().disabled).toBe(true);

    fireEvent.click(screen.getByRole('checkbox', { name: form.stdioAcknowledge }));
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());

    await waitFor(() => expect(api.updateInstance).toHaveBeenCalledTimes(1));
    expect(api.updateInstance.mock.calls[0][1]).toMatchObject({
      command: 'npx',
      args: ['-y', 'pkg@1.2.3'],
      acknowledgeUnsandboxedExecution: true,
    });
  });

  it('explains an unpinned package and keeps save disabled', () => {
    renderPanel('edit', customStdioInstance(['-y', 'pkg@latest']));
    fireEvent.click(screen.getByRole('checkbox', { name: form.stdioAcknowledge }));

    expect(screen.getByText(/Pin "pkg@latest" to an exact version \(for example name@1\.2\.3\)/)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
  });
});
