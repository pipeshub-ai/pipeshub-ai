// @vitest-environment node
// jsdom's FormData/File are not readable by Node's Response; Chromium's are.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { streamingFetch } from '../streaming-fetch';

type BridgeInit = { method?: string; headers?: Record<string, string>; body?: string | Uint8Array };
type BridgeCallbacks = {
  onHeaders: (h: { ok: boolean; status: number; statusText: string; headers: Record<string, string> }) => void;
  onEnd: () => void;
};

function installBridge() {
  const streamFetch = vi.fn((_url: string, _init: BridgeInit, callbacks: BridgeCallbacks) => {
    queueMicrotask(() => {
      callbacks.onHeaders({ ok: true, status: 200, statusText: 'OK', headers: {} });
      callbacks.onEnd();
    });
    return () => {};
  });
  (globalThis as unknown as { electronAPI?: unknown }).electronAPI = { isElectron: true, streamFetch };
  return streamFetch;
}

describe('streamingFetch (Electron bridge)', () => {
  afterEach(() => {
    delete (globalThis as unknown as { electronAPI?: unknown }).electronAPI;
  });

  it('sends FormData as multipart bytes with its boundary', async () => {
    const streamFetch = installBridge();
    const form = new FormData();
    form.append('files', new File(['hello pdf'], 'letter.pdf', { type: 'application/pdf' }));

    await streamingFetch('http://api/upload', {
      method: 'POST',
      headers: { Authorization: 'Bearer t' },
      body: form,
    });

    const init = streamFetch.mock.calls[0][1];
    expect(init.headers?.authorization).toBe('Bearer t');
    const contentType = init.headers?.['content-type'] ?? '';
    expect(contentType).toMatch(/^multipart\/form-data; boundary=/);
    expect(init.body).toBeInstanceOf(Uint8Array);

    const text = new TextDecoder().decode(init.body as Uint8Array);
    const boundary = contentType.split('boundary=')[1];
    expect(text).toContain(`--${boundary}`);
    expect(text).toContain('filename="letter.pdf"');
    expect(text).toContain('hello pdf');
  });

  it('replaces a caller multipart Content-Type that has no boundary', async () => {
    const streamFetch = installBridge();
    const form = new FormData();
    form.append('files', new File(['hello'], 'a.txt', { type: 'text/plain' }));

    await streamingFetch('http://api/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'multipart/form-data' },
      body: form,
    });

    const init = streamFetch.mock.calls[0][1];
    const contentType = init.headers?.['content-type'] ?? '';
    expect(contentType).toMatch(/^multipart\/form-data; boundary=/);
    const boundary = contentType.split('boundary=')[1];
    expect(new TextDecoder().decode(init.body as Uint8Array)).toContain(`--${boundary}`);
  });

  it('passes string bodies through unchanged', async () => {
    const streamFetch = installBridge();

    await streamingFetch('http://api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"q":"hi"}',
    });

    const init = streamFetch.mock.calls[0][1];
    expect(init.body).toBe('{"q":"hi"}');
    expect(init.headers?.['content-type']).toBe('application/json');
  });
});
