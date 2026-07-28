/**
 * Unit tests for the agent capabilities localStorage helpers and
 * AgentCapabilitiesBar component.
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
        deepSearch: typeof parsed.deepSearch === 'boolean' ? parsed.deepSearch : false,
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
  it('has both search modes enabled and deepSearch off', () => {
    expect(DEFAULT_AGENT_CAPABILITIES.internalSearch).toBe(true);
    expect(DEFAULT_AGENT_CAPABILITIES.webSearch).toBe(true);
    expect(DEFAULT_AGENT_CAPABILITIES.deepSearch).toBe(false);
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
    const caps: AgentCapabilities = { internalSearch: false, webSearch: true, deepSearch: false };
    lsSet(caps);
    expect(lsGet()).toEqual(caps);
  });

  it('round-trips scoped capabilities per agentId', () => {
    const caps: AgentCapabilities = { internalSearch: true, webSearch: false, deepSearch: false };
    lsSet(caps, 'agent-42');
    expect(lsGet('agent-42')).toEqual(caps);
  });

  it('scoped and universal keys are independent', () => {
    const universal: AgentCapabilities = { internalSearch: false, webSearch: true, deepSearch: false };
    const scoped: AgentCapabilities = { internalSearch: true, webSearch: false, deepSearch: false };
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
    expect(caps.deepSearch).toBe(false);
  });

  it('ignores non-boolean values and returns defaults for those fields', () => {
    storage.setItem(LS_KEY, JSON.stringify({ internalSearch: 'yes', webSearch: 0 }));
    const caps = lsGet();
    expect(caps.internalSearch).toBe(true);
    expect(caps.webSearch).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AgentCapabilitiesBar component
// ---------------------------------------------------------------------------

describe('AgentCapabilitiesBar', () => {
  async function importBar() {
    const mod = await import(
      '../components/chat-panel/expansion-panels/agent-capabilities-bar'
    );
    return mod.AgentCapabilitiesBar;
  }

  type BarProps = Parameters<Awaited<ReturnType<typeof importBar>>>[0];

  function renderBar(props: BarProps, Bar: Awaited<ReturnType<typeof importBar>>) {
    return render(h(Theme, null, h(Bar, props)));
  }

  it('renders Capabilities label, Internal Search and Web Search text', async () => {
    const Bar = await importBar();
    renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
      },
      Bar,
    );
    expect(screen.getByText('Capabilities')).toBeTruthy();
    expect(screen.getByText('Internal Search')).toBeTruthy();
    expect(screen.getByText('Web Search')).toBeTruthy();
  });

  it('disables the internal search switch when agentHasInternalSearch=false', async () => {
    const Bar = await importBar();
    const { container } = renderBar(
      {
        internalSearch: false,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
        agentHasInternalSearch: false,
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    expect(internalSwitch.disabled).toBe(true);
  });

  it('disables the web search switch when agentHasWebSearch=false', async () => {
    const Bar = await importBar();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: false,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
        agentHasWebSearch: false,
      },
      Bar,
    );
    const switches = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    expect(switches[1].disabled).toBe(true);
  });

  it('calls onToggleInternalSearch when the enabled switch is clicked', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: toggle,
        onToggleWebSearch: vi.fn(),
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(internalSwitch);
    expect(toggle).toHaveBeenCalled();
  });

  it('does NOT call onToggleInternalSearch when the capability is absent', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: false,
        webSearch: true,
        onToggleInternalSearch: toggle,
        onToggleWebSearch: vi.fn(),
        agentHasInternalSearch: false,
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(internalSwitch);
    expect(toggle).not.toHaveBeenCalled();
  });

  it('calls onToggleWebSearch when the enabled switch is clicked', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: toggle,
      },
      Bar,
    );
    const switches = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(switches[1]);
    expect(toggle).toHaveBeenCalled();
  });
});
