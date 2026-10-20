import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationData, CitationMaps } from '../response-tabs/citations';

afterEach(() => cleanup());

const h = React.createElement;

function makeCitationMaps(n: number): CitationMaps {
  const maps: CitationMaps = { citations: {}, sources: { r1: 'c1' }, sourcesOrder: ['r1'], citationsOrder: {} };
  for (let i = 1; i <= n; i += 1) {
    const citation: CitationData = {
      citationId: `c${i}`,
      content: `snippet ${i}`,
      chunkIndex: i,
      recordId: 'r1',
      recordName: 'handbook.pdf',
      connector: 'ONEDRIVE',
      recordType: 'FILE',
      webUrl: 'https://example.com/handbook.pdf',
      mimeType: 'application/pdf',
      extension: 'pdf',
      previewRenderable: true,
      hideWeburl: false,
      citationType: 'vectordb|document',
    };
    maps.citations[`c${i}`] = citation;
    maps.citationsOrder[i] = `c${i}`;
  }
  return maps;
}

function renderMarkers(n: number) {
  const markers = Array.from({ length: n }, (_, i) => `[${i + 1}]`).join('');
  return render(
    h(Theme, null, h(AnswerContent, { content: `Leave policy${markers}.`, citationMaps: makeCitationMaps(n), isStreaming: false })),
  );
}

const circleLabels = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('[aria-haspopup="dialog"]')).map((c) => c.textContent);

describe('InlineCitationGroup overflow', () => {
  it('shows two circles and a +N chip for four same-record citations, all of them after expanding', () => {
    const { container, getByText } = renderMarkers(4);
    expect(circleLabels(container)).toEqual(['1', '2']);

    fireEvent.click(getByText('+2'));

    expect(circleLabels(container)).toEqual(['1', '2', '3', '4']);
    expect(container.textContent).not.toContain('+2');
  });

  it('shows no chip when the group fits', () => {
    const { container } = renderMarkers(2);
    expect(circleLabels(container)).toEqual(['1', '2']);
    expect(container.textContent).not.toMatch(/\+\d/);
  });
});
