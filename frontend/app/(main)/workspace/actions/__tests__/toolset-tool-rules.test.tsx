import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
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

import { ToolsetToolRulesDialog } from '../components/toolset-tool-rules-dialog';
import { ToolsetInstanceRowCard } from '../components/toolset-instance-row-card';
import { ruleToolsFromSchema } from '../utils/tool-names-from-schema';

const SCHEMA = {
  status: 'success',
  toolset: {
    name: 'jira',
    tools: [
      { name: 'search_issues', description: 'Search Jira issues.', kind: 'read' },
      { name: 'create_issue', description: 'Create a Jira issue.', kind: 'write' },
      { name: 'delete_issue', description: 'Delete a Jira issue.', kind: 'destructive' },
    ],
  },
};

function openDialog() {
  const onOpenChange = vi.fn();
  renderInTheme(
    <ToolsetToolRulesDialog open onOpenChange={onOpenChange} instanceId="inst-j" instanceName="Jira (Engineering)" toolsetType="jira" />
  );
  return onOpenChange;
}

function ruleRow(name: string) {
  return within(screen.getByRole('radiogroup', { name }));
}

/** The segment that is on (Radix draws each label twice, so it's found by its accessible name). */
function chosen(name: string): string | undefined {
  return ['Pre-approved', 'Allow on approval', 'Deny'].find((label) =>
    ruleRow(name).queryByRole('radio', { name: label, checked: true })
  );
}

beforeEach(() => {
  installBrowserShims();
  toolsets.getToolsetRegistrySchema.mockResolvedValue(SCHEMA);
  toolsets.getToolRules.mockResolvedValue({ tools: {}, canEdit: true });
  toolsets.updateToolRules.mockResolvedValue({ tools: {}, canEdit: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ruleToolsFromSchema', () => {
  it("reads each tool's name, description and kind", () => {
    expect(ruleToolsFromSchema(SCHEMA)[0]).toEqual({ name: 'search_issues', description: 'Search Jira issues.', kind: 'read' });
  });

  it('leaves an unknown kind out (it starts as a tool that changes data) and skips nameless tools', () => {
    expect(ruleToolsFromSchema({ toolset: { tools: [{ name: 'x', kind: 'wipe' }, { kind: 'read' }, null] } })).toEqual([{ name: 'x' }]);
    expect(ruleToolsFromSchema(null)).toEqual([]);
  });
});

describe('ToolsetToolRulesDialog', () => {
  it("shows the instance's rules, each tool starting from what it does to data", async () => {
    toolsets.getToolRules.mockResolvedValue({ tools: { search_issues: 'block' }, canEdit: true });
    openDialog();

    await screen.findByRole('radiogroup', { name: 'search_issues' });
    expect(toolsets.getToolsetRegistrySchema).toHaveBeenCalledWith('jira');
    expect(toolsets.getToolRules).toHaveBeenCalledWith('inst-j');
    expect(screen.getByText('Tool approvals: Jira (Engineering)')).toBeTruthy();
    expect(chosen('search_issues')).toBe('Deny');
    // A toolset's writes start Pre-approved and its deleting tools on approval.
    expect(chosen('create_issue')).toBe('Pre-approved');
    expect(chosen('delete_issue')).toBe('Allow on approval');
    expect(screen.queryByText('Only an admin can change these.')).toBeNull();
  });

  it("an admin saves what differs from the starting rules, and a rule for a tool the toolset dropped goes", async () => {
    toolsets.getToolRules.mockResolvedValue({ tools: { retired_tool: 'allow' }, canEdit: true });
    const onOpenChange = openDialog();

    await screen.findByRole('radiogroup', { name: 'create_issue' });
    expect(screen.queryByRole('radiogroup', { name: 'retired_tool' })).toBeNull();
    fireEvent.click(ruleRow('create_issue').getByRole('radio', { name: 'Allow on approval' }));
    fireEvent.click(ruleRow('search_issues').getByRole('radio', { name: 'Pre-approved' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(toolsets.updateToolRules).toHaveBeenCalledWith('inst-j', { tools: { create_issue: 'ask' } });
  });

  it('shows a member the rules without letting them change them', async () => {
    toolsets.getToolRules.mockResolvedValue({ tools: { create_issue: 'allow' }, canEdit: false });
    openDialog();

    await screen.findByText('Only an admin can change these.');
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
    // search_issues and create_issue are Pre-approved; delete_issue starts on approval.
    expect(screen.getAllByText('Pre-approved')).toHaveLength(2);
    expect(screen.getAllByText('Allow on approval')).toHaveLength(1);
    expect(screen.queryByText('Deny')).toBeNull();
  });

  it("says so when the toolset's tools can't be loaded", async () => {
    toolsets.getToolsetRegistrySchema.mockRejectedValue(new Error('down'));
    openDialog();

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText("Couldn't load this toolset's tools.")).toBeTruthy();
  });
});

describe('ToolsetInstanceRowCard', () => {
  const instance = {
    name: 'jira',
    displayName: 'Jira',
    instanceId: 'inst-j',
    instanceName: 'Jira (Engineering)',
    toolsetType: 'jira',
    isAuthenticated: true,
    tools: [{ name: 'create_issue', fullName: 'jira.create_issue', description: '' }],
  } as BuilderSidebarToolset;

  function card(scope: 'team' | 'personal') {
    renderInTheme(<ToolsetInstanceRowCard scope={scope} instance={instance} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />);
  }

  it('lets everyone open the tool approvals from My Actions', async () => {
    card('personal');

    fireEvent.click(screen.getByRole('button', { name: /Tool approvals/ }));

    expect(await screen.findByText('Tool approvals: Jira (Engineering)')).toBeTruthy();
    expect(toolsets.getToolRules).toHaveBeenCalledWith('inst-j');
    expect(screen.getByText(/The same rules apply to everyone in your organization/)).toBeTruthy();
  });

  it("says a personal action's rules are the owner's own", async () => {
    renderInTheme(
      <ToolsetInstanceRowCard scope="personal" instance={{ ...instance, scope: 'personal' }} onAuthenticate={vi.fn()} onConfigure={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Tool approvals/ }));

    expect(await screen.findByText(/in your chats/)).toBeTruthy();
    expect(screen.queryByText(/The same rules apply to everyone/)).toBeNull();
  });

  it("leaves them to Manage on the admins' page", () => {
    card('team');
    expect(screen.queryByRole('button', { name: /Tool approvals/ })).toBeNull();
  });
});
