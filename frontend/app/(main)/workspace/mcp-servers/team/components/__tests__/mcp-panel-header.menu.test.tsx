import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null, resolveConnectorType: () => 'mcp' }));

import { McpServerActionsMenu, type McpMenuEntry } from '../mcp-panel-header';

// Radix measures the menu; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

/** Like `useWorkspaceDrawerNestedModalHost`: a full-screen layer that lets the pointer through. */
function drawerHost(): HTMLElement {
  const host = document.createElement('div');
  host.style.pointerEvents = 'none';
  document.body.appendChild(host);
  return host;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('McpServerActionsMenu', () => {
  it('can be clicked inside the drawer host, which ignores the pointer', () => {
    const host = drawerHost();
    const onCopy = vi.fn();
    const entries: McpMenuEntry[] = [
      { kind: 'item', id: 'copy', icon: 'content_copy', label: 'Copy server URL', onSelect: onCopy },
      { kind: 'info', id: 'by', icon: 'person', label: 'Added by someone' },
    ];
    render(
      <Theme>
        <McpServerActionsMenu entries={entries} container={host} />
      </Theme>,
    );

    fireEvent.keyDown(screen.getByRole('button', { name: 'workspace.mcpServers.configPanel.menu.label' }), { key: 'Enter' });

    const menu = screen.getByRole('menu');
    expect(host.contains(menu)).toBe(true);
    expect(menu.style.pointerEvents).toBe('auto');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Copy server URL' }));
    expect(onCopy).toHaveBeenCalledTimes(1);
  });
});
