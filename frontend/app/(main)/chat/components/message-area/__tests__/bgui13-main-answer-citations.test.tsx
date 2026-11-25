/**
 * BGUI-13 (BGUI12-FU-1): the chat's own answer, through `ChatResponse` and the real `AnswerContent`. On the engine
 * lane the parent's answer repeats the engine's `[1](https://…)` links with `citations: []`; its chips drew bare
 * numbers that opened nothing. Now each names its page, opens the answer's popover with a link to it, and the
 * Sources and Citation tabs count what the chips show. A normal RAG answer reads exactly as before.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { StreamingCitationData } from '../response-tabs/citations/types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

const { ChatResponse } = await import('../chat-response');
const { InlineCitationPopoverHost } = await import('../response-tabs/citations/inline-citation-popover-host');
const { useInlineCitationPopoverStore } = await import('../response-tabs/citations/citation-popover-store');
const { buildCitationMapsFromStreaming, emptyCitationMaps } = await import('../response-tabs/citations/utils');

const FINAL = 'https://www.iplt20.com/news/article/tata-ipl-2026-final-rcb-v-gt-match-report';
const CRICINFO = 'https://www.espncricinfo.com/series/ipl-2026/final';
const ENGINE_ANSWER = `RCB won the final [1](${FINAL}#:~:text=Royal%20Challengers), by 5 wickets [2](${CRICINFO}).`;

const record = (chunkIndex: number): StreamingCitationData =>
  ({
    content: 'Q3 revenue grew 12%.',
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: { recordId: 'rec-q3', recordName: 'Q3 board pack.pdf', connector: 'KB', origin: 'UPLOAD', recordType: 'FILE', mimeType: 'application/pdf', extension: 'pdf', pageNum: [4] },
  }) as unknown as StreamingCitationData;

const tab = (name: RegExp) => screen.getByRole('tab', { name });

beforeEach(() => installBrowserShims());
afterEach(() => {
  cleanup();
  act(() => useInlineCitationPopoverStore.getState().close());
});

describe('BGUI-13: the chat answer\'s citation chips', () => {
  it('BGUI13-V1: an engine\'s repeated links name their pages, and the tabs count them', () => {
    renderInTheme(
      <>
        <ChatResponse question="Who won IPL 2026?" answer={ENGINE_ANSWER} citationMaps={emptyCitationMaps()} />
        <InlineCitationPopoverHost />
      </>,
    );
    const body = document.body.textContent ?? '';
    expect(body).toMatch(/final,\s*iplt20\s*1/);
    expect(body).toMatch(/wickets\.\s*espncricinfo\s*2/);
    expect(body).not.toContain('](http');
    expect(tab(/sources/i).textContent).toContain('2');
    expect(tab(/citation/i).textContent).toContain('2');
  });

  it('BGUI13-V2: a chip opens the answer\'s popover with a link to the page, its fragment kept', async () => {
    renderInTheme(
      <>
        <ChatResponse question="Who won IPL 2026?" answer={ENGINE_ANSWER} citationMaps={emptyCitationMaps()} />
        <InlineCitationPopoverHost />
      </>,
    );
    const circle = document.querySelector('.citation-number-circle') as HTMLElement;
    fireEvent.click(circle.closest('a, button') as HTMLElement);
    const card = (await screen.findAllByRole('link')).find((a) => a.getAttribute('href')?.startsWith(FINAL));
    expect(card?.getAttribute('href')).toBe(`${FINAL}#:~:text=Royal%20Challengers`);
  });

  it('BGUI13-V3: a RAG answer\'s record chip, popover and counts are as before', async () => {
    const maps = buildCitationMapsFromStreaming([record(1)]);
    renderInTheme(
      <>
        <ChatResponse question="Q3?" answer="Revenue grew [1](/record/rec-q3/preview#blockIndex=2)." citationMaps={maps} />
        <InlineCitationPopoverHost />
      </>,
    );
    expect(document.body.textContent).toContain('Q3 board pack');
    expect(tab(/sources/i).textContent).toContain('1');
    fireEvent.click(document.querySelector('.citation-number-circle')?.closest('a, button') as HTMLElement);
    const passage = await screen.findByText('Q3 revenue grew 12%.');
    expect(within(passage.closest('[data-radix-popper-content-wrapper]') as HTMLElement).getByRole('button', { name: /preview/i })).toBeTruthy();
  });

  it('BGUI13-V4: a number with no source behind it is not drawn once the answer is settled', () => {
    renderInTheme(<ChatResponse question="Q" answer="As noted [7], done." citationMaps={emptyCitationMaps()} />);
    expect(document.querySelector('.citation-number-circle')).toBeNull();
    expect(document.body.textContent).toContain('As noted, done.');
  });

  it('BGUI13-V5: while streaming, a link still being written shows no raw URL', () => {
    renderInTheme(
      <ChatResponse question="Q" answer="" isStreaming streamingContent="RCB won [1](https://www.iplt20.com/news/ar" />,
    );
    expect(document.body.textContent).toContain('RCB won');
    expect(document.body.textContent).not.toContain('iplt20.com/news');
  });
});
