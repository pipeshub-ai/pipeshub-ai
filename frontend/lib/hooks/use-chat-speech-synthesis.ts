'use client';

import { useChatSpeechConfig } from './use-chat-speech-config';
import { useServerSpeechSynthesis } from './use-server-speech-synthesis';
import { useSpeechSynthesis } from './use-speech-synthesis';

interface UseChatSpeechSynthesisOptions {
  lang?: string;
  rate?: number;
  pitch?: number;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

interface UseChatSpeechSynthesisReturn {
  isSpeaking: boolean;
  isSupported: boolean;
  speak: (text: string) => void;
  stop: () => void;
  /** Diagnostic flag; true when the current output source is the server TTS route. */
  usingServerTts: boolean;
}

/**
 * Composite text-to-speech hook used by the chat UI.
 *
 * When the backend reports a configured TTS provider we POST to
 * `/api/v1/chat/speak` and play the returned audio blob; otherwise the
 * browser's `window.speechSynthesis` is used. Both underlying hooks are
 * called unconditionally (stable hook order).
 */
export function useChatSpeechSynthesis(
  options: UseChatSpeechSynthesisOptions = {}
): UseChatSpeechSynthesisReturn {
  const { hasTts, isLoading } = useChatSpeechConfig();

  const browser = useSpeechSynthesis({
    lang: options.lang,
    rate: options.rate,
    pitch: options.pitch,
    onEnd: options.onEnd,
    onError: options.onError,
  });
  const server = useServerSpeechSynthesis({
    rate: options.rate,
    onEnd: options.onEnd,
    onError: options.onError,
  });

  const useServer = hasTts && !isLoading;
  const active = useServer ? server : browser;

  return {
    isSpeaking: active.isSpeaking,
    isSupported: active.isSupported,
    speak: active.speak,
    stop: active.stop,
    usingServerTts: useServer,
  };
}
