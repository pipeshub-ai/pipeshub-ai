import { describe, it, expect, beforeAll } from 'vitest';
import { MERMAID_CONFIG } from '../mermaid-diagram';

const SOURCE = [
  'flowchart TD',
  '  A["<img src=x onerror=alert(1)>"] --> B[Next]',
  '  click A "javascript:alert(1)"',
].join('\n');

// jsdom has no SVG layout; mermaid only needs plausible sizes to finish rendering.
beforeAll(() => {
  const proto = window.SVGElement.prototype as unknown as Record<string, unknown>;
  proto.getBBox = () => ({ x: 0, y: 0, width: 80, height: 20 });
  proto.getComputedTextLength = () => 80;
  // Mermaid waits for label images to settle; jsdom never loads them.
  Object.defineProperty(window.HTMLImageElement.prototype, 'complete', { configurable: true, get: () => true });
});

describe('mermaid security', () => {
  it('pins the strict security level', () => {
    expect(MERMAID_CONFIG.securityLevel).toBe('strict');
  });

  it('renders hostile labels and click targets without handlers or javascript: URLs', async () => {
    const { default: mermaid } = await import('mermaid');
    mermaid.initialize({ ...MERMAID_CONFIG });
    const { svg } = await mermaid.render('sec-test', SOURCE);
    expect(svg).toContain('<svg');
    expect(svg).not.toMatch(/\son[a-z]+\s*=/i);
    expect(svg.toLowerCase()).not.toContain('javascript:');
  }, 30000);
});
