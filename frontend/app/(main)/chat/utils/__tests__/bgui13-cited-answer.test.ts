/**
 * BGUI-13 (BGUI12-FU-1, the owner: "fix the main chat answer's citation chips too"): the chat's own answer drew an
 * engine's `[1](https://…)` links, repeated with no stored citation, as bare chips that opened nothing, and dropped
 * their URLs. One rule (`citedAnswer`) now reads every answer: the chat's and a job's reply.
 */
import { describe, expect, it } from 'vitest';
import type { CitationMaps, StreamingCitationData } from '../../components/message-area/response-tabs/citations/types';
import { buildCitationMapsFromStreaming, emptyCitationMaps } from '../../components/message-area/response-tabs/citations/utils';
import { citedAnswer } from '../cited-answer';
import { processMarkdownContent } from '../process-markdown-content';

const FINAL = 'https://www.iplt20.com/news/article/tata-ipl-2026-final-rcb-v-gt-match-report';
const CRICINFO = 'https://www.espncricinfo.com/series/ipl-2026/final';

const web = (chunkIndex: number, url: string, title: string): StreamingCitationData =>
  ({
    content: `From ${title}`,
    chunkIndex,
    citationType: 'web|url',
    metadata: { recordId: url, recordName: title, webUrl: url, origin: 'WEB_SEARCH', connector: 'WEB', recordType: 'WEBPAGE', mimeType: 'text/html' },
  }) as unknown as StreamingCitationData;

const record = (chunkIndex: number, recordId = 'rec-q3'): StreamingCitationData =>
  ({
    content: 'Q3 revenue grew 12%.',
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: { recordId, recordName: 'Q3 board pack.pdf', connector: 'KB', origin: 'UPLOAD', recordType: 'FILE', mimeType: 'application/pdf', extension: 'pdf' },
  }) as unknown as StreamingCitationData;

const maps = (...citations: StreamingCitationData[]): CitationMaps => buildCitationMapsFromStreaming(citations);
const at = (m: CitationMaps, n: number) => m.citations[m.citationsOrder[n]];

/** The engine lane's parent answer: the engine's own links, repeated, with `citations: []`. */
const ENGINE_ANSWER = `RCB won the final [1](${FINAL}#:~:text=Royal%20Challengers), by 5 wickets [2](${CRICINFO}).`;

describe('BGUI-13: a normal RAG answer reads as before', () => {
  it('BGUI13-U1: record and web citations keep their numbers, and the maps are the stored ones', () => {
    const stored = maps(record(1), web(2, FINAL, 'IPL final'));
    const raw = `Revenue grew [1](https://app.test/record/rec-q3/preview#blockIndex=2) and RCB won [2](${FINAL}#:~:text=RCB).`;
    const out = citedAnswer(raw, stored);
    expect(out.text).toBe(processMarkdownContent(raw));
    expect(out.text).toBe('Revenue grew [1] and RCB won [2].');
    expect(out.citationMaps).toBe(stored);
  });

  it('BGUI13-U2: bare numbers a stored citation covers stay; double brackets read as before', () => {
    const stored = maps(record(1), record(2, 'rec-b'));
    expect(citedAnswer('A [1] and B [[2]].', stored).text).toBe('A [1] and B [[2]].');
  });

  it('BGUI13-U3: a sources list no footnote points at is the answer\'s own content, left as written', () => {
    const raw = 'Answer.\n\nReferences:\n1. [IPL](https://www.iplt20.com/)';
    expect(citedAnswer(raw, emptyCitationMaps()).text).toBe(raw);
  });

  it('BGUI13-U4: a withheld source of a shared chat keeps its number', () => {
    const stored = buildCitationMapsFromStreaming([{ chunkIndex: 1, unavailable: true } as StreamingCitationData]);
    const out = citedAnswer(`Won [1](${FINAL}).`, stored);
    expect(out.text).toBe('Won [1].');
    expect(out.citationMaps).toBe(stored);
  });
});

describe('BGUI-13: the chat answer\'s own links get a source', () => {
  it('BGUI13-U5: an engine\'s numbered links, no stored citation: each is a web source on its own URL, by site', () => {
    const out = citedAnswer(ENGINE_ANSWER, emptyCitationMaps());
    expect(out.text).toBe('RCB won the final [1], by 5 wickets [2].');
    // The URL keeps its text fragment as written: the comparison ignores it, the link does not lose it.
    expect(at(out.citationMaps, 1)).toMatchObject({ recordName: 'iplt20.com', webUrl: `${FINAL}#:~:text=Royal%20Challengers` });
    expect(at(out.citationMaps, 2)).toMatchObject({ recordName: 'espncricinfo.com', webUrl: CRICINFO });
    expect(out.citationMaps.sourcesOrder).toHaveLength(2);
  });

  it('BGUI13-U6: mixed record and web: the engine\'s [1] to another page never takes the record\'s number', () => {
    const out = citedAnswer(`Revenue [1](/record/rec-q3/preview#blockIndex=0); IPL [1](${FINAL}).`, maps(record(1)));
    expect(out.text).toBe('Revenue [1]; IPL [2].');
    expect(at(out.citationMaps, 1).recordName).toBe('Q3 board pack.pdf');
    expect(at(out.citationMaps, 2).webUrl).toBe(FINAL);
  });

  it('BGUI13-U7: a link to a stored page joins its citation, fragment or not', () => {
    const out = citedAnswer(`A [1](${FINAL}#:~:text=a) b [3](${FINAL}/).`, maps(web(1, `${FINAL}#:~:text=b`, 'Final')));
    expect(out.text).toBe('A [1] b [1].');
  });

  it('BGUI13-U8: once settled, a number with no source is dropped, a record link with none keeps its words', () => {
    const out = citedAnswer('See [4], and [Leave Policy](/record/abc/preview) [5](/record/abc/preview).', emptyCitationMaps());
    expect(out.text).toBe('See, and Leave Policy.');
  });
});

describe('BGUI-13: while the answer streams', () => {
  it('BGUI13-U9: a numbered link still being written is held back, not shown as a raw URL', () => {
    const out = citedAnswer('RCB won [1](https://www.iplt20.com/news/art', emptyCitationMaps(), { streaming: true });
    expect(out.text).toBe('RCB won');
    expect(out.text).not.toContain('http');
  });

  it('BGUI13-U10: a number whose citation has not arrived keeps its place; refN keeps provisional numbering', () => {
    const out = citedAnswer('A [3] and [source](ref7) and [2](/record/r2/preview).', emptyCitationMaps(), { streaming: true });
    expect(out.text).toBe('A [3] and [1] and [2].');
  });

  it('BGUI13-U11: chips do not jump: a link\'s number while streaming is its number once settled', () => {
    const half = ENGINE_ANSWER.slice(0, ENGINE_ANSWER.indexOf(', by'));
    const streamed = citedAnswer(half, emptyCitationMaps(), { streaming: true });
    const settled = citedAnswer(ENGINE_ANSWER, emptyCitationMaps());
    expect(at(streamed.citationMaps, 1).webUrl).toBe(at(settled.citationMaps, 1).webUrl);
    expect(settled.text.startsWith(streamed.text)).toBe(true);
  });

  it('BGUI13-U12: the same sources on the next flush reuse the maps, so settled chips are not redrawn', () => {
    const base = emptyCitationMaps();
    const first = citedAnswer(`A [1](${FINAL}) b`, base, { streaming: true });
    const second = citedAnswer(`A [1](${FINAL}) b c d`, base, { streaming: true, previous: first });
    expect(second.citationMaps).toBe(first.citationMaps);
    const third = citedAnswer(`A [1](${FINAL}) b [2](${CRICINFO})`, base, { streaming: true, previous: second });
    expect(third.citationMaps).not.toBe(first.citationMaps);
    // New stored citations arriving are new maps, whatever the text adds.
    const fourth = citedAnswer(`A [1](${FINAL}) b`, maps(record(5)), { streaming: true, previous: first });
    expect(fourth.citationMaps).not.toBe(first.citationMaps);
  });
});
