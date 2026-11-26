/**
 * R25UI-FU-1: React Flow writes its colour mode on its root as a class (`light` by default), and Radix Themes
 * redeclares every colour scale on `.light`. The canvas must carry the app's own appearance, or every card on it reads
 * light tokens (light panels, light labels) in dark mode.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { AgentBuilderCanvas } from '../agent-builder-canvas';

const theme = vi.hoisted(() => ({ appearance: 'dark' as 'light' | 'dark' }));
vi.mock('@/app/components/theme-provider', () => ({
  useThemeAppearance: () => ({ appearance: theme.appearance, preference: theme.appearance, setPreference: () => {} }),
}));

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    class NoopResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = NoopResizeObserver;
  }
});

afterEach(() => cleanup());

function renderCanvas() {
  const noop = () => {};
  return render(
    <AgentBuilderCanvas
      sidebarOpen={false}
      sidebarWidth={0}
      nodes={[]}
      edges={[]}
      onNodesChange={noop}
      onEdgesChange={noop}
      onConnect={noop}
      onEdgeClick={noop}
      setNodes={noop}
      setEdges={noop}
      nodeTemplates={[]}
      configuredConnectors={[]}
      activeAgentConnectors={[]}
      onNodeDelete={noop}
    />,
  );
}

describe('agent builder canvas colour mode', () => {
  it.each(['dark', 'light'] as const)('carries the app appearance (%s) and never the other one', (appearance) => {
    theme.appearance = appearance;
    const other = appearance === 'dark' ? 'light' : 'dark';
    const { container } = renderCanvas();
    const root = container.querySelector('.react-flow');
    expect(root).not.toBeNull();
    expect(root?.classList.contains(appearance)).toBe(true);
    expect(root?.classList.contains(other)).toBe(false);
  });
});
