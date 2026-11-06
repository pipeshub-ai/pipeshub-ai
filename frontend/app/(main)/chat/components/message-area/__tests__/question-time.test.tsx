/** The time under a question: a question from before today names the day too. */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return { ...real, chatSlots: { ...real.chatSlots, useResponseExtension: () => ({ hideTabs: false, hideConfidence: false, allowRegenerate: true }) } };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({ useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }) }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../answer-content', () => ({ AnswerContent: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';

afterEach(cleanup);
const at = (iso: string) => {
  render(<Theme><ChatResponse question="What changed?" answer="Two pages." messageId="r" citationMessageRowKey="r" isLastMessage createdAt={iso} /></Theme>);
  return screen.queryByTestId('question-time')?.textContent ?? null;
};
describe('the time under the question, before today', () => {
  it('names the day too', () => {
    const d = new Date(); d.setDate(d.getDate() - 1); d.setHours(13, 40, 0, 0);
    expect(at(d.toISOString())).toBe(d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }));
  });
  it('draws nothing for a time that cannot be read, without a crash', () => {
    expect(at('not a date')).toBe('');
  });
});
