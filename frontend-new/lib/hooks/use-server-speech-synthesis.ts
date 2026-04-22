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

// Keep chunks small so the first audio blob arrives quickly. The provider
// cost scales with total characters, not number of requests, so splitting
// doesn't change billing — it only improves time-to-first-audio.
//
// The first chunk is especially aggressive (one sentence, capped tight) so
// the user hears playback start within a second or two even for long
// answers; subsequent chunks are larger to keep the pipeline efficient.
const FIRST_CHUNK_MAX_CHARS = 220;
const CHUNK_MAX_CHARS = 600;

// How many chunks can be in flight to the TTS backend at once. A value of 2
// means: while the current chunk is playing, the next is already being
// synthesized. Higher values can overrun provider rate limits for long
// responses and rarely improve perceived latency further.
const PIPELINE_DEPTH = 2;

// Sentence terminators we split on. Multi-character terminators (e.g. "...")
// are normalised by the regex below; we keep the terminator attached to the
// preceding sentence so the TTS engine gets natural prosody cues.
const SENTENCE_SPLIT_RE = /(?<=[.!?。！？])\s+(?=\S)/;

/**
 * Split a block of text into ordered chunks of speakable length.
 *
 * Rules applied in order:
 *   1. Split on sentence terminators.
 *   2. Any sentence longer than {@link CHUNK_MAX_CHARS} is further split on
 *      clause boundaries (", " / "; " / ": " / " — ") and as a last resort
 *      on whitespace.
 *   3. Adjacent sentences are coalesced into the same chunk up to the
 *      per-chunk cap — smaller for the first chunk (fast first audio),
 *      larger for the rest.
 *
 * The produced chunks are non-empty and preserve original ordering.
 */
function splitTextIntoChunks(text: string): string[] {
  const normalised = text.replace(/\s+/g, ' ').trim();
  if (!normalised) return [];

  const sentences = normalised
    .split(SENTENCE_SPLIT_RE)
    .map((s) => s.trim())
    .filter(Boolean);

  const pieces: string[] = [];
  for (const sentence of sentences) {
    if (sentence.length <= CHUNK_MAX_CHARS) {
      pieces.push(sentence);
      continue;
    }
    pieces.push(...splitLongSentence(sentence, CHUNK_MAX_CHARS));
  }

  const chunks: string[] = [];
  let buffer = '';
  let isFirst = true;
  const capFor = () => (isFirst ? FIRST_CHUNK_MAX_CHARS : CHUNK_MAX_CHARS);

  const flush = () => {
    if (buffer) {
      chunks.push(buffer);
      buffer = '';
      isFirst = false;
    }
  };

  for (const piece of pieces) {
    if (!buffer) {
      buffer = piece;
      continue;
    }
    if (buffer.length + 1 + piece.length <= capFor()) {
      buffer += ` ${piece}`;
    } else {
      flush();
      buffer = piece;
    }
  }
  flush();

  return chunks;
}

function splitLongSentence(sentence: string, maxChars: number): string[] {
  const out: string[] = [];
  let remaining = sentence;

  const softBoundary = /[,;:—]\s+/g;

  while (remaining.length > maxChars) {
    let cut = -1;
    softBoundary.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = softBoundary.exec(remaining)) !== null) {
      const end = match.index + match[0].length;
      if (end > maxChars) break;
      cut = end;
    }
    if (cut <= 0) {
      // No soft boundary fit — fall back to the last space within the cap.
      cut = remaining.lastIndexOf(' ', maxChars);
      if (cut <= 0) cut = maxChars;
    }
    out.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) out.push(remaining);
  return out;
}

/**
 * Server-side Text-to-Speech hook with pipelined chunk streaming.
 *
 * Rather than POSTing the whole message to `/api/v1/chat/speak` and waiting
 * for the entire audio blob before playback begins, this hook splits the
 * text into sentence-sized chunks, keeps up to {@link PIPELINE_DEPTH}
 * requests in flight, and plays the returned blobs back-to-back in order.
 * Time-to-first-audio therefore scales with the first sentence rather than
 * the full response length, which matters for long chat answers.
 *
 * The exposed interface matches `useSpeechSynthesis` so callers can swap
 * implementations transparently.
 */
export function useServerSpeechSynthesis(
  options: UseServerSpeechSynthesisOptions = {}
): UseServerSpeechSynthesisReturn {
  const { voice, format, rate, onEnd, onError } = options;

  const [isSpeaking, setIsSpeaking] = useState(false);

  // Monotonically-increasing session id. Every speak() call bumps it so
  // stale callbacks from a previous (aborted) session can short-circuit.
  const sessionIdRef = useRef(0);

  // Per-session state. Held in a ref so that long-lived callbacks (audio
  // end, fetch resolution) can read the latest value without re-binding.
  //
  // `audio` is created eagerly inside `speak()` — i.e. during the user's
  // click handler — and reused across every chunk in the session by
  // swapping its `src`. Creating a fresh `new Audio()` for each chunk
  // worked for chunk #0 (the user gesture was still on the stack) but
  // Safari and stricter Chromium autoplay policies reject `play()` on
  // Audio elements constructed asynchronously after a network await,
  // which produced a confusing "stops after the first sentence" failure.
  interface Session {
    id: number;
    chunks: string[];
    // In-flight or completed chunk fetches, indexed by chunk position.
    jobs: ChunkJob[];
    // Index of the next chunk whose fetch hasn't been started yet.
    nextFetchIdx: number;
    // Index of the chunk currently playing (or about to play).
    playIdx: number;
    audio: HTMLAudioElement | null;
    objectUrl: string | null;
    cancelled: boolean;
  }
  interface ChunkJob {
    controller: AbortController;
    promise: Promise<Blob>;
  }
  const sessionRef = useRef<Session | null>(null);

  const onEndRef = useRef(onEnd);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => {
    onEndRef.current = onEnd;
    onErrorRef.current = onError;
  });

  const voiceRef = useRef(voice);
  const formatRef = useRef(format);
  const rateRef = useRef(rate);
  useLayoutEffect(() => {
    voiceRef.current = voice;
    formatRef.current = format;
    rateRef.current = rate;
  });

  const isSupported = typeof window !== 'undefined' && typeof Audio !== 'undefined';

  const teardownPlayback = useCallback((session: Session) => {
    if (session.audio) {
      try {
        session.audio.pause();
      } catch {
        // ignore
      }
      // Detach event handlers before clearing src so the implicit "empty
      // src" transition doesn't retrigger our onerror/onended logic.
      session.audio.onended = null;
      session.audio.onerror = null;
      try {
        session.audio.removeAttribute('src');
        session.audio.load();
      } catch {
        // ignore
      }
      session.audio = null;
    }
    if (session.objectUrl) {
      URL.revokeObjectURL(session.objectUrl);
      session.objectUrl = null;
    }
  }, []);

  const endSession = useCallback(
    (session: Session, reason: 'completed' | 'cancelled' | 'error', errorMessage?: string) => {
      if (session.cancelled && reason !== 'cancelled') return;
      session.cancelled = true;

      for (const job of session.jobs) {
        try {
          job.controller.abort();
        } catch {
          // ignore
        }
      }

      teardownPlayback(session);

      // Only the owning session is allowed to clear the shared ref; a newer
      // session may have already replaced us.
      if (sessionRef.current && sessionRef.current.id === session.id) {
        sessionRef.current = null;
        setIsSpeaking(false);
      }

      if (reason === 'completed') {
        onEndRef.current?.();
      } else if (reason === 'error') {
        onErrorRef.current?.(errorMessage ?? 'speech-synthesis-failed');
      }
    },
    [teardownPlayback]
  );

  const fetchChunk = useCallback((text: string, controller: AbortController): Promise<Blob> => {
    const body: Record<string, unknown> = { text };
    if (voiceRef.current) body.voice = voiceRef.current;
    if (formatRef.current) body.format = formatRef.current;
    if (rateRef.current) body.speed = rateRef.current;

    return apiClient
      .post<Blob>('/api/v1/chat/speak', body, {
        responseType: 'blob',
        signal: controller.signal,
        // Individual sentence requests are fast, but a very long sentence
        // can still take ~10s on some providers; stay well inside the
        // proxy's 120s ceiling.
        timeout: 60_000,
        // Hook surfaces errors via onError; suppress the global toast.
        suppressErrorToast: true,
      })
      .then((resp) => resp.data);
  }, []);

  const ensureFetchesQueued = useCallback(
    (session: Session) => {
      while (
        !session.cancelled &&
        session.nextFetchIdx < session.chunks.length &&
        session.nextFetchIdx - session.playIdx < PIPELINE_DEPTH
      ) {
        const idx = session.nextFetchIdx++;
        const controller = new AbortController();
        const promise = fetchChunk(session.chunks[idx], controller);
        session.jobs[idx] = { controller, promise };
        // Prevent unhandled rejection warnings for aborted sessions; the
        // actual consumer (playNextChunk) re-awaits this promise.
        promise.catch(() => undefined);
      }
    },
    [fetchChunk]
  );

  // Holds the latest playNextChunk closure so the audio.onended callback can
  // recurse without forward-referencing the function inside its own body
  // (which the React Compiler's immutability rule forbids).
  const playNextChunkRef = useRef<((session: Session) => Promise<void>) | null>(null);

  const playNextChunk = useCallback(
    async (session: Session) => {
      if (session.cancelled) return;
      if (session.playIdx >= session.chunks.length) {
        endSession(session, 'completed');
        return;
      }

      ensureFetchesQueued(session);

      const job = session.jobs[session.playIdx];
      let blob: Blob;
      try {
        blob = await job.promise;
      } catch (err) {
        if (session.cancelled) return;
        const message = err instanceof Error ? err.message : 'speech-synthesis-failed';
        endSession(session, 'error', message);
        return;
      }
      if (session.cancelled) return;

      // `session.audio` is set during `speak()` inside the user-gesture
      // frame, so by reusing that same element we inherit its autoplay
      // permission for every subsequent chunk. Only construct a new one
      // if something external (e.g. a teardown bug) cleared it.
      const audio = session.audio ?? new Audio();
      session.audio = audio;

      // Swap source to the current chunk's blob. Revoke the previous
      // URL only after the new one is assigned so the decoder never
      // observes a disconnected src.
      const previousUrl = session.objectUrl;
      const url = URL.createObjectURL(blob);
      session.objectUrl = url;
      audio.src = url;
      if (previousUrl) {
        URL.revokeObjectURL(previousUrl);
      }

      audio.onended = () => {
        if (session.cancelled) return;
        session.playIdx += 1;
        void playNextChunkRef.current?.(session);
      };
      audio.onerror = () => {
        if (session.cancelled) return;
        endSession(session, 'error', 'playback-error');
      };

      try {
        await audio.play();
      } catch (err) {
        if (session.cancelled) return;
        const message = err instanceof Error ? err.message : 'playback-error';
        endSession(session, 'error', message);
        return;
      }

      // Opportunistically prefetch further chunks now that one slot is
      // "playing" rather than "queued".
      ensureFetchesQueued(session);
    },
    [endSession, ensureFetchesQueued]
  );

  useLayoutEffect(() => {
    playNextChunkRef.current = playNextChunk;
  });

  const stop = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    endSession(session, 'cancelled');
  }, [endSession]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported || !text || !text.trim()) return;

      // Cancel any previous session first so its callbacks can't mutate
      // the new one we're about to install.
      if (sessionRef.current) {
        endSession(sessionRef.current, 'cancelled');
      }

      const chunks = splitTextIntoChunks(text);
      if (chunks.length === 0) return;

      // Construct the Audio element synchronously inside `speak()` so the
      // browser attributes it to the current user gesture (the click
      // that triggered TTS). This element is reused for every chunk by
      // reassigning `src`, which preserves the autoplay permission even
      // after network awaits that would otherwise break the gesture
      // chain — previously the 2nd chunk's freshly-constructed Audio
      // element would fail `play()` on Safari / strict Chromium.
      let audio: HTMLAudioElement | null = null;
      if (typeof Audio !== 'undefined') {
        audio = new Audio();
        // Using preload='auto' lets the element prime its media pipeline
        // immediately; without it Safari occasionally defers the first
        // load until after the user gesture has expired.
        audio.preload = 'auto';
      }

      sessionIdRef.current += 1;
      const session: Session = {
        id: sessionIdRef.current,
        chunks,
        jobs: new Array(chunks.length),
        nextFetchIdx: 0,
        playIdx: 0,
        audio,
        objectUrl: null,
        cancelled: false,
      };
      sessionRef.current = session;
      setIsSpeaking(true);

      ensureFetchesQueued(session);
      void playNextChunk(session);
    },
    [endSession, ensureFetchesQueued, isSupported, playNextChunk]
  );

  // Cleanup on unmount — abort any in-flight session.
  useEffect(
    () => () => {
      if (sessionRef.current) {
        endSession(sessionRef.current, 'cancelled');
      }
    },
    [endSession]
  );

  return {
    isSpeaking,
    isSupported,
    speak,
    stop,
  };
}
