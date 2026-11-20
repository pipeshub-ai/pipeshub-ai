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
  MERMAID_CONFIG,
  MermaidDiagram,
  fitZoomPercent,
  inlineDiagramWidth,
  makeSvgResponsive,
  parseSvgNaturalSize,
  pngExportScale,
  sanitizeRenderedSvg,
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

  it('lets labels keep formatting tags only, and lets no diagram change that or the CSS', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    render(React.createElement(Theme, null, React.createElement(MermaidDiagram, { chart: 'graph TD; A-->B' })));
    await waitFor(() => expect(mermaidMock.initialize).toHaveBeenCalled(), { timeout: 3000 });
    const config = mermaidMock.initialize.mock.calls[0][0] as unknown as typeof MERMAID_CONFIG;
    expect(config).toEqual(MERMAID_CONFIG);
    expect(config.dompurifyConfig?.ALLOWED_TAGS).not.toContain('img');
    expect(config.dompurifyConfig?.ALLOWED_ATTR).toEqual(['class']);
    expect(config.secure).toEqual(
      expect.arrayContaining(['securityLevel', 'dompurifyConfig', 'themeCSS', 'fontFamily', 'altFontFamily']),
    );
  });

  it('draws the source after making it safe, and shows the drawing after cleaning it', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));
    mermaidMock.render.mockClear();
    mermaidMock.render.mockImplementationOnce(async () => ({
      svg: '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="https://attacker.example/i.png"></image><g><text>ok</text></g></svg>',
    }));
    const { container } = render(
      React.createElement(Theme, null, React.createElement(MermaidDiagram, { chart: 'flowchart TD\n  A@{ img: "https://attacker.example/i.png" }' })),
    );
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull(), { timeout: 3000 });
    expect(mermaidMock.render.mock.calls[0][1]).not.toContain('attacker.example');
    expect(container.innerHTML).not.toContain('attacker.example');
    expect(container.textContent).toContain('ok');
  });
});

describe('sanitizeRenderedSvg', () => {
  const svg = (inner: string) =>
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">${inner}</svg>`;
  const OUT = 'attacker.example';

  it.each([
    ['an <image> with an outside href', `<image href="https://${OUT}/a.png"></image>`],
    ['an <image> with an outside xlink:href', `<image xlink:href="https://${OUT}/a.png"></image>`],
    ['a <use> pointing outside', `<use xlink:href="https://${OUT}/s.svg#x"></use>`],
    ['an <img> in a label', `<foreignObject><div xmlns="http://www.w3.org/1999/xhtml"><img src="https://${OUT}/l.png"></div></foreignObject>`],
    ['a <picture> in a label', `<foreignObject><div xmlns="http://www.w3.org/1999/xhtml"><picture><source srcset="https://${OUT}/s.png"></picture></div></foreignObject>`],
    ['an inline style url()', `<g style="background-image: url(https://${OUT}/s.png)"></g>`],
    ['a fill url() to another document', `<rect fill="url(https://${OUT}/p.svg#g)"></rect>`],
    ['an outside link', `<a xlink:href="https://${OUT}/l"><text>x</text></a>`],
    ['a stylesheet url()', `<style>.n { background-image: url("https://${OUT}/c.png"); }</style>`],
    ['a stylesheet @import', `<style>@import url(https://${OUT}/i.css); .n { fill: red; }</style>`],
  ])('removes %s', (_label, inner) => {
    expect(sanitizeRenderedSvg(svg(`${inner}<text>kept</text>`))).not.toContain(OUT);
  });

  it('keeps references into the drawing and its own styles', () => {
    const out = sanitizeRenderedSvg(
      svg(
        '<style>#m .node rect { fill: #ECECFF; }</style>' +
          '<defs><marker id="m-end"><path d="M0 0"></path></marker><linearGradient id="g"></linearGradient></defs>' +
          '<path marker-end="url(#m-end)" d="M0 0 L1 1"></path><rect fill="url(#g)"></rect>' +
          '<a xlink:href="#m-end"><text>in page</text></a>',
      ),
    );
    expect(out).toContain('fill: #ECECFF');
    expect(out).toContain('marker-end="url(#m-end)"');
    expect(out).toContain('fill="url(#g)"');
    expect(out).toContain('href="#m-end"');
    expect(out).toContain('in page');
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
