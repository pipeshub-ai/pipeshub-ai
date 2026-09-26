import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async () => ({ svg: '<svg xmlns="http://www.w3.org/2000/svg"><g></g></svg>' })),
}));
vi.mock('mermaid', () => ({ default: mermaidMock }));

import { MermaidDiagram } from '../mermaid-diagram';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('MermaidDiagram security', () => {
  // T31
  it("initialises mermaid with securityLevel 'strict'", async () => {
    // jsdom has no matchMedia; the component reads it for dark mode.
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    render(React.createElement(Theme, null, React.createElement(MermaidDiagram, { chart: 'graph TD; A-->B' })));
    await waitFor(() => expect(mermaidMock.initialize).toHaveBeenCalled(), { timeout: 3000 });
    expect(mermaidMock.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ securityLevel: 'strict' }),
    );
  });
});
