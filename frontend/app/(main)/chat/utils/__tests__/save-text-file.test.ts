import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { saveTextFile } from '../save-text-file';

const createObjectURL = vi.fn((blob: Blob) => {
  void blob;
  return 'blob:saved-file';
});
const revokeObjectURL = vi.fn();
let click: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe('saveTextFile', () => {
  it('downloads the text under the given name', async () => {
    vi.useRealTimers();
    saveTextFile('notes.md', '# Hello\n', 'text/markdown');

    const clicked = click.mock.contexts[0] as HTMLAnchorElement;
    expect(clicked.download).toBe('notes.md');
    expect(clicked.href).toBe('blob:saved-file');
    const blob = createObjectURL.mock.calls[0][0];
    expect(blob.type).toBe('text/markdown;charset=utf-8');
    expect(await readBlob(blob)).toBe('# Hello\n');
  });

  it('removes the link it added and frees the file address afterwards', () => {
    saveTextFile('a.txt', 'x', 'text/plain');

    expect(document.body.querySelector('a[download]')).toBeNull();
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:saved-file');
  });
});
