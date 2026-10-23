import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { MessageRedaction, RedactedCitationApiResponse } from '@/chat/types';

const api = vi.hoisted(() => ({ requestAccess: vi.fn() }));
vi.mock('../../../api', () => ({ ChatApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { DetailsHiddenNote, RedactedAttachments, RedactedSourcesNotice } from '../redacted-sources';
import { parseInlineCitations } from '../answer-content';
import { buildCitationMapsFromApi } from '../response-tabs/citations/utils';

installBrowserShims();
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const redactedCitation = (recordId: string, kind: 'native' | 'connector'): RedactedCitationApiResponse => ({
  citationId: `c-${recordId}`,
  redacted: true,
  reason: 'no_access',
  requestAccess: { recordId, kind, endpoint: '/api/v1/access-requests' },
});
const partial: MessageRedaction = { redacted: false, partial: true, reason: 'no_access' };

describe('shared chat redaction UI', () => {
  it('keeps redacted citations out of the citation maps and lists one source per record', () => {
    const maps = buildCitationMapsFromApi(
      [redactedCitation('r1', 'native'), redactedCitation('r1', 'native'), redactedCitation('r2', 'connector')],
      partial,
    );
    expect(Object.keys(maps.citations)).toEqual([]);
    expect(maps.redaction?.sources.map((s) => s.recordId)).toEqual(['r1', 'r2']);
    expect(buildCitationMapsFromApi([]).redaction).toBeUndefined();
  });

  it('renders the redaction token as a hidden chip and keeps the text around it', () => {
    const nodes = parseInlineCitations('Before. {{redacted}} After.');
    const { container } = renderInTheme(<p>{nodes}</p>);
    expect(screen.getByTestId('redacted-inline')).toBeTruthy();
    expect(container.textContent).toContain('Before.');
    expect(container.textContent).toContain('After.');
    expect(container.textContent).not.toContain('{{redacted}}');
  });

  it('shows the partial marker and requests access for a native source', async () => {
    api.requestAccess.mockResolvedValue({});
    renderInTheme(<RedactedSourcesNotice conversationId="conv-1" redaction={{ ...partial, sources: [redactedCitation('r1', 'native').requestAccess] }} />);
    expect(screen.getByText(/partially based on sources you can't access/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /request access/i }));
    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith('r1', { conversationId: 'conv-1' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /access requested/i }).hasAttribute('disabled')).toBe(true));
  });

  it('uses the all-hidden wording when the whole answer is redacted', () => {
    renderInTheme(<RedactedSourcesNotice conversationId="conv-1" redaction={{ redacted: true, partial: false, sources: [] }} />);
    expect(screen.getByText(/^This answer is based on sources you can't access/i)).toBeTruthy();
  });

  it('shows a connector source’s page only after the request, from the reply, never from the redacted item', async () => {
    api.requestAccess.mockResolvedValue({ sourceUrl: 'https://wiki.example/p/1' });
    const item = redactedCitation('r2', 'connector');
    expect(JSON.stringify(item)).not.toContain('sourceUrl');
    renderInTheme(<RedactedSourcesNotice conversationId="conv-1" redaction={{ ...partial, sources: [item.requestAccess] }} />);
    expect(screen.queryByRole('link', { name: /open in source/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /request access/i }));
    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith('r2', { conversationId: 'conv-1' }));
    const link = await screen.findByRole('link', { name: /open in source/i });
    expect(link.getAttribute('href')).toBe('https://wiki.example/p/1');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('shows an error when the request fails', async () => {
    api.requestAccess.mockRejectedValue(new Error('x'));
    renderInTheme(<RedactedSourcesNotice conversationId="conv-1" redaction={{ ...partial, sources: [redactedCitation('r1', 'native').requestAccess] }} />);
    fireEvent.click(screen.getByRole('button', { name: /request access/i }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/couldn't send/i));
  });

  it('keeps the details-hidden flag in the maps and shows the neutral note', () => {
    expect(buildCitationMapsFromApi([], undefined, true).detailsHidden).toBe(true);
    expect(buildCitationMapsFromApi([]).detailsHidden).toBeUndefined();
    renderInTheme(<DetailsHiddenNote />);
    expect(screen.getByTestId('details-hidden-note').textContent).toMatch(/hidden for shared viewers/i);
  });

  it('gives a redacted attachment the same Request access row', async () => {
    api.requestAccess.mockResolvedValue({});
    renderInTheme(<RedactedAttachments conversationId="conv-1" sources={[redactedCitation('att1', 'native').requestAccess]} />);
    fireEvent.click(screen.getByRole('button', { name: /request access/i }));
    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith('att1', { conversationId: 'conv-1' }));
  });

  it('words a collection record as a request for the whole collection', () => {
    renderInTheme(
      <RedactedAttachments conversationId="conv-1" sources={[{ recordId: 'kb1', kind: 'native', collection: true, endpoint: '/x' }]} />,
    );
    expect(screen.getByText('Request access to the collection that contains this source')).toBeTruthy();
  });
});
