import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpRuleTool } from '../tool-rules';

const api = vi.hoisted(() => ({
  getMyToolRules: vi.fn(),
  updateMyToolRules: vi.fn(),
}));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../api', () => ({ McpServersApi: api }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));

import { McpToolRulesList, useToolRulesEditor } from '../components/mcp-tool-rules-editor';

const TOOLS: McpRuleTool[] = [
  { name: 'search', title: 'Search issues', kind: 'read', kindSource: 'server' },
  { name: 'create_issue', kind: 'write', kindSource: 'none' },
  { name: 'delete_issue', kind: 'destructive', kindSource: 'name' },
];

/** The rules as the server page shows them: the list, and a save that reports whether it worked. */
function RulesHarness({ tools, onSaved }: { tools: McpRuleTool[]; onSaved: () => void }) {
  const editor = useToolRulesEditor('inst-1', tools);
  return (
    <>
      <McpToolRulesList editor={editor} />
      <button
        type="button"
        disabled={editor.loading || Boolean(editor.loadError) || editor.changes === 0}
        onClick={() => void editor.save().then((ok) => ok && onSaved())}
      >
        Save
      </button>
    </>
  );
}

function open(tools: McpRuleTool[] = TOOLS) {
  const onSaved = vi.fn();
  renderInTheme(<RulesHarness tools={tools} onSaved={onSaved} />);
  return onSaved;
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
  api.getMyToolRules.mockResolvedValue({ tools: {} });
  api.updateMyToolRules.mockResolvedValue({ tools: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the tool rules editor', () => {
  it('shows each tool where it starts, or the rule saved for it', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: { search: 'block' } });
    open();

    await screen.findByRole('radiogroup', { name: 'search' });
    expect(api.getMyToolRules).toHaveBeenCalledWith('inst-1');
    expect(chosen('search')).toBe('Deny');
    expect(chosen('create_issue')).toBe('Allow on approval');
    expect(chosen('delete_issue')).toBe('Deny');
  });

  it('groups the tools by what they do to data, deleting ones first', async () => {
    open();

    await screen.findByRole('radiogroup', { name: 'search' });
    const groups = screen.getAllByTestId(/^tool-group-/).map((group) => group.dataset.testid);
    expect(groups).toEqual(['tool-group-destructive', 'tool-group-write', 'tool-group-read']);
    expect(within(screen.getByTestId('tool-group-destructive')).getByText('Review these first')).toBeTruthy();
  });

  it('saves only what differs from the starting rules', async () => {
    const onSaved = open();

    await screen.findByRole('radiogroup', { name: 'create_issue' });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(ruleRow('create_issue').getByRole('radio', { name: 'Pre-approved' }));
    fireEvent.click(ruleRow('delete_issue').getByRole('radio', { name: 'Allow on approval' }));
    fireEvent.click(ruleRow('search').getByRole('radio', { name: 'Pre-approved' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.updateMyToolRules).toHaveBeenCalledWith('inst-1', { tools: { create_issue: 'allow', delete_issue: 'ask' } });
    expect(toasts.success).toHaveBeenCalledWith('Tool approvals saved');
  });

  it('reports a failed save and keeps the changes', async () => {
    api.updateMyToolRules.mockRejectedValue(new Error('boom'));
    const onSaved = open();

    fireEvent.click(ruleRow((await screen.findByRole('radiogroup', { name: 'search' })).getAttribute('aria-label')!).getByRole('radio', { name: 'Deny' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(toasts.error).toHaveBeenCalled());
    expect(onSaved).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('says so when the rules cannot be loaded, and offers no save', async () => {
    api.getMyToolRules.mockRejectedValue(new Error('down'));
    open();

    expect((await screen.findByRole('alert')).textContent).toBe("Couldn't load the rules.");
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers search only for a long list, and clears it', async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ name: `tool_${i}`, kind: 'write' as const }));
    open(many);

    fireEvent.change(await screen.findByPlaceholderText('Search by name or ID'), { target: { value: 'zzz' } });
    expect(screen.getByText('No tools match your search.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(screen.getByRole('radiogroup', { name: 'tool_11' })).toBeTruthy();
  });

  it('has no search for a short list', async () => {
    open();
    await screen.findByRole('radiogroup', { name: 'search' });
    expect(screen.queryByPlaceholderText('Search by name or ID')).toBeNull();
  });

  it('keeps a rule for a tool the server no longer lists', async () => {
    api.getMyToolRules.mockResolvedValue({ tools: { archive_issue: 'block' } });
    open([]);

    expect(await screen.findByRole('radiogroup', { name: 'archive_issue' })).toBeTruthy();
    expect(screen.getByTestId('tool-group-notOffered')).toBeTruthy();
  });
});
