'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { apiClient } from '@/lib/api';

interface UseServerSpeechSynthesisOptions {
  /** Optional voice override (otherwise the provider default is used). */
  voice?: string;
  /** Optional output format ("mp3" | "opus" | "aac" | "flac" | "wav"). */
  format?: string;
  /** Playback speed (0.25 – 4.0). */
  rate?: number;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

interface UseServerSpeechSynthesisReturn {
  isSpeaking: boolean;
  isSupported: boolean;
  speak: (text: string) => void;
  stop: () => void;
}

/**
 * Server-side Text-to-Speech hook.
 *
 * Posts the requested text to `POST /api/v1/chat/speak`, receives a binary
 * audio blob, and plays it through an in-memory `HTMLAudioElement`. Exposes
 * the same interface as `useSpeechSynthesis` so callers can swap the two.
 */
export function useServerSpeechSynthesis(
  options: UseServerSpeechSynthesisOptions = {}
): UseServerSpeechSynthesisReturn {
  const { voice, format, rate, onEnd, onError } = options;

  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  // Tracks in-flight `apiClient.post` so overlapping speak() calls abort the
  // previous request instead of racing each other to play.
  const abortRef = useRef<AbortController | null>(null);

  const onEndRef = useRef(onEnd);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => {
    onEndRef.current = onEnd;
    onErrorRef.current = onError;
  });

  const isSupported = typeof window !== 'undefined' && typeof Audio !== 'undefined';

  const cleanupAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      try {
        audio.pause();
      } catch {
        // ignore
      }
      audio.src = '';
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    cleanupAudio();
    setIsSpeaking(false);
  }, [cleanupAudio]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported || !text || !text.trim()) return;

      // Cancel any previous request / playback.
      if (abortRef.current) {
        abortRef.current.abort();
      }
      cleanupAudio();

      const controller = new AbortController();
      abortRef.current = controller;
      setIsSpeaking(true);

      (async () => {
        try {
          const { data: blob } = await apiClient.post<Blob>(
            '/api/v1/chat/speak',
            {
              text,
              ...(voice ? { voice } : {}),
              ...(format ? { format } : {}),
              ...(rate ? { speed: rate } : {}),
            },
            {
              responseType: 'blob',
              signal: controller.signal,
            }
          );

          if (controller.signal.aborted) return;

          const url = URL.createObjectURL(blob);
          urlRef.current = url;

          const audio = new Audio(url);
          audioRef.current = audio;

          audio.onended = () => {
            cleanupAudio();
            setIsSpeaking(false);
            onEndRef.current?.();
          };
          audio.onerror = () => {
            cleanupAudio();
            setIsSpeaking(false);
            onErrorRef.current?.('playback-error');
          };

          await audio.play();
        } catch (err) {
          if (controller.signal.aborted) return;
          cleanupAudio();
          setIsSpeaking(false);
          const message =
            err instanceof Error ? err.message : 'speech-synthesis-failed';
          onErrorRef.current?.(message);
        } finally {
          if (abortRef.current === controller) {
            abortRef.current = null;
          }
        }
      })();
    },
    [cleanupAudio, format, isSupported, rate, voice]
  );

  // Cleanup on unmount
  useEffect(
    () => () => {
      if (abortRef.current) abortRef.current.abort();
      cleanupAudio();
    },
    [cleanupAudio]
  );

  return {
    isSpeaking,
    isSupported,
    speak,
    stop,
  };
}
