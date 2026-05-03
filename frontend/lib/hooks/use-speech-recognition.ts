'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

interface UseSpeechRecognitionOptions {
  lang?: string;
  continuous?: boolean;
  interimResults?: boolean;
  onError?: (error: string) => void;
}

interface UseSpeechRecognitionReturn {
  isListening: boolean;
  isSupported: boolean;
  transcript: string;
  interimTranscript: string;
  start: () => void;
  stop: () => void;
  toggle: () => void;
  resetTranscript: () => void;
}

function getBrowserSpeechRecognition(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function useSpeechRecognition(
  options: UseSpeechRecognitionOptions = {}
): UseSpeechRecognitionReturn {
  const {
    lang = 'en-US',
    continuous = true,
    interimResults = true,
    onError,
  } = options;

  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [interimTranscript, setInterimTranscript] = useState('');

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const isListeningRef = useRef(false);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => { onErrorRef.current = onError; });

  const isSupported = typeof window !== 'undefined' && getBrowserSpeechRecognition() !== null;

  const stop = useCallback(() => {
    isListeningRef.current = false;
    setIsListening(false);
    setInterimTranscript('');
    recognitionRef.current?.stop();
  }, []);

  const start = useCallback(() => {
    const SpeechRecognitionAPI = getBrowserSpeechRecognition();
    if (!SpeechRecognitionAPI) return;

    // Stop any existing session before starting a new one
    if (recognitionRef.current) {
      recognitionRef.current.onend = null;
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }

    const recognition = new SpeechRecognitionAPI();
    recognition.continuous = continuous;
    recognition.interimResults = interimResults;
    recognition.lang = lang;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalText = '';
      let interim = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalText += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }

      if (finalText) {
        setTranscript((prev) => {
          const separator = prev.length > 0 ? ' ' : '';
          return prev + separator + finalText.trim();
        });
        setInterimTranscript('');
      } else {
        setInterimTranscript(interim);
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      // 'no-speech' and 'aborted' are expected during normal usage
      if (event.error === 'no-speech' || event.error === 'aborted') return;

      onErrorRef.current?.(event.error);
      isListeningRef.current = false;
      setIsListening(false);
      setInterimTranscript('');
    };

    recognition.onend = () => {
      // Auto-restart if the user hasn't explicitly stopped
      if (isListeningRef.current) {
        try {
          recognition.start();
        } catch {
          isListeningRef.current = false;
          setIsListening(false);
          setInterimTranscript('');
        }
        return;
      }
      setIsListening(false);
      setInterimTranscript('');
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
      isListeningRef.current = true;
      setIsListening(true);
      setTranscript('');
      setInterimTranscript('');
    } catch {
      onErrorRef.current?.('audio-capture');
    }
  }, [lang, continuous, interimResults]);

  const toggle = useCallback(() => {
    if (isListeningRef.current) {
      stop();
    } else {
      start();
    }
  }, [start, stop]);

  const resetTranscript = useCallback(() => {
    setTranscript('');
    setInterimTranscript('');
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.onend = null;
        recognitionRef.current.abort();
        recognitionRef.current = null;
      }
      isListeningRef.current = false;
    };
  }, []);

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
