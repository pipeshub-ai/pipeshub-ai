'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

interface UseSpeechSynthesisOptions {
  lang?: string;
  rate?: number;
  pitch?: number;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

interface UseSpeechSynthesisReturn {
  isSpeaking: boolean;
  isSupported: boolean;
  speak: (text: string) => void;
  stop: () => void;
}

function getSpeechSynthesis(): SpeechSynthesis | null {
  if (typeof window === 'undefined') return null;
  return window.speechSynthesis ?? null;
}

const PREMIUM_VOICE_KEYWORDS = /natural|enhanced|premium|neural|online/i;

/**
 * Pick the highest-quality voice for the given BCP-47 language tag.
 *
 * Ranking strategy (first match wins):
 *   1. Voice whose name contains a premium keyword (Natural, Enhanced, etc.)
 *      AND matches the full locale (e.g. "en-US").
 *   2. Premium-keyword voice matching just the base language ("en").
 *   3. Any voice matching the full locale.
 *   4. Any voice matching the base language.
 *   5. null — let the browser fall back to its default.
 */
function pickBestVoice(
  voices: SpeechSynthesisVoice[],
  lang: string
): SpeechSynthesisVoice | null {
  if (voices.length === 0) return null;

  const base = lang.split('-')[0].toLowerCase();

  let premiumLocale: SpeechSynthesisVoice | null = null;
  let premiumBase: SpeechSynthesisVoice | null = null;
  let localeMatch: SpeechSynthesisVoice | null = null;
  let baseMatch: SpeechSynthesisVoice | null = null;

  for (const v of voices) {
    const vLang = v.lang.toLowerCase();
    const vBase = vLang.split('-')[0];
    const isPremium = PREMIUM_VOICE_KEYWORDS.test(v.name);

    if (isPremium && vLang === lang.toLowerCase() && !premiumLocale) {
      premiumLocale = v;
    } else if (isPremium && vBase === base && !premiumBase) {
      premiumBase = v;
    } else if (vLang === lang.toLowerCase() && !localeMatch) {
      localeMatch = v;
    } else if (vBase === base && !baseMatch) {
      baseMatch = v;
    }
  }

  return premiumLocale ?? premiumBase ?? localeMatch ?? baseMatch ?? null;
}

export function useSpeechSynthesis(
  options: UseSpeechSynthesisOptions = {}
): UseSpeechSynthesisReturn {
  const { lang = 'en-US', rate = 1, pitch = 1, onEnd, onError } = options;

  const [isSpeaking, setIsSpeaking] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const onEndRef = useRef(onEnd);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => {
    onEndRef.current = onEnd;
    onErrorRef.current = onError;
  });

  const isSupported =
    typeof window !== 'undefined' && getSpeechSynthesis() !== null;

  // Voices load asynchronously in Chrome/Edge — listen for the event.
  useEffect(() => {
    const synth = getSpeechSynthesis();
    if (!synth) return;

    const loadVoices = () => setVoices(synth.getVoices());
    loadVoices();

    synth.addEventListener('voiceschanged', loadVoices);
    return () => synth.removeEventListener('voiceschanged', loadVoices);
  }, []);

  const stop = useCallback(() => {
    const synth = getSpeechSynthesis();
    if (!synth) return;
    synth.cancel();
    utteranceRef.current = null;
    setIsSpeaking(false);
  }, []);

  const speak = useCallback(
    (text: string) => {
      const synth = getSpeechSynthesis();
      if (!synth || !text) return;

      synth.cancel();
      utteranceRef.current = null;

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = lang;
      utterance.rate = rate;
      utterance.pitch = pitch;

      const voice = pickBestVoice(voices, lang);
      if (voice) utterance.voice = voice;

      utterance.onstart = () => {
        setIsSpeaking(true);
      };

      utterance.onend = () => {
        setIsSpeaking(false);
        utteranceRef.current = null;
        onEndRef.current?.();
      };

      utterance.onerror = (event: SpeechSynthesisErrorEvent) => {
        if (event.error === 'canceled' || event.error === 'interrupted') {
          setIsSpeaking(false);
          utteranceRef.current = null;
          return;
        }
        setIsSpeaking(false);
        utteranceRef.current = null;
        onErrorRef.current?.(event.error);
      };

      utteranceRef.current = utterance;
      synth.speak(utterance);
    },
    [lang, rate, pitch, voices]
  );

  useEffect(() => {
    return () => {
      const synth = getSpeechSynthesis();
      if (synth) synth.cancel();
      utteranceRef.current = null;
    };
  }, []);

  return {
    isSpeaking,
    isSupported,
    speak,
    stop,
  };
}
