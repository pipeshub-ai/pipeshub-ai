/**
 * Unit tests for the agent capabilities localStorage helpers and the
 * "+" menu (attach files + capability switches).
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { DEFAULT_AGENT_CAPABILITIES } from '../types';
import type { AgentCapabilities } from '../types';

afterEach(() => cleanup());

// ---------------------------------------------------------------------------
// Minimal in-memory localStorage stub
// ---------------------------------------------------------------------------

function makeLocalStorage(): Storage & { _data: Record<string, string> } {
  const _data: Record<string, string> = {};
  return {
    _data,
    getItem(key: string) { return Object.prototype.hasOwnProperty.call(_data, key) ? _data[key] : null; },
    setItem(key: string, value: string) { _data[key] = String(value); },
    removeItem(key: string) { delete _data[key]; },
    clear() { Object.keys(_data).forEach((k) => delete _data[k]); },
    get length() { return Object.keys(_data).length; },
    key(index: number) { return Object.keys(_data)[index] ?? null; },
  };
}

// ---------------------------------------------------------------------------
// Re-implement the localStorage helpers inline (mirrors store.ts logic)
// ---------------------------------------------------------------------------

const LS_KEY = 'pipeshub-agent-capabilities';

function makeHelpers(storage: Storage) {
  function lsGet(agentId?: string): AgentCapabilities {
    try {
      const key = agentId ? `${LS_KEY}:${agentId}` : LS_KEY;
      const raw = storage.getItem(key);
      if (!raw) return { ...DEFAULT_AGENT_CAPABILITIES };
      const parsed = JSON.parse(raw) as Partial<AgentCapabilities>;
      return {
        internalSearch: typeof parsed.internalSearch === 'boolean' ? parsed.internalSearch : true,
        webSearch: typeof parsed.webSearch === 'boolean' ? parsed.webSearch : true,
      };
    } catch {
      return { ...DEFAULT_AGENT_CAPABILITIES };
    }
  }

  function lsSet(caps: AgentCapabilities, agentId?: string): void {
    const key = agentId ? `${LS_KEY}:${agentId}` : LS_KEY;
    storage.setItem(key, JSON.stringify(caps));
  }

  return { lsGet, lsSet };
}

// Use React.createElement so we don't need a separate JSX transform
const h = React.createElement;

// ---------------------------------------------------------------------------
// DEFAULT_AGENT_CAPABILITIES
// ---------------------------------------------------------------------------

describe('DEFAULT_AGENT_CAPABILITIES', () => {
  it('has both search modes enabled', () => {
    expect(DEFAULT_AGENT_CAPABILITIES.internalSearch).toBe(true);
    expect(DEFAULT_AGENT_CAPABILITIES.webSearch).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

describe('localStorage agent capabilities helpers', () => {
  let storage: ReturnType<typeof makeLocalStorage>;
  let lsGet: ReturnType<typeof makeHelpers>['lsGet'];
  let lsSet: ReturnType<typeof makeHelpers>['lsSet'];

  beforeEach(() => {
    storage = makeLocalStorage();
    ({ lsGet, lsSet } = makeHelpers(storage));
  });

  it('returns defaults when nothing is stored', () => {
    expect(lsGet()).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('returns defaults for an unknown agentId', () => {
    expect(lsGet('agent-xyz')).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('round-trips universal capabilities', () => {
    const caps: AgentCapabilities = { internalSearch: false, webSearch: true };
    lsSet(caps);
    expect(lsGet()).toEqual(caps);
  });

  it('round-trips scoped capabilities per agentId', () => {
    const caps: AgentCapabilities = { internalSearch: true, webSearch: false };
    lsSet(caps, 'agent-42');
    expect(lsGet('agent-42')).toEqual(caps);
  });

  it('scoped and universal keys are independent', () => {
    const universal: AgentCapabilities = { internalSearch: false, webSearch: true };
    const scoped: AgentCapabilities = { internalSearch: true, webSearch: false };
    lsSet(universal);
    lsSet(scoped, 'agent-abc');
    expect(lsGet()).toEqual(universal);
    expect(lsGet('agent-abc')).toEqual(scoped);
  });

  it('falls back to defaults when stored JSON is corrupt', () => {
    storage.setItem(LS_KEY, 'not valid json {{{');
    expect(lsGet()).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('uses boolean defaults for missing keys in stored object', () => {
    storage.setItem(LS_KEY, JSON.stringify({ internalSearch: false }));
    const caps = lsGet();
    expect(caps.internalSearch).toBe(false);
    expect(caps.webSearch).toBe(true);
  });

  it('ignores non-boolean values and returns defaults for those fields', () => {
    storage.setItem(LS_KEY, JSON.stringify({ internalSearch: 'yes', webSearch: 0 }));
    const caps = lsGet();
    expect(caps.internalSearch).toBe(true);
    expect(caps.webSearch).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The "+" menu (attach files + capability switches), open
// ---------------------------------------------------------------------------

describe('PlusMenuButton menu', () => {
  // The open menu lays out in a Radix ScrollArea, which observes its size.
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => vi.unstubAllGlobals());

  async function importButton() {
    const mod = await import('../components/chat-panel/plus-menu-button');
    return mod.PlusMenuButton;
  }

  type ButtonProps = Parameters<Awaited<ReturnType<typeof importButton>>>[0];

  const base = {
    activeIconColor: 'var(--emerald-a11)',
    open: true,
    onOpenChange: vi.fn(),
    onAttachFiles: vi.fn(),
    internalSearch: true,
    webSearch: true,
    onToggleInternalSearch: vi.fn(),
    onToggleWebSearch: vi.fn(),
  };

  async function renderMenu(props: Partial<ButtonProps> = {}) {
    const Button = await importButton();
    render(h(Theme, null, h(Button, { ...base, ...props } as ButtonProps)));
  }

  it('renders Attach files, Internal search and Web search as menu items named by their words', async () => {
    await renderMenu();
    expect(screen.getByRole('menu')).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Attach files' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Internal search' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Web search' })).toBeTruthy();
  });

  it('calls onAttachFiles when the attach files item is chosen', async () => {
    const onAttachFiles = vi.fn();
    await renderMenu({ onAttachFiles });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach files' }));
    expect(onAttachFiles).toHaveBeenCalled();
  });

  it('disables the internal search item and switch when agentHasInternalSearch=false', async () => {
    await renderMenu({ internalSearch: false, agentHasInternalSearch: false });
    const internalSwitch = screen.getByRole('switch', { name: 'Internal search' }) as HTMLButtonElement;
    expect(internalSwitch.disabled).toBe(true);
    expect(screen.getByRole('menuitem', { name: 'Internal search' }).getAttribute('aria-disabled')).toBe('true');
  });

  it('a disabled capability row still says why: its tooltip opens on focus or hover', async () => {
    await renderMenu({ internalSearch: false, agentHasInternalSearch: false });
    const row = screen.getByRole('menuitem', { name: 'Internal search' });
    expect(row.getAttribute('aria-disabled')).toBe('true');
    expect((screen.getByRole('switch', { name: 'Internal search' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.focus(row);
    const tip = await screen.findByRole('tooltip');
    expect(tip.textContent).toContain('Not configured on this agent');

    fireEvent.blur(row);
    fireEvent.pointerMove(row, { pointerType: 'mouse' });
    expect((await screen.findByRole('tooltip')).textContent).toContain('Not configured on this agent');
  });

  it('disables the web search switch when agentHasWebSearch=false', async () => {
    await renderMenu({ webSearch: false, agentHasWebSearch: false });
    const webSwitch = screen.getByRole('switch', { name: 'Web search' }) as HTMLButtonElement;
    expect(webSwitch.disabled).toBe(true);
  });

  it('flips internal search once when its switch is clicked', async () => {
    const toggle = vi.fn();
    await renderMenu({ onToggleInternalSearch: toggle });
    fireEvent.click(screen.getByRole('switch', { name: 'Internal search' }));
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(toggle).toHaveBeenCalledWith(false);
  });

  it('flips a capability from its row and keeps the menu open', async () => {
    const toggle = vi.fn();
    const onOpenChange = vi.fn();
    await renderMenu({ onToggleWebSearch: toggle, onOpenChange });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Web search' }));
    expect(toggle).toHaveBeenCalledWith(false);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('does NOT call onToggleInternalSearch when the capability is absent', async () => {
    const toggle = vi.fn();
    await renderMenu({ internalSearch: false, onToggleInternalSearch: toggle, agentHasInternalSearch: false });
    fireEvent.click(screen.getByRole('switch', { name: 'Internal search' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Internal search' }));
    expect(toggle).not.toHaveBeenCalled();
  });

  it('flips web search once when its switch is clicked', async () => {
    const toggle = vi.fn();
    await renderMenu({ onToggleWebSearch: toggle });
    fireEvent.click(screen.getByRole('switch', { name: 'Web search' }));
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it('marks the menu as a composer popover', async () => {
    await renderMenu();
    expect(screen.getByRole('menu').closest('[data-composer-popover]')).toBeTruthy();
  });
});

describe('hasNonDefaultSearchCapabilities', () => {
  it('is false only when both capabilities are at their default (true)', async () => {
    const { hasNonDefaultSearchCapabilities } = await import(
      '../components/chat-panel/plus-menu-button'
    );
    expect(hasNonDefaultSearchCapabilities(true, true)).toBe(false);
    expect(hasNonDefaultSearchCapabilities(false, true)).toBe(true);
    expect(hasNonDefaultSearchCapabilities(true, false)).toBe(true);
    expect(hasNonDefaultSearchCapabilities(false, false)).toBe(true);
  });
});

describe('PlusMenuButton filter badge', () => {
  async function importButton() {
    const mod = await import('../components/chat-panel/plus-menu-button');
    return mod.PlusMenuButton;
  }

  type ButtonProps = Parameters<Awaited<ReturnType<typeof importButton>>>[0];

  function renderButton(props: ButtonProps, Button: Awaited<ReturnType<typeof importButton>>) {
    return render(h(Theme, null, h(Button, props)));
  }

  const baseProps = {
    onAttachFiles: vi.fn(),
    onToggleInternalSearch: vi.fn(),
    onToggleWebSearch: vi.fn(),
    activeIconColor: 'var(--emerald-a11)',
    open: false,
    onOpenChange: vi.fn(),
  };

  it('hides the badge when both capabilities are on', async () => {
    const Button = await importButton();
    renderButton({ ...baseProps, internalSearch: true, webSearch: true }, Button);
    expect(screen.queryByTestId('plus-menu-filter-badge')).toBeNull();
  });

  it('shows the badge for each non-default capability combination', async () => {
    const Button = await importButton();
    const nonDefaults: Array<Pick<ButtonProps, 'internalSearch' | 'webSearch'>> = [
      { internalSearch: false, webSearch: true },
      { internalSearch: true, webSearch: false },
      { internalSearch: false, webSearch: false },
    ];
    for (const caps of nonDefaults) {
      const { unmount } = renderButton({ ...baseProps, ...caps }, Button);
      expect(screen.getByTestId('plus-menu-filter-badge')).toBeTruthy();
      unmount();
    }
  });
});
