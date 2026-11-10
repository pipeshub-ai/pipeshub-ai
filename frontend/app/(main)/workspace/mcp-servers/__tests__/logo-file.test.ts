import { describe, expect, it } from 'vitest';
import { fitWithin, imageFileToLogo, MCP_LOGO_MAX_FILE_BYTES, McpLogoFileError } from '../logo-file';

describe('fitWithin', () => {
  it('shrinks a large image to fit, keeping its shape', () => {
    expect(fitWithin(400, 200, 96)).toEqual({ width: 96, height: 48 });
    expect(fitWithin(50, 1000, 96)).toEqual({ width: 5, height: 96 });
  });

  it('never enlarges a small image', () => {
    expect(fitWithin(20, 10, 96)).toEqual({ width: 20, height: 10 });
  });

  it('keeps at least one pixel on the short side', () => {
    expect(fitWithin(10_000, 1, 96)).toEqual({ width: 96, height: 1 });
  });

  it('draws an SVG with no size of its own as a square', () => {
    expect(fitWithin(0, 0, 96)).toEqual({ width: 96, height: 96 });
  });
});

describe('imageFileToLogo', () => {
  async function problem(file: File): Promise<string | null> {
    try {
      await imageFileToLogo(file);
      return null;
    } catch (e) {
      return e instanceof McpLogoFileError ? e.problem : 'other';
    }
  }

  it('refuses a file that is not an image it can draw', async () => {
    expect(await problem(new File(['x'], 'logo.bmp', { type: 'image/bmp' }))).toBe('type');
    expect(await problem(new File(['%PDF'], 'logo.pdf', { type: 'application/pdf' }))).toBe('type');
  });

  it('refuses a file over the size limit before reading it', async () => {
    const big = new File([new Uint8Array(MCP_LOGO_MAX_FILE_BYTES + 1)], 'logo.png', { type: 'image/png' });

    expect(await problem(big)).toBe('size');
  });
});
