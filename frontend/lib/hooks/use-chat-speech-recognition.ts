'use client';

import { useChatSpeechConfig } from './use-chat-speech-config';
import { useServerSpeechRecognition } from './use-server-speech-recognition';
import { useSpeechRecognition } from './use-speech-recognition';

interface UseChatSpeechRecognitionOptions {
  lang?: string;
  continuous?: boolean;
  interimResults?: boolean;
  onError?: (error: string) => void;
}

interface UseChatSpeechRecognitionReturn {
  isListening: boolean;
  isSupported: boolean;
  transcript: string;
  interimTranscript: string;
  start: () => void;
  stop: () => void;
  toggle: () => void;
  resetTranscript: () => void;
  /** Diagnostic flag; true when the current transcript source is the server STT route. */
  usingServerStt: boolean;
}

/**
 * Composite speech-recognition hook used by the chat UI.
 *
 * - When an admin has configured an STT provider under `/services/aiModels`,
 *   audio is recorded via `MediaRecorder` and uploaded to
 *   `POST /api/v1/chat/transcribe`.
 * - Otherwise, the browser's native `window.SpeechRecognition` is used.
 *
 * We intentionally call **both** underlying hooks on every render (React
 * requires a stable hook-call order); only the active one is started and its
 * state is exposed via the returned object.
 */
export function useChatSpeechRecognition(
  options: UseChatSpeechRecognitionOptions = {}
): UseChatSpeechRecognitionReturn {
  const { hasStt, isLoading } = useChatSpeechConfig();

  const browser = useSpeechRecognition(options);
  const server = useServerSpeechRecognition({
    lang: options.lang,
    onError: options.onError,
  });

  // While the capabilities request is still loading we default to the browser
  // hook so the mic button never locks up waiting on a backend round-trip.
  const useServer = hasStt && !isLoading;
  const active = useServer ? server : browser;

  return {
    isListening: active.isListening,
    isSupported: active.isSupported,
    transcript: active.transcript,
    interimTranscript: active.interimTranscript,
    start: active.start,
    stop: active.stop,
    toggle: active.toggle,
    resetTranscript: active.resetTranscript,
    usingServerStt: useServer,
  };
}
