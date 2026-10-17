import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import type { RecordDetailsResponse } from '@/app/(main)/knowledge-base/types';
import { FileDetailsTab } from '../file-details-tab';

const WEB_URL = 'https://jira.acme-demo.example/browse/INC-2031';

function sourceLink(container: HTMLElement): Element | null {
  return container.querySelector(`a[href="${WEB_URL}"]`);
}

function details(hideWeburl: boolean): RecordDetailsResponse {
  return {
    record: {
      id: 'rec-1',
      recordName: 'INC-2031: Synchronised billing retries',
      recordType: 'TICKET',
      origin: 'CONNECTOR',
      webUrl: WEB_URL,
      hideWeburl,
    },
    knowledgeBase: null,
    folder: null,
    metadata: { departments: [], categories: [], subcategories1: [], subcategories2: [], subcategories3: [], topics: [], languages: [] },
    permissions: [],
  } as unknown as RecordDetailsResponse;
}

afterEach(cleanup);

describe('FileDetailsTab web URL', () => {
  it('shows the source link for a connector record', () => {
    const { container } = render(<Theme><FileDetailsTab recordDetails={details(false)} /></Theme>);
    expect(sourceLink(container)).not.toBeNull();
  });

  it('hides it when the record says its link cannot be opened', () => {
    const { container } = render(<Theme><FileDetailsTab recordDetails={details(true)} /></Theme>);
    expect(sourceLink(container)).toBeNull();
  });
});
