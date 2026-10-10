import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api', () => ({ apiClient: { post: vi.fn() }, isProcessedError: () => false }));
vi.mock('@/config', () => ({ isMcpInstanceReadOnly: () => false, McpInheritedCallout: () => null }));
vi.mock('../../../api', () => ({
  McpServersApi: { discoverOAuthMetadata: vi.fn(), getOAuthConfig: vi.fn() },
}));
vi.mock('../../../components', () => ({ McpDisabledCallout: () => null }));
vi.mock('../../../../components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({
    open,
    children,
    primaryDisabled,
  }: {
    open: boolean;
    children: React.ReactNode;
    primaryDisabled?: boolean;
  }) =>
    open ? (
      <div>
        {children}
        <button type="button" disabled={primaryDisabled}>
          save
        </button>
      </div>
    ) : null,
}));
// Native controls so the test can read the selected transport and the offered options.
vi.mock('../../../../components', () => ({
  FormField: ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div role="group" aria-label={label}>
      {children}
    </div>
  ),
  SelectDropdown: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
  TagInput: () => null,
}));

import { McpInstanceConfigPanel } from '../mcp-instance-config-panel';

const createCustom = { open: true, mode: 'create' as const, editingInstance: null, prefillTemplate: null };

function panel(customStdioAllowed: boolean) {
  return (
    <Theme>
      <McpInstanceConfigPanel
        state={createCustom}
        templates={[]}
        instances={[]}
        customStdioAllowed={customStdioAllowed}
        onOpenChange={vi.fn()}
        onSaved={vi.fn()}
        onRequestDelete={vi.fn()}
        busyInstanceId={null}
        onAuthenticate={vi.fn()}
        onReauthenticate={vi.fn()}
        onDisconnect={vi.fn()}
      />
    </Theme>
  );
}

const field = (label: string) => within(screen.getByRole('group', { name: label }));
const transportSelect = () => field('workspace.mcpServers.form.transport').getByRole('combobox') as HTMLSelectElement;
const offeredTransports = () => Array.from(transportSelect().options).map((o) => o.value);
const textInput = (label: string) => field(label).getByRole('textbox') as HTMLInputElement;

afterEach(cleanup);

describe('McpInstanceConfigPanel custom STDIO setting', () => {
  it('keeps what the admin typed when the setting arrives after the drawer opened', () => {
    const { rerender } = render(panel(false));
    expect(transportSelect().value).toBe('streamable_http');
    expect(offeredTransports()).toEqual(['streamable_http']);

    fireEvent.change(textInput('workspace.mcpServers.form.name'), { target: { value: 'Internal tools' } });
    fireEvent.change(textInput('workspace.mcpServers.form.url'), { target: { value: 'https://mcp.example.com' } });

    rerender(panel(true));

    expect(textInput('workspace.mcpServers.form.name').value).toBe('Internal tools');
    expect(textInput('workspace.mcpServers.form.url').value).toBe('https://mcp.example.com');
    expect(transportSelect().value).toBe('streamable_http');
    expect(offeredTransports()).toEqual(['stdio', 'streamable_http']);
    expect(screen.queryByText('workspace.mcpServers.stdioPolicy.unavailableHint')).toBeNull();
  });

  it('starts a new custom server on STDIO, with the warning, when the setting is on', () => {
    render(panel(true));
    expect(transportSelect().value).toBe('stdio');
    expect(screen.getByText('workspace.mcpServers.stdioPolicy.enabledWarning')).toBeTruthy();
  });

  it('keeps a STDIO form readable but unsavable if the setting turns off while it is open', () => {
    const { rerender } = render(panel(true));
    fireEvent.change(textInput('workspace.mcpServers.form.name'), { target: { value: 'Local files' } });

    rerender(panel(false));

    expect(textInput('workspace.mcpServers.form.name').value).toBe('Local files');
    expect(transportSelect().value).toBe('stdio');
    expect(offeredTransports()).toContain('stdio');
    expect(screen.getByText('workspace.mcpServers.stdioPolicy.unavailableHint')).toBeTruthy();
    expect((screen.getByText('save') as HTMLButtonElement).disabled).toBe(true);
  });
});
