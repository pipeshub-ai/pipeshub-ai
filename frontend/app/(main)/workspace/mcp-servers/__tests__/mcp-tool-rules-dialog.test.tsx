import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpRuleTool } from '../tool-rules';

const api = vi.hoisted(() => ({
  getMyToolRules: vi.fn(),
  updateMyToolRules: vi.fn(),
  getToolPolicy: vi.fn(),
  updateToolPolicy: vi.fn(),
  getAgentToolRules: vi.fn(),
  updateAgentToolRules: vi.fn(),
}));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../api', () => ({ McpServersApi: api }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));

import { McpToolRulesDialog, type McpToolRulesTarget } from '../components/mcp-tool-rules-dialog';

const TOOLS: McpRuleTool[] = [
  { name: 'search', title: 'Search issues', kind: 'read', kindSource: 'server' },
  { name: 'create_issue', kind: 'write', kindSource: 'none' },
  { name: 'delete_issue', kind: 'destructive', kindSource: 'name' },
];

function open(target: McpToolRulesTarget, props: { tools?: McpRuleTool[]; readOnly?: boolean } = {}) {
  const onOpenChange = vi.fn();
  renderInTheme(
    <McpToolRulesDialog
      open
      onOpenChange={onOpenChange}
      target={target}
      serverName="Jira"
      tools={props.tools ?? TOOLS}
      readOnly={props.readOnly}
    />
  );
  return onOpenChange;
}

function ruleRow(name: string) {
  return within(screen.getByRole('group', { name }));
}

/** The segment that is on (Radix draws each label twice, so it's found by its accessible name). */
function chosen(name: string): string | undefined {
  const labels = ['Pre-approved', 'Allow on approval', 'Deny', 'No limit'];
  return labels.find((label) => ruleRow(name).queryByRole('radio', { name: label, checked: true }));
}

beforeEach(() => {
  installBrowserShims();
  api.updateMyToolRules.mockResolvedValue({ tools: {} });
  api.updateToolPolicy.mockResolvedValue({ tools: {} });
  api.updateAgentToolRules.mockResolvedValue({ tools: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('McpToolRulesDialog — a person’s own rules', () => {
  it('shows each tool where it starts, or the rule saved for it', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: { search: 'block' } });
    open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    expect(api.getMyToolRules).toHaveBeenCalledWith('inst-1');
    expect(chosen('search')).toBe('Deny');
    expect(chosen('create_issue')).toBe('Allow on approval');
    expect(chosen('delete_issue')).toBe('Deny');
  });

  it('groups the tools by what they do to data, deleting ones first, and says where that came from', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: {} });
    open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    const groups = screen.getAllByTestId(/^tool-group-/).map((group) => group.dataset.testid);
    expect(groups).toEqual(['tool-group-destructive', 'tool-group-write', 'tool-group-read']);
    const deleting = within(screen.getByTestId('tool-group-destructive'));
    expect(deleting.getByText('Review these first')).toBeTruthy();
    expect(deleting.getByText('From its name')).toBeTruthy();
    expect(within(screen.getByTestId('tool-group-read')).getByText('As the server says')).toBeTruthy();
    expect(within(screen.getByTestId('tool-group-write')).getByText('No labels from the server')).toBeTruthy();
  });

  it('filters by kind and by having a rule, and clears back to everything', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: { search: 'block' } });
    open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    fireEvent.click(screen.getByRole('button', { name: 'Deletes data' }));
    expect(screen.queryByRole('group', { name: 'search' })).toBeNull();
    expect(screen.getByRole('group', { name: 'delete_issue' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Has a rule' }));
    expect(screen.getByRole('group', { name: 'search' })).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'delete_issue' })).toBeNull();
    fireEvent.change(screen.getByPlaceholderText('Search by name or ID'), { target: { value: 'zzz' } });
    expect(screen.getByText('No tools match your search.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear search and filters' }));
    expect(screen.getByRole('group', { name: 'create_issue' })).toBeTruthy();
  });

  it('saves a deleting tool someone moved to Allow on approval, and nothing for one left on Deny', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: {} });
    open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'delete_issue' });
    fireEvent.click(ruleRow('delete_issue').getByRole('radio', { name: 'Allow on approval' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: { delete_issue: 'ask' } }));
  });

  it('saves only what differs from the starting rules, then closes', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: {} });
    const onOpenChange = open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'create_issue' });
    fireEvent.click(ruleRow('create_issue').getByRole('radio', { name: 'Pre-approved' }));
    fireEvent.click(ruleRow('search').getByRole('radio', { name: 'Pre-approved' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(api.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: { create_issue: 'allow' } });
    expect(toasts.success).toHaveBeenCalledWith('Tool approvals saved');
  });

  it('adds a tool the server does not list, by name', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: {} });
    open({ kind: 'personal', instanceId: 'inst-1' }, { tools: [] });

    fireEvent.change(await screen.findByPlaceholderText('Add a tool by name'), { target: { value: 'archive_issue' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(ruleRow('archive_issue').getByRole('radio', { name: 'Deny' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(screen.getByText('Not offered now')).toBeTruthy();
    await waitFor(() => expect(api.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: { archive_issue: 'block' } }));
  });

  it('keeps the dialog open when saving fails', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: {} });
    api.updateMyToolRules.mockRejectedValue(new Error('boom'));
    const onOpenChange = open({ kind: 'personal', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(toasts.error).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('says so when the rules cannot be loaded, and offers no save', async () => {
    api.getMyToolRules.mockRejectedValue(new Error('down'));
    open({ kind: 'personal', instanceId: 'inst-1' });

    expect((await screen.findByRole('alert')).textContent).toBe("Couldn't load the rules.");
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('McpToolRulesDialog — company rules', () => {
  it('sets Allow on approval, Deny and allowed-unattended, and saves only what says something', async () => {
    api.getToolPolicy.mockResolvedValue({ tools: {} });
    open({ kind: 'company', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    expect(chosen('search')).toBe('No limit');
    fireEvent.click(ruleRow('create_issue').getByRole('radio', { name: 'Allow on approval' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Allow create_issue when no one can approve' }));
    fireEvent.click(ruleRow('search').getByRole('radio', { name: 'Deny' }));
    expect(screen.queryByRole('switch', { name: 'Allow search when no one can approve' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.updateToolPolicy).toHaveBeenCalledWith('inst-1', {
        tools: { create_issue: { rule: 'ask', unattended: true }, search: { rule: 'block', unattended: false } },
      })
    );
  });

  it('quick setup asks for approval on every tool that changes or deletes data and has no company rule', async () => {
    api.getToolPolicy.mockResolvedValue({ tools: { delete_issue: { rule: 'block', unattended: false } } });
    open({ kind: 'company', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'search' });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(chosen('create_issue')).toBe('Allow on approval');
    expect(chosen('delete_issue')).toBe('Deny');
    expect(chosen('search')).toBe('No limit');
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });
});

describe('McpToolRulesDialog — an agent’s rules', () => {
  it('saves them for the agent', async () => {
    api.getAgentToolRules.mockResolvedValue({ tools: {} });
    open({ kind: 'agent', agentKey: 'agent-1', instanceId: 'inst-1' });

    await screen.findByRole('group', { name: 'create_issue' });
    fireEvent.click(ruleRow('create_issue').getByRole('radio', { name: 'Deny' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.updateAgentToolRules).toHaveBeenCalledWith('agent-1', 'inst-1', { tools: { create_issue: 'block' } })
    );
  });

  it('shows them without controls to someone who cannot edit the agent', async () => {
    api.getAgentToolRules.mockResolvedValue({ tools: { create_issue: 'block' } });
    open({ kind: 'agent', agentKey: 'agent-1', instanceId: 'inst-1' }, { readOnly: true });

    expect(await screen.findAllByText('Deny')).toHaveLength(2);
    expect(screen.getByText('Only people who can edit this agent can change these.')).toBeTruthy();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryByPlaceholderText('Add a tool by name')).toBeNull();
  });
});
