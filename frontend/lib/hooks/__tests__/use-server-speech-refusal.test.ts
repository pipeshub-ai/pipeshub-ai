/** A speech call refused with 402 (the server refuses every run) passes the server's words on; any other failure passes none. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const post = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiClient: { post } }));

import { useServerSpeechSynthesis } from '../use-server-speech-synthesis';
import { useServerSpeechRecognition } from '../use-server-speech-recognition';

// As the API client's interceptor rejects them: a plain object with a status and the server's words.
const REFUSAL = { type: 'UNKNOWN', statusCode: 402, message: 'This request was refused.' };
const OUTAGE = { type: 'SERVER', statusCode: 500, message: 'The speech service failed.' };

beforeEach(() => {
  post.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

describe('reading aloud', () => {
  const read = async (failure: unknown) => {
    post.mockRejectedValue(failure);
    const onError = vi.fn();
    const { result } = renderHook(() => useServerSpeechSynthesis({ onError }));
    act(() => result.current.speak('Hello there.'));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    return onError.mock.calls[0];
  };

  it("hands on the server's reason for a 402", async () => {
    expect((await read(REFUSAL))[1]).toBe('This request was refused.');
  });

  it('hands on none for any other failure', async () => {
    expect((await read(OUTAGE))[1]).toBeUndefined();
  });
});

describe('dictation', () => {
  class FakeRecorder {
    static last: FakeRecorder;
    state = 'inactive';
    mimeType = 'audio/webm';
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor() {
      FakeRecorder.last = this;
    }
    static isTypeSupported = () => true;
    start() {
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob([new Uint8Array(4096)], { type: 'audio/webm' }) });
      this.onstop?.();
    }
  }

  const dictate = async (failure: unknown) => {
    post.mockRejectedValue(failure);
    vi.stubGlobal('MediaRecorder', FakeRecorder);
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [] }) },
    });
    const onError = vi.fn();
    const { result } = renderHook(() => useServerSpeechRecognition({ onError }));
    await act(async () => {
      result.current.start();
      await Promise.resolve();
    });
    await waitFor(() => expect(FakeRecorder.last?.state).toBe('recording'));
    act(() => result.current.stop());
    await waitFor(() => expect(onError).toHaveBeenCalled());
    return onError.mock.calls[0];
  };

  it("hands on the server's reason for a 402", async () => {
    expect((await dictate(REFUSAL))[1]).toBe('This request was refused.');
  });

  it('hands on none for any other failure', async () => {
    expect((await dictate(OUTAGE))[1]).toBeUndefined();
  });
});
