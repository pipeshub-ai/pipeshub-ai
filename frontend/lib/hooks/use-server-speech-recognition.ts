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
  /**
   * Live partial transcript produced while the user is still speaking.
   * Populated from opportunistic `POST /chat/transcribe` calls against the
   * audio captured so far. Cleared once the final transcript commits on
   * stop.
   */
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
// Matches OpenAI's hard 25 MB limit for audio.transcriptions.create and
// keeps self-hosted faster-whisper within a sane single-request budget.
const MAX_AUDIO_BYTES = 25 * 1024 * 1024;

// MediaRecorder emits a chunk every this many milliseconds when started
// with a timeslice. A short slice keeps the live transcript close to
// real-time; anything shorter than ~500ms tends to produce under-sized
// chunks that the STT backend can't do much with.
const CHUNK_TIMESLICE_MS = 1000;

// Minimum gap between interim STT round-trips. OpenAI whisper-1 is roughly
// 1-3s for short clips, so pinging faster than this just burns tokens
// without getting meaningfully fresher text back.
const MIN_INTERIM_INTERVAL_MS = 1500;

// Skip interim transcribes for tiny blobs — the model can't do much with
// <1s of audio and we'd waste a request on noise / silence.
const MIN_INTERIM_BYTES = 6 * 1024;

// Stop firing interim requests once the accumulated audio exceeds this
// size. At that point every round-trip is transcribing the entire
// recording from scratch and the latency makes the partial useless; the
// final transcribe on stop still covers the full utterance.
const MAX_INTERIM_BYTES = 2 * 1024 * 1024;

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
 * Server-side Speech-to-Text hook with live partial results.
 *
 * Captures audio via `MediaRecorder` (with a 1s timeslice) and dispatches
 * periodic `POST /api/v1/chat/transcribe` calls against the audio
 * accumulated so far to populate `interimTranscript` as the user is
 * speaking. On stop, a final transcribe commits the full utterance to
 * `transcript` and clears the interim.
 *
 * Returns the same shape as `useSpeechRecognition` so callers can switch
 * between browser and server STT without additional branching.
 *
 * Note: because OpenAI / Whisper-style endpoints are single-shot rather
 * than truly streaming, each interim transcribes the entire buffer from
 * the start of the utterance. We throttle these requests and drop them
 * once the recording grows past ~2 MB so costs stay bounded.
 */
export function useServerSpeechRecognition(
  options: UseServerSpeechRecognitionOptions = {}
): UseServerSpeechRecognitionReturn {
  const { lang, onError } = options;

  const [transcript, setTranscript] = useState('');
  const [interimTranscript, setInterimTranscript] = useState('');
  const [isListening, setIsListening] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const mimeRef = useRef<string>(FALLBACK_MIME);
  const onErrorRef = useRef(onError);
  // Abort handle for the currently in-flight interim transcribe, so a new
  // chunk can cancel its predecessor instead of queueing behind it.
  const interimAbortRef = useRef<AbortController | null>(null);
  const interimInFlightRef = useRef<boolean>(false);
  const lastInterimStartRef = useRef<number>(0);
  // Indirection so `transcribeInterim` can re-trigger itself on completion
  // without creating a direct circular dependency with `maybeFireInterim`.
  const maybeFireInterimRef = useRef<() => void>(() => {});
  // Latch flipped during `stop()` so an in-flight interim response that
  // lands after the user has already stopped speaking doesn't overwrite
  // (or race with) the final commit.
  const stoppingRef = useRef<boolean>(false);
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
    if (interimAbortRef.current) {
      try {
        interimAbortRef.current.abort();
      } catch {
        // ignore
      }
      interimAbortRef.current = null;
    }
    interimInFlightRef.current = false;
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const buildForm = useCallback(
    (blob: Blob): FormData => {
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
      return form;
    },
    [lang]
  );

  /**
   * Best-effort interim transcribe. Failures (including aborts caused by
   * a newer chunk arriving) are swallowed so we don't spam the UI with
   * errors while the user is still speaking — the final pass on stop
   * will surface any real failure.
   */
  const transcribeInterim = useCallback(
    async (blob: Blob) => {
      if (blob.size < MIN_INTERIM_BYTES) return;
      if (blob.size > MAX_INTERIM_BYTES) return;
      if (stoppingRef.current) return;

      if (interimAbortRef.current) {
        try {
          interimAbortRef.current.abort();
        } catch {
          // ignore
        }
      }
      const controller = new AbortController();
      interimAbortRef.current = controller;
      interimInFlightRef.current = true;
      lastInterimStartRef.current = Date.now();

      try {
        const { data } = await apiClient.post<{ text: string }>(
          '/api/v1/chat/transcribe',
          buildForm(blob),
          {
            // Letting axios/the browser set the multipart Content-Type with
            // the correct boundary — this hint just keeps axios from
            // inheriting the default 'application/json'.
            headers: { 'Content-Type': 'multipart/form-data' },
            signal: controller.signal,
            // Keep interim requests snappy; if the round-trip is slower
            // than this the partial is already stale.
            timeout: 30_000,
            // Interim errors are best-effort — final pass will toast if
            // the pipeline is genuinely broken.
            suppressErrorToast: true,
          }
        );
        if (controller.signal.aborted) return;
        if (stoppingRef.current) return;
        const text = (data?.text ?? '').trim();
        // Interim always reflects the full rolling transcript of the
        // current utterance; on stop the final commit replaces it.
        setInterimTranscript(text);
      } catch {
        // Aborted or failed — interim updates are best-effort.
      } finally {
        if (interimAbortRef.current === controller) {
          interimAbortRef.current = null;
        }
        interimInFlightRef.current = false;
        // If more audio accumulated while we were in flight, immediately
        // try to dispatch the next interim instead of waiting for the
        // next MediaRecorder chunk boundary (which could be up to
        // CHUNK_TIMESLICE_MS away). The throttle + in-flight + stopping
        // guards inside maybeFireInterim keep this from runaway-looping.
        maybeFireInterimRef.current();
      }
    },
    [buildForm]
  );

  /**
   * Final transcribe — commits the complete utterance to `transcript`
   * and clears any still-displayed interim. Errors bubble up to the
   * caller's `onError` handler so the UI can surface a toast.
   */
  const transcribeFinal = useCallback(
    async (blob: Blob) => {
      if (blob.size === 0) {
        setInterimTranscript('');
        return;
      }
      if (blob.size > MAX_AUDIO_BYTES) {
        setInterimTranscript('');
        onErrorRef.current?.('audio-too-large');
        return;
      }
      try {
        const { data } = await apiClient.post<{ text: string }>(
          '/api/v1/chat/transcribe',
          buildForm(blob),
          {
            headers: { 'Content-Type': 'multipart/form-data' },
            // Mic->STT round-trips can exceed the 20s default (large
            // whisper models, cold faster-whisper load, slow provider).
            timeout: 120_000,
            suppressErrorToast: true,
          }
        );
        const text = (data?.text ?? '').trim();
        setInterimTranscript('');
        if (text) {
          setTranscript((prev) => (prev ? `${prev} ${text}` : text));
        }
      } catch (err) {
        setInterimTranscript('');
        const message =
          err instanceof Error ? err.message : 'Transcription failed';
        onErrorRef.current?.(message);
      }
    },
    [buildForm]
  );

  /**
   * Called from `ondataavailable`. Decides (based on throttle + in-flight
   * state) whether to actually dispatch a partial transcribe for the
   * audio captured so far.
   */
  const maybeFireInterim = useCallback(() => {
    if (stoppingRef.current) return;
    if (interimInFlightRef.current) return;
    const now = Date.now();
    if (now - lastInterimStartRef.current < MIN_INTERIM_INTERVAL_MS) return;
    if (chunksRef.current.length === 0) return;
    const blob = new Blob(chunksRef.current, { type: mimeRef.current });
    if (blob.size < MIN_INTERIM_BYTES) return;
    if (blob.size > MAX_INTERIM_BYTES) return;
    void transcribeInterim(blob);
  }, [transcribeInterim]);

  // Keep the ref pointed at the latest closure so `transcribeInterim` can
  // re-invoke it from its `finally` without capturing stale state.
  useLayoutEffect(() => {
    maybeFireInterimRef.current = maybeFireInterim;
  }, [maybeFireInterim]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    stoppingRef.current = true;
    if (interimAbortRef.current) {
      try {
        interimAbortRef.current.abort();
      } catch {
        // ignore
      }
      interimAbortRef.current = null;
    }
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
    stoppingRef.current = false;
    lastInterimStartRef.current = 0;
    setInterimTranscript('');

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
            // Opportunistically kick off an interim transcribe whenever a
            // new slice arrives. The throttle + in-flight guard inside
            // maybeFireInterim decides whether to actually dispatch.
            maybeFireInterim();
          }
        };

        recorder.onstop = async () => {
          const blob = new Blob(chunksRef.current, {
            type: mimeRef.current,
          });
          teardown();
          await transcribeFinal(blob);
        };

        recorder.onerror = () => {
          onErrorRef.current?.('recording-error');
          teardown();
          setIsListening(false);
        };

        // Timeslice is what turns a single monolithic blob-on-stop into a
        // stream of chunks during recording — without it, ondataavailable
        // only fires once, when stop() is called.
        recorder.start(CHUNK_TIMESLICE_MS);
        setIsListening(true);
      })
      .catch((err) => {
        const denied =
          err && typeof err === 'object' && 'name' in err && err.name === 'NotAllowedError';
        onErrorRef.current?.(denied ? 'not-allowed' : 'audio-capture');
        setIsListening(false);
      });
  }, [isSupported, teardown, maybeFireInterim, transcribeFinal]);

  const toggle = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      stop();
    } else {
      start();
    }
  }, [start, stop]);

  const resetTranscript = useCallback(() => {
    setTranscript('');
    setInterimTranscript('');
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
    interimTranscript,
    start,
    stop,
    toggle,
    resetTranscript,
  };
}
