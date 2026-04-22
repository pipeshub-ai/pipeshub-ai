/**
 * Unit tests for the chat speech switcher hooks.
 *
 * These confirm that `useChatSpeechRecognition` and `useChatSpeechSynthesis`
 * route to the server-side implementation when the backend reports a
 * configured TTS/STT provider and fall back to the browser Web Speech API
 * otherwise.
 *
 * Written in vitest/jest-compatible style. They become executable the moment
 * a unit-test runner is added to `frontend-new` (only Playwright is
 * currently configured), matching the convention established by
 * `app/(main)/chat/utils/__tests__/parse-download-markers.test.ts`.
 */

import { renderHook } from '@testing-library/react';

import { useChatSpeechRecognition } from '../use-chat-speech-recognition';
import { useChatSpeechSynthesis } from '../use-chat-speech-synthesis';

jest.mock('../use-chat-speech-config', () => ({
  useChatSpeechConfig: jest.fn(),
}));
jest.mock('../use-speech-recognition', () => ({
  useSpeechRecognition: jest.fn(),
}));
jest.mock('../use-server-speech-recognition', () => ({
  useServerSpeechRecognition: jest.fn(),
}));
jest.mock('../use-speech-synthesis', () => ({
  useSpeechSynthesis: jest.fn(),
}));
jest.mock('../use-server-speech-synthesis', () => ({
  useServerSpeechSynthesis: jest.fn(),
}));

import { useChatSpeechConfig } from '../use-chat-speech-config';
import { useServerSpeechRecognition } from '../use-server-speech-recognition';
import { useServerSpeechSynthesis } from '../use-server-speech-synthesis';
import { useSpeechRecognition } from '../use-speech-recognition';
import { useSpeechSynthesis } from '../use-speech-synthesis';

const mockedConfig = useChatSpeechConfig as unknown as jest.Mock;
const mockedBrowserStt = useSpeechRecognition as unknown as jest.Mock;
const mockedServerStt = useServerSpeechRecognition as unknown as jest.Mock;
const mockedBrowserTts = useSpeechSynthesis as unknown as jest.Mock;
const mockedServerTts = useServerSpeechSynthesis as unknown as jest.Mock;

function makeSttResult(label: string) {
  return {
    isListening: false,
    isSupported: true,
    transcript: label,
    interimTranscript: '',
    start: jest.fn(),
    stop: jest.fn(),
    toggle: jest.fn(),
    resetTranscript: jest.fn(),
  };
}

function makeTtsResult(label: string) {
  return {
    isSpeaking: false,
    isSupported: true,
    speak: jest.fn().mockName(`${label}-speak`),
    stop: jest.fn(),
  };
}

describe('useChatSpeechRecognition', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedBrowserStt.mockReturnValue(makeSttResult('browser'));
    mockedServerStt.mockReturnValue(makeSttResult('server'));
  });

  it('picks the server hook when hasStt === true', () => {
    mockedConfig.mockReturnValue({
      hasStt: true,
      hasTts: false,
      tts: null,
      stt: { provider: 'openAI', model: 'whisper-1' },
      isLoading: false,
      error: null,
    });

    const { result } = renderHook(() => useChatSpeechRecognition());
    expect(result.current.usingServerStt).toBe(true);
    expect(result.current.transcript).toBe('server');
  });

  it('picks the browser hook when hasStt === false', () => {
    mockedConfig.mockReturnValue({
      hasStt: false,
      hasTts: false,
      tts: null,
      stt: null,
      isLoading: false,
      error: null,
    });

    const { result } = renderHook(() => useChatSpeechRecognition());
    expect(result.current.usingServerStt).toBe(false);
    expect(result.current.transcript).toBe('browser');
  });

  it('stays on the browser hook while capabilities are still loading', () => {
    mockedConfig.mockReturnValue({
      hasStt: true,
      hasTts: false,
      tts: null,
      stt: null,
      isLoading: true,
      error: null,
    });

    const { result } = renderHook(() => useChatSpeechRecognition());
    expect(result.current.usingServerStt).toBe(false);
    expect(result.current.transcript).toBe('browser');
  });
});

describe('useChatSpeechSynthesis', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedBrowserTts.mockReturnValue(makeTtsResult('browser'));
    mockedServerTts.mockReturnValue(makeTtsResult('server'));
  });

  it('picks the server hook when hasTts === true', () => {
    mockedConfig.mockReturnValue({
      hasStt: false,
      hasTts: true,
      tts: { provider: 'openAI', model: 'tts-1' },
      stt: null,
      isLoading: false,
      error: null,
    });

    const { result } = renderHook(() => useChatSpeechSynthesis());
    expect(result.current.usingServerTts).toBe(true);
  });

  it('picks the browser hook when hasTts === false', () => {
    mockedConfig.mockReturnValue({
      hasStt: false,
      hasTts: false,
      tts: null,
      stt: null,
      isLoading: false,
      error: null,
    });

    const { result } = renderHook(() => useChatSpeechSynthesis());
    expect(result.current.usingServerTts).toBe(false);
  });
});
