'use client';

import { isElectron } from '@/lib/electron';

import { useChatSpeechConfig } from './use-chat-speech-config';
import { useServerSpeechRecognition } from './use-server-speech-recognition';
import { useSpeechRecognition } from './use-speech-recognition';

interface NavigatorWithBrave extends Navigator {
  brave?: {
    isBrave?: () => Promise<boolean>;
  };
}

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
  /**
   * Voice input requires a configured server STT provider when the browser
   * path is unavailable: Electron's packaged `app://` origin cannot use
   * Chrome's upstream speech service, Brave/Edge should always use our
   * server STT path, and some browsers do not expose the Web Speech
   * recognizer at all. `'stt-not-configured'` when no provider is set;
   * `'stt-loading'` while the capabilities probe is still in flight;
   * `null` when voice input is available.
   */
  unavailableReason: 'stt-not-configured' | 'stt-loading' | null;
}

function isBraveBrowser(): boolean {
  if (typeof navigator === 'undefined') return false;
  return Boolean((navigator as NavigatorWithBrave).brave?.isBrave);
}

function isEdgeBrowser(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /\b(?:Edg|EdgA|EdgiOS|Edge)\//.test(navigator.userAgent);
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

  // In Electron the browser path is non-functional regardless of what
  // `window.SpeechRecognition` reports: Chromium ships the API surface,
  // but the upstream Google speech endpoint rejects requests from the
  // `app://` origin / missing API key, so the recognizer ends ~instantly
  // after `start()`. Brave and Edge should also use the configured server STT
  // route instead of the browser recognizer. Some browsers expose no
  // recognizer at all. Surface these cases to the UI so the mic button can be
  // disabled with an explanatory "configure STT" tooltip instead of silently
  // failing.
  const requiresServerStt =
    isElectron() || isBraveBrowser() || isEdgeBrowser() || !browser.isSupported;
  const unavailableReason: UseChatSpeechRecognitionReturn['unavailableReason'] =
    requiresServerStt && !useServer
      ? isLoading
        ? 'stt-loading'
        : 'stt-not-configured'
      : null;

  return {
    isListening: active.isListening,
    isSupported: unavailableReason ? false : active.isSupported,
    transcript: active.transcript,
    interimTranscript: active.interimTranscript,
    start: active.start,
    stop: active.stop,
    toggle: active.toggle,
    resetTranscript: active.resetTranscript,
    usingServerStt: useServer,
    unavailableReason,
  };
}
