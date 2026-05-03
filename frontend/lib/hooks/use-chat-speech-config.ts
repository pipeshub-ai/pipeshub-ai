'use client';

import useSWR from 'swr';

import { apiClient } from '@/lib/api';

interface SpeechCapabilitySummary {
  provider: string;
  /**
   * Active (default) model id the server will dispatch to. Mirrors
   * `defaultModel`; retained for backwards compatibility with clients that
   * were written before the field was added.
   */
  model: string | null;
  /** Model id the server uses when no explicit override is passed. */
  defaultModel?: string | null;
  /** All models exposed by the active provider config, in admin order. */
  models?: string[];
  /**
   * `true` when the returned config was picked because it is flagged
   * `isDefault` in `aiModels`; `false` when no entry was flagged and the
   * server fell back to the first configured one.
   */
  isDefault?: boolean;
  /** Stable identifier assigned to the config entry by the admin UI. */
  modelKey?: string | null;
  friendlyName?: string | null;
}

interface SpeechCapabilitiesResponse {
  tts: SpeechCapabilitySummary | null;
  stt: SpeechCapabilitySummary | null;
}

export interface ChatSpeechConfig {
  /** True when an admin has configured a server-side STT provider. */
  hasStt: boolean;
  /** True when an admin has configured a server-side TTS provider. */
  hasTts: boolean;
  tts: SpeechCapabilitySummary | null;
  stt: SpeechCapabilitySummary | null;
  /** True while the initial capabilities fetch is in-flight. */
  isLoading: boolean;
  /** Error from the capabilities endpoint, if any. */
  error: unknown;
}

const CAPABILITIES_ENDPOINT = '/api/v1/chat/speech/capabilities';

// Local fetcher so capability probes don't fire the global error toast on
// transient backend failures — a missing capability simply means the UI
// should fall back to the browser's Web Speech API.
async function silentCapabilitiesFetcher(
  url: string
): Promise<SpeechCapabilitiesResponse> {
  const { data } = await apiClient.get<SpeechCapabilitiesResponse>(url, {
    suppressErrorToast: true,
  });
  return data;
}

/**
 * Fetches the server's configured TTS/STT providers so the chat UI can pick
 * between server-backed and browser speech APIs.
 *
 * A failed fetch (network / auth / backend down) is treated as "unconfigured"
 * so the UX always falls back to the browser's Web Speech API rather than
 * breaking the mic / speaker button.
 */
export function useChatSpeechConfig(): ChatSpeechConfig {
  const { data, error, isLoading } = useSWR<SpeechCapabilitiesResponse>(
    CAPABILITIES_ENDPOINT,
    silentCapabilitiesFetcher,
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
      dedupingInterval: 60_000,
    }
  );

  const tts = data?.tts ?? null;
  const stt = data?.stt ?? null;

  return {
    hasTts: Boolean(tts),
    hasStt: Boolean(stt),
    tts,
    stt,
    isLoading,
    error,
  };
}
