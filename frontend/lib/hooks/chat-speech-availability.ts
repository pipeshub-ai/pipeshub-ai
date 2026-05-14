'use client';

import { isElectron } from '@/lib/electron';

export type BrowserSpeechUnavailableReason =
  | 'server-render'
  | 'browser-unavailable'
  | 'electron'
  | 'insecure-context'
  | 'microphone-denied'
  | 'microphone-unavailable'
  | 'network'
  | 'service-not-available'
  | 'language-not-supported'
  | 'unknown';

export type BrowserSpeechAvailability =
  | { status: 'checking'; reason: null }
  | { status: 'available'; reason: null }
  | { status: 'unavailable'; reason: BrowserSpeechUnavailableReason };

export const CHECKING_BROWSER_SPEECH_AVAILABILITY: BrowserSpeechAvailability = {
  status: 'checking',
  reason: null,
};

export const AVAILABLE_BROWSER_SPEECH: BrowserSpeechAvailability = {
  status: 'available',
  reason: null,
};

export function unavailableBrowserSpeech(
  reason: BrowserSpeechUnavailableReason
): BrowserSpeechAvailability {
  return { status: 'unavailable', reason };
}

export function getBrowserSpeechRecognition(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function normalizeLang(lang: string): string {
  return lang.trim() || 'en-US';
}

async function isKnownBlockedSpeechBrowser(): Promise<boolean> {
  if (typeof navigator === 'undefined') return false;

  const brave = (navigator as Navigator & {
    brave?: { isBrave?: () => boolean | Promise<boolean> };
  }).brave;

  if (typeof brave?.isBrave !== 'function') return false;

  try {
    return Boolean(await brave.isBrave());
  } catch {
    return true;
  }
}

function isKnownNativeSpeechRuntime(): boolean {
  if (typeof navigator === 'undefined') return false;

  const nav = navigator as Navigator & {
    userAgentData?: { brands?: Array<{ brand: string; version: string }> };
  };
  const brands = nav.userAgentData?.brands ?? [];
  const brandNames = brands.map(({ brand }) => brand.toLowerCase());
  const hasBrand = (name: string) => brandNames.some((brand) => brand.includes(name));

  if (hasBrand('google chrome')) return true;
  if (brandNames.length > 0) return false;

  const userAgent = navigator.userAgent;
  const isSafari =
    /Safari\//.test(userAgent) &&
    !/Chrome\/|Chromium\/|CriOS\/|FxiOS\/|Edg\/|OPR\/|SamsungBrowser\//.test(userAgent);
  const isGoogleChrome =
    /Chrome\/|CriOS\//.test(userAgent) &&
    !/Chromium\/|Edg\/|OPR\/|SamsungBrowser\//.test(userAgent);

  return isSafari || isGoogleChrome;
}

async function getMicrophonePermissionState(): Promise<PermissionState | null> {
  if (typeof navigator === 'undefined') return null;
  const permissions = navigator.permissions;
  if (!permissions?.query) return null;

  try {
    const status = await permissions.query({
      name: 'microphone' as PermissionName,
    });
    return status.state;
  } catch {
    return null;
  }
}

export function mapRecognitionErrorToAvailability(
  error: SpeechRecognitionErrorEvent['error']
): BrowserSpeechAvailability | null {
  switch (error) {
    case 'no-speech':
    case 'aborted':
      return null;
    case 'not-allowed':
      return unavailableBrowserSpeech('microphone-denied');
    case 'audio-capture':
      return unavailableBrowserSpeech('microphone-unavailable');
    case 'network':
      return unavailableBrowserSpeech('network');
    case 'service-not-allowed':
    case 'service-not-available':
      return unavailableBrowserSpeech('service-not-available');
    case 'language-not-supported':
      return unavailableBrowserSpeech('language-not-supported');
    default:
      return unavailableBrowserSpeech('unknown');
  }
}

export async function detectBrowserSpeechAvailability(
  lang = 'en-US'
): Promise<BrowserSpeechAvailability> {
  if (typeof window === 'undefined') {
    return unavailableBrowserSpeech('server-render');
  }

  if (isElectron()) {
    return unavailableBrowserSpeech('electron');
  }

  const SpeechRecognitionAPI = getBrowserSpeechRecognition();
  if (!SpeechRecognitionAPI) {
    return unavailableBrowserSpeech('browser-unavailable');
  }

  if (window.isSecureContext === false) {
    return unavailableBrowserSpeech('insecure-context');
  }

  if (await isKnownBlockedSpeechBrowser()) {
    return unavailableBrowserSpeech('browser-unavailable');
  }

  const microphonePermission = await getMicrophonePermissionState();
  if (microphonePermission === 'denied') {
    return unavailableBrowserSpeech('microphone-denied');
  }

  if (typeof SpeechRecognitionAPI.available === 'function') {
    try {
      const status = await SpeechRecognitionAPI.available({
        langs: [normalizeLang(lang)],
      });
      if (status === 'unavailable') {
        return unavailableBrowserSpeech('browser-unavailable');
      }
      return AVAILABLE_BROWSER_SPEECH;
    } catch {
      return unavailableBrowserSpeech('browser-unavailable');
    }
  }

  if (!isKnownNativeSpeechRuntime()) {
    return unavailableBrowserSpeech('browser-unavailable');
  }

  return AVAILABLE_BROWSER_SPEECH;
}
