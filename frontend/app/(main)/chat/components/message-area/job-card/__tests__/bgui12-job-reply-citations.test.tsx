/**
 * BGUI-12 (BGUI11-FU-2): the job panel's "How it came out" drew the
 * engine's citations as bare numbers (1 … 12 on an IPL report job) because the job's timeline did not carry its answer's
 * citations. Now each chip names its source and opens the chat answer's own popover, from the citations the timeline
 * carries; an older job's links still name their site; a number with no source behind it is not drawn.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { RunAnswerCitation } from '../../../../types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const { JobReply } = await import('../job-reply');
const { citedAnswer } = await import('../../../../utils/cited-answer');
const { buildCitationMapsFromStreaming } = await import('../../response-tabs/citations/utils');
const citedReply = (raw: string, citations: RunAnswerCitation[]) =>
  citedAnswer(raw, buildCitationMapsFromStreaming(citations as never));
const { jobReplyOf } = await import('../job-report-state');
const { InlineCitationPopoverHost } = await import('../../response-tabs/citations/inline-citation-popover-host');
const { useInlineCitationPopoverStore } = await import('../../response-tabs/citations/citation-popover-store');
const { isCitationPopoverKeyStillValid } = await import('../../response-tabs/citations/citation-popover-control');

const h = React.createElement;
const FINAL = 'https://www.iplt20.com/news/article/tata-ipl-2026-final-rcb-v-gt-match-report';
const AWARDS = 'https://www.rajasthanroyals.com/latest-news/vaibhav-sooryavanshi-ipl-2026-awards';

const web = (chunkIndex: number, url: string, title: string, content = `From ${title}`): RunAnswerCitation =>
  ({
    content,
    chunkIndex,
    citationType: 'web|url',
    metadata: { recordId: url, recordName: title, webUrl: url, origin: 'WEB_SEARCH', connector: 'WEB', recordType: 'WEBPAGE', mimeType: 'text/html' },
  }) as RunAnswerCitation;

const record = (chunkIndex: number): RunAnswerCitation =>
  ({
    content: 'Q3 revenue grew 12%.',
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: { recordId: 'rec-q3', recordName: 'Q3 board pack.pdf', connector: 'KB', origin: 'UPLOAD', recordType: 'FILE', mimeType: 'application/pdf', extension: 'pdf', pageNum: [4] },
  }) as RunAnswerCitation;

/** A recorded IPL reply as its timeline keeps it: numbered links with text fragments, as the answer pipeline wrote them. */
const IPL = [
  `RCB retained the title by beating Gujarat Titans [1](${FINAL}#:~:text=Royal%20Challengers) and Sooryavanshi`,
  `took the Orange Cap [2](${AWARDS}#:~:text=Strike%20Rate) [3](${AWARDS}#:~:text=He%20also).`,
].join(' ');

const view = (node: React.ReactElement) => render(h(Theme, null, node, h(InlineCitationPopoverHost)));

afterEach(() => {
  cleanup();
  act(() => useInlineCitationPopoverStore.getState().close());
});

describe('BGUI-12: a job reply\'s citation chips name their source', () => {
  it('BGUI12-V1: each chip shows its stored source\'s title and number, never a bare number', () => {
    view(
      h(JobReply, {
        text: IPL,
        citations: [web(1, FINAL, 'TATA IPL 2026, Final: RCB v GT'), web(2, AWARDS, 'Sooryavanshi awards'), web(3, AWARDS, 'Sooryavanshi awards')],
      }),
    );
    const reply = screen.getByTestId('job-reply');
    expect(reply.textContent).toMatch(/Titans\s*TATA IPL 2026, Final: RC…\s*1/);
    expect(reply.textContent).toContain('Sooryavanshi awards');
    expect(reply.textContent).not.toContain('](http');
    // The same page twice is one group, as the chat's answer draws it.
    expect(reply.querySelectorAll('.citation-number-circle')).toHaveLength(3);
  });

  it('BGUI12-V2: a chip opens the answer\'s popover with the passage and a link to the page', async () => {
    view(h(JobReply, { text: IPL, citations: [web(1, FINAL, 'TATA IPL 2026 Final', 'RCB chased 156 with 12 balls left.')] }));
    const circle = screen.getByTestId('job-reply').querySelector('.citation-number-circle') as HTMLElement;
    fireEvent.click(circle.closest('a, button') as HTMLElement);
    const popover = await screen.findByText('RCB chased 156 with 12 balls left.');
    const card = popover.closest('[data-radix-popper-content-wrapper]') as HTMLElement;
    expect(within(card).getByRole('link').getAttribute('href')).toBe(FINAL);
  });

  it('BGUI12-V10: the popover is scoped outside the message list, which keeps it open, and closes with the reply', async () => {
    const { unmount } = view(h(JobReply, { text: IPL, citations: [web(1, FINAL, 'Final', 'RCB chased 156.')] }));
    fireEvent.click(screen.getByTestId('job-reply').querySelector('.citation-number-circle')?.closest('a, button') as HTMLElement);
    await screen.findByText('RCB chased 156.');
    const key = useInlineCitationPopoverStore.getState().activeKey;
    // The list clears a key no row of it holds (its streaming clean-up): the reply's key is not one it may clear.
    expect(isCitationPopoverKeyStillValid(key, ['msg-1', 'msg-2'])).toBe(true);
    expect(isCitationPopoverKeyStillValid('orphan::c1@0-1', ['msg-1'])).toBe(false);
    unmount();
    expect(useInlineCitationPopoverStore.getState().activeKey).toBeNull();
  });

  it('BGUI12-V3: a record citation is the chat\'s own record chip and popover, Preview included', async () => {
    view(h(JobReply, { text: 'Revenue grew [1](/record/rec-q3/preview#blockIndex=2).', citations: [record(1)] }));
    const reply = screen.getByTestId('job-reply');
    expect(reply.textContent).toContain('Q3 board pack');
    fireEvent.click(reply.querySelector('.citation-number-circle')?.closest('a, button') as HTMLElement);
    expect(await screen.findByText('Q3 revenue grew 12%.')).toBeTruthy();
    expect(screen.getByRole('button', { name: /preview/i })).toBeTruthy();
  });

  it('BGUI12-V4: an older job with no stored citations still names each link\'s site; nothing looks broken', () => {
    view(h(JobReply, { text: `${IPL} See also [4] and [9].` }));
    const reply = screen.getByTestId('job-reply');
    // Named by its site, as the chat names a web page it has no title for (the chip drops a name's last `.part`).
    expect(reply.textContent).toMatch(/Titans\s*iplt20\s*1/);
    expect(reply.textContent).toMatch(/rajasthanroyals\s*2\s*2|rajasthanroyals\s*22/);
    // Footnotes with no source behind them are not drawn at all.
    expect(reply.textContent).not.toMatch(/4|9/);
    expect(reply.textContent).toMatch(/See also\s+and\s*\./);
  });

  it('BGUI12-V5: a withheld source keeps its number as the chat\'s unavailable chip', () => {
    view(h(JobReply, { text: `Won [1](${FINAL}).`, citations: [{ chunkIndex: 1, unavailable: true }] }));
    expect(screen.getByTestId('citation-unavailable')).toBeTruthy();
  });
});

describe('BGUI-12: which source a number points at', () => {
  it('BGUI12-V6: the engine\'s own numbered link to an unread page never takes a stored source\'s number', () => {
    const { text, citationMaps } = citedReply(`A [1](${FINAL}) and B [1](https://example.org/other).`, [web(1, FINAL, 'Final')]);
    expect(text).toBe('A [1] and B [2].');
    const at = (n: number) => citationMaps.citations[citationMaps.citationsOrder[n]];
    expect(at(1).recordName).toBe('Final');
    expect(at(2)).toMatchObject({ recordName: 'example.org', webUrl: 'https://example.org/other' });
  });

  it('BGUI12-V7: footnotes resolve through the reply\'s own sources list, numbered as the list is', () => {
    const raw = [
      'RCB won [2] and GT were runners-up [1].',
      '',
      'Sources:',
      '1. GT season review - https://example.com/gt',
      `2. [IPL final report](${FINAL})`,
      '3. https://example.net/unused',
    ].join('\n');
    const { text, citationMaps } = citedReply(raw, []);
    expect(text).toBe('RCB won [2] and GT were runners-up [1].\n\n[3]');
    const at = (n: number) => citationMaps.citations[citationMaps.citationsOrder[n]];
    expect(at(1)).toMatchObject({ recordName: 'GT season review', webUrl: 'https://example.com/gt' });
    expect(at(2)).toMatchObject({ recordName: 'IPL final report', webUrl: FINAL });
    expect(at(3)).toMatchObject({ recordName: 'example.net' });
  });

  it('BGUI12-V8: code is never read for citations, and ordinary links stay links', () => {
    const raw = 'Use `arr[1]` here [docs](https://example.com/docs).\n\n```\nx = y[2]\n```';
    const { text } = citedReply(raw, []);
    expect(text).toBe(raw);
  });
});

describe('BGUI-12: the panel reads the reply and its citations together', () => {
  it('BGUI12-V9: the final text comes with its answer\'s citations; the engine\'s finished line with none', () => {
    const citations = [web(1, FINAL, 'Final')];
    const timeline = { parts: [{ type: 'text', content: IPL, isFinal: true }], answer: { citations } } as never;
    expect(jobReplyOf(timeline, [])).toMatchObject({ text: IPL, citations });
    const finished = [{ kind: 'finished', text: 'Done.', data: { success: true } }] as never;
    expect(jobReplyOf({ parts: [], answer: { citations } } as never, finished)).toMatchObject({ text: 'Done.', citations: [] });
    // An older server sends no `answer`: the reply still draws, its links named by their sites.
    expect(jobReplyOf({ parts: [{ type: 'text', content: IPL, isFinal: true }] } as never, [])).toMatchObject({ citations: [] });
  });
});
