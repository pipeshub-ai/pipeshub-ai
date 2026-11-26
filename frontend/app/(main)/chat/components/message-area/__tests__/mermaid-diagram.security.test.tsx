import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async () => ({ svg: '<svg xmlns="http://www.w3.org/2000/svg"><g></g></svg>' })),
}));
vi.mock('mermaid', () => ({ default: mermaidMock }));

import {
  MermaidDiagram,
  fitZoomPercent,
  inlineDiagramWidth,
  makeSvgResponsive,
  parseSvgNaturalSize,
  pngExportScale,
  zoomOutPercent,
} from '../mermaid-diagram';

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

describe('MermaidDiagram sizing', () => {
  const tall = '<svg xmlns="http://www.w3.org/2000/svg" width="100%" style="max-width: 190px;" viewBox="0 0 190 694"><g></g></svg>';

  it('reads the natural size from the viewBox', () => {
    expect(parseSvgNaturalSize(tall)).toEqual({ width: 190, height: 694 });
  });

  it('renders inline at natural width, shrunk so a tall diagram stays within the height cap', () => {
    expect(inlineDiagramWidth(300, 200)).toBe(300);
    expect(inlineDiagramWidth(190, 694)).toBe(Math.floor(190 * (480 / 694)));
    const inline = makeSvgResponsive(tall, false);
    expect(inline).toContain(`width: ${Math.floor(190 * (480 / 694))}px`);
    expect(inline).toContain('max-width: 100%');
  });

  it('fits a tall diagram to the full view height and caps a wide one at full width', () => {
    // 1000x600 area: a 190x694 diagram is height-bound.
    expect(fitZoomPercent(190, 694, 1000, 600)).toBe(Math.floor(((600 * 190) / (694 * 1000)) * 100));
    expect(fitZoomPercent(1200, 300, 1000, 600)).toBe(100);
  });

  it('fits a very tall diagram below the zoom-out floor, down to 1%', () => {
    expect(fitZoomPercent(190, 10_000, 1000, 600)).toBe(1);
    expect(fitZoomPercent(10, 1_000_000, 1000, 600)).toBe(1);
  });

  it('zooms out to the 5% floor without jumping up from a smaller fit', () => {
    expect(zoomOutPercent(100)).toBe(75);
    expect(zoomOutPercent(20)).toBe(5);
    expect(zoomOutPercent(5)).toBe(5);
    expect(zoomOutPercent(2)).toBe(2);
  });

  it('exports PNGs at 2x at least and enlarges small diagrams', () => {
    expect(pngExportScale(1600, 1200, 1)).toBe(2);
    expect(pngExportScale(1600, 1200, 3)).toBe(3);
    expect(pngExportScale(190, 694, 1)).toBeCloseTo(2000 / 694);
    expect(pngExportScale(5000, 5000, 1)).toBeLessThanOrEqual(8000 / 5000);
  });

  it('scales a diagram larger than the PNG cap below 1x', () => {
    expect(pngExportScale(20_000, 20_000, 1)).toBeCloseTo(0.4);
    expect(pngExportScale(400, 16_000, 2)).toBeCloseTo(0.5);
    expect(Math.max(20_000, 20_000) * pngExportScale(20_000, 20_000, 3)).toBeLessThanOrEqual(8000);
  });
});

describe('MermaidDiagram copy source', () => {
  it('offers Copy source on a rendered diagram and confirms with a toast', async () => {
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { useToastStore } = await import('@/lib/store/toast-store');
    const { getAllByLabelText } = render(
      React.createElement(Theme, null, React.createElement(MermaidDiagram, { chart: 'graph TD; A-->B' })),
    );
    const btn = await waitFor(() => getAllByLabelText('chat.copyDiagramSource')[0], { timeout: 3000 });
    btn.click();
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('graph TD; A-->B'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t: { title?: string }) => /diagramSourceCopied|Diagram source copied/.test(t.title ?? ''))).toBe(true),
    );
  });
});
