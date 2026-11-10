import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiClient: { get } }));

import { useChatSpeechConfig } from '../use-chat-speech-config';
import { clearSessionCache } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';

const NO_SPEECH = { tts: null, stt: null };
const WITH_TTS = { tts: { provider: 'openAI', model: 'tts-1' }, stt: null };

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mountComposer() {
  const view = renderHook(() => useChatSpeechConfig().hasTts);
  await settle();
  return view;
}

describe('useChatSpeechConfig cache', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    clearSessionCache();
    get.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('asks the server once per session, quietly', async () => {
    get.mockResolvedValue({ data: NO_SPEECH });
    (await mountComposer()).unmount();
    await settle(3_600_000);
    const view = await mountComposer();

    expect(view.result.current).toBe(false);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('/api/v1/chat/speech/capabilities', { suppressErrorToast: true });
  });

  it('a model write made while the composer is not shown refreshes it on its next mount', async () => {
    get.mockResolvedValue({ data: NO_SPEECH });
    (await mountComposer()).unmount();

    get.mockResolvedValue({ data: WITH_TTS });
    revalidateSwrNamespace(SWR_NS.aiModelConfig);
    const view = await mountComposer();

    expect(get).toHaveBeenCalledTimes(2);
    expect(view.result.current).toBe(true);
  });
});
