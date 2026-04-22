'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { apiClient } from '@/lib/api';

interface UseServerSpeechRecognitionOptions {
  /** Optional BCP-47 language tag, forwarded to the STT provider. */
  lang?: string;
  onError?: (error: string) => void;
}

interface UseServerSpeechRecognitionReturn {
  isListening: boolean;
  isSupported: boolean;
  transcript: string;
  /** Always empty for server STT (no interim results without streaming). */
  interimTranscript: string;
  start: () => void;
  stop: () => void;
  toggle: () => void;
  resetTranscript: () => void;
}

const FALLBACK_MIME = 'audio/webm';
const PREFERRED_MIMES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
  'audio/mpeg',
];

function pickRecorderMime(): string {
  if (typeof window === 'undefined' || typeof MediaRecorder === 'undefined') {
    return FALLBACK_MIME;
  }
  for (const mime of PREFERRED_MIMES) {
    try {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    } catch {
      // Some older browsers throw here; fall through.
    }
  }
  return FALLBACK_MIME;
}

/**
 * Server-side Speech-to-Text hook.
 *
 * Captures a single utterance via `MediaRecorder`, uploads it to
 * `POST /api/v1/chat/transcribe`, then resolves the transcript. Returns the
 * same shape as `useSpeechRecognition` so callers can switch between browser
 * and server STT without additional branching.
 */
export function useServerSpeechRecognition(
  options: UseServerSpeechRecognitionOptions = {}
): UseServerSpeechRecognitionReturn {
  const { lang, onError } = options;

  const [transcript, setTranscript] = useState('');
  const [isListening, setIsListening] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const mimeRef = useRef<string>(FALLBACK_MIME);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => {
    onErrorRef.current = onError;
  });

  // MediaRecorder is widely supported in Chromium/Firefox/Safari 14.1+.
  const isSupported =
    typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices !== 'undefined' &&
    typeof MediaRecorder !== 'undefined';

  const teardown = useCallback(() => {
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        try {
          track.stop();
        } catch {
          // Ignore — some browsers may throw if the track is already stopped.
        }
      }
      streamRef.current = null;
    }
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const uploadAndTranscribe = useCallback(
    async (blob: Blob) => {
      if (blob.size === 0) return;
      try {
        const form = new FormData();
        const ext = mimeRef.current.includes('mp4')
          ? 'mp4'
          : mimeRef.current.includes('mpeg')
            ? 'mp3'
            : mimeRef.current.includes('ogg')
              ? 'ogg'
              : 'webm';
        form.append('file', blob, `speech.${ext}`);
        if (lang) {
          // The provider accepts ISO-639-1 (e.g. "en"); trim locale to base.
          form.append('language', lang.split('-')[0]);
        }
        const { data } = await apiClient.post<{ text: string }>(
          '/api/v1/chat/transcribe',
          form,
        );
        const text = (data?.text ?? '').trim();
        if (text) {
          setTranscript((prev) => (prev ? `${prev} ${text}` : text));
        }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Transcription failed';
        onErrorRef.current?.(message);
      }
    },
    [lang]
  );

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    setIsListening(false);
    if (recorder && recorder.state !== 'inactive') {
      try {
        recorder.stop();
      } catch {
        teardown();
      }
    } else {
      teardown();
    }
  }, [teardown]);

  const start = useCallback(() => {
    if (!isSupported) {
      onErrorRef.current?.('not-supported');
      return;
    }
    if (recorderRef.current) return;

    mimeRef.current = pickRecorderMime();

    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        streamRef.current = stream;
        let recorder: MediaRecorder;
        try {
          recorder = new MediaRecorder(stream, { mimeType: mimeRef.current });
        } catch {
          recorder = new MediaRecorder(stream);
          mimeRef.current = recorder.mimeType || FALLBACK_MIME;
        }
        recorderRef.current = recorder;
        chunksRef.current = [];

        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            chunksRef.current.push(event.data);
          }
        };

        recorder.onstop = async () => {
          const blob = new Blob(chunksRef.current, {
            type: mimeRef.current,
          });
          teardown();
          await uploadAndTranscribe(blob);
        };

        recorder.onerror = () => {
          onErrorRef.current?.('recording-error');
          teardown();
          setIsListening(false);
        };

        recorder.start();
        setIsListening(true);
      })
      .catch((err) => {
        const denied =
          err && typeof err === 'object' && 'name' in err && err.name === 'NotAllowedError';
        onErrorRef.current?.(denied ? 'not-allowed' : 'audio-capture');
        setIsListening(false);
      });
  }, [isSupported, teardown, uploadAndTranscribe]);

  const toggle = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      stop();
    } else {
      start();
    }
  }, [start, stop]);

  const resetTranscript = useCallback(() => {
    setTranscript('');
  }, []);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch {
          // ignore
        }
      }
      teardown();
    };
  }, [teardown]);

  return {
    isListening,
    isSupported,
    transcript,
    interimTranscript: '',
    start,
    stop,
    toggle,
    resetTranscript,
  };
}
