import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationData, CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

// No JSX here — see the same note in agent-activity.test.tsx.
const h = React.createElement;

function makeCitationMaps(): CitationMaps {
  const citation: CitationData = {
    citationId: 'c1',
    content: 'snippet text',
    chunkIndex: 1,
    recordId: 'r1',
    recordName: 'report.pdf',
    connector: 'ONEDRIVE',
    recordType: 'FILE',
    webUrl: 'https://example.com/report.pdf',
    mimeType: 'application/pdf',
    extension: 'pdf',
    previewRenderable: true,
    hideWeburl: false,
    citationType: 'vectordb|document',
  };
  return {
    citations: { c1: citation },
    sources: { r1: 'c1' },
    sourcesOrder: ['r1'],
    citationsOrder: { 1: 'c1' },
  };
}

// Table + links + citations — the exact combination that used to flicker.
const FIXTURE = [
  'Here is a summary with a citation[1] and a [link](https://example.com/x).',
  '',
  '| Name | Role | Link |',
  '| --- | --- | --- |',
  '| Alice | Engineer | [profile](https://example.com/alice) |',
  '| Bob | Manager | [profile](https://example.com/bob) |',
].join('\n');

function renderAnswer(isStreaming: boolean) {
  return render(
    h(
      Theme,
      null,
      h(AnswerContent, {
        content: FIXTURE,
        citationMaps: makeCitationMaps(),
        isStreaming,
      }),
    ),
  );
}

describe('AnswerContent — streaming block-split vs single-instance render equivalence', () => {
  // Guards against splitting artifacts: whatever `splitMarkdownBlocks` does
  // while streaming must produce the exact same visible result as the
  // single-`ReactMarkdown`-instance path used for the persisted message.
  it('renders identical textContent whether block-split (streaming) or not', () => {
    const streamed = renderAnswer(true);
    const streamedText = streamed.container.textContent;
    cleanup();

    const single = renderAnswer(false);
    const singleText = single.container.textContent;

    expect(streamedText).toBeTruthy();
    expect(streamedText).toBe(singleText);
  });

  it('renders the same table/td counts in both modes', () => {
    const streamed = renderAnswer(true);
    const streamedTableCount = streamed.container.querySelectorAll('table').length;
    const streamedTdCount = streamed.container.querySelectorAll('td').length;
    cleanup();

    const single = renderAnswer(false);
    const singleTableCount = single.container.querySelectorAll('table').length;
    const singleTdCount = single.container.querySelectorAll('td').length;

    expect(streamedTableCount).toBe(1);
    expect(streamedTableCount).toBe(singleTableCount);
    expect(streamedTdCount).toBe(singleTdCount);
    expect(streamedTdCount).toBeGreaterThan(0);
  });

  it('renders the same number of links in both modes', () => {
    const streamed = renderAnswer(true);
    const streamedLinkCount = streamed.container.querySelectorAll('a').length;
    cleanup();

    const single = renderAnswer(false);
    const singleLinkCount = single.container.querySelectorAll('a').length;

    expect(streamedLinkCount).toBe(singleLinkCount);
    expect(streamedLinkCount).toBeGreaterThan(0);
  });
});
