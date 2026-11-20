/** The question shows its applied-filter chips unless the response extension turns them off (`showAppliedFilters: false`). */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const response = vi.hoisted(() => ({ showAppliedFilters: undefined as boolean | undefined }));

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: () => ({
        hideTabs: false,
        hideConfidence: false,
        allowRegenerate: true,
        ...(response.showAppliedFilters === undefined ? {} : { showAppliedFilters: response.showAppliedFilters }),
      }),
    },
  };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => <div>{content}</div>,
}));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';
import { AnswerPanelScope } from '../../answer-panel/answer-panel-store';
import { AppliedFilters } from '../../applied-filters';

afterEach(() => {
  cleanup();
  response.showAppliedFilters = undefined;
});

const FILTERS = { apps: [{ id: 'c1', name: 'Engineering handbook', nodeType: 'app', connector: 'KB' }], kb: [] };

const row = () =>
  render(
    <Theme>
      <ChatResponse
        question="What changed?"
        answer="Two pages."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        appliedFilters={FILTERS}
      />
    </Theme>,
  );

describe('Applied-filter chips under the question', () => {
  it('are shown by default', () => {
    row();
    expect(screen.getByText('Engineering handbook')).toBeTruthy();
  });

  it('are not shown when the extension turns them off, and the answer stays', () => {
    response.showAppliedFilters = false;
    row();
    expect(screen.queryByText('Engineering handbook')).toBeNull();
    expect(screen.getByText('Two pages.')).toBeTruthy();
  });
});

describe('only where the chat has no right column', () => {
  const at = (column: boolean) => (
    <Theme>
      <AnswerPanelScope.Provider value={{ column }}>
        <ChatResponse question="What changed?" answer="Two pages." messageId="row-1" citationMessageRowKey="row-1" isLastMessage appliedFilters={FILTERS} />
      </AnswerPanelScope.Provider>
    </Theme>
  );

  it('from 1024 px the right column lists them in its Context tab, so none sit under the question; below it they do', () => {
    const { rerender } = render(at(true));
    expect(screen.queryByText('Engineering handbook')).toBeNull();
    expect(screen.getByText('Two pages.')).toBeTruthy();
    // Crossing the breakpoint switches them.
    rerender(at(false));
    expect(screen.getByText('Engineering handbook')).toBeTruthy();
    rerender(at(true));
    expect(screen.queryByText('Engineering handbook')).toBeNull();
  });
});

describe('the icon of a chip', () => {
  const iconColor = (name: string) =>
    (screen.getByText(name).parentElement?.querySelector('svg') as SVGElement | null)?.style.color;

  it("an agent's own collection, sent as `kb` with the record group type, shows the collection icon", () => {
    render(
      <Theme>
        <AppliedFilters appliedFilters={{ apps: [], kb: [{ id: 'col-1', name: 'Atlas', nodeType: 'recordGroup', connector: 'KB' }] }} />
      </Theme>,
    );
    expect(iconColor('Atlas')).toBe('var(--emerald-11)');
  });

  it('control: the same collection picked as an app, and a record group, keep their own icons', () => {
    render(
      <Theme>
        <AppliedFilters
          appliedFilters={{
            apps: [{ id: 'col-1', name: 'Atlas', nodeType: 'app', connector: 'KB' }],
            kb: [],
            recordGroups: [{ id: 'rg-1', name: 'Atlas space', nodeType: 'recordGroup', connector: 'KB' }],
          }}
        />
      </Theme>,
    );
    expect(iconColor('Atlas')).toBe('var(--emerald-11)');
    expect(iconColor('Atlas space')).toBe('var(--accent-9)');
  });
});
