import React from 'react';
import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { UserMessage } from '../user-message';
import { useCommandStore } from '@/lib/store/command-store';
import type { AppliedFilters, AttachmentRef } from '../../../types';

// `UserMessage` imports `@/knowledge-base/api` for the attachment-preview
// click handler, which transitively pulls in the shared axios instance and
// hydrates the auth store from `window.localStorage` at module load. jsdom's
// storage stubbing is unreliable for that eager side effect, so replace the
// API module entirely — these tests never click an attachment through to a
// real network call.
vi.mock('@/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    streamRecord: vi.fn(),
  },
}));

beforeAll(() => {
  window.matchMedia = window.matchMedia ?? ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ handlers: {} });
});

const h = React.createElement;

function renderUserMessage(props: Partial<React.ComponentProps<typeof UserMessage>> = {}) {
  return render(
    h(Theme, null, h(UserMessage, { question: 'What is the refund policy?', ...props })),
  );
}

describe('UserMessage', () => {
  it('renders the question text', () => {
    renderUserMessage({ question: 'How do I reset my password?' });
    expect(screen.getByText('How do I reset my password?')).toBeTruthy();
  });

  it('does not truncate short questions or show a "Show more" toggle', () => {
    renderUserMessage({ question: 'Short question' });
    expect(screen.queryByText('Show more')).toBeNull();
  });

  it('truncates long questions behind a "Show more" toggle, then expands on click', () => {
    const longQuestion = 'A'.repeat(300);
    renderUserMessage({ question: longQuestion });

    expect(screen.getByText('Show more')).toBeTruthy();
    expect(screen.queryByText(longQuestion)).toBeNull();

    fireEvent.click(screen.getByText('Show more'));

    expect(screen.getByText(longQuestion)).toBeTruthy();
    expect(screen.getByText('Show less')).toBeTruthy();
  });

  it('renders one chip per attachment, labeled with the record name', () => {
    const attachments: AttachmentRef[] = [
      { recordId: 'r-1', recordName: 'invoice.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v-1' },
      { recordId: 'r-2', recordName: 'photo.png', mimeType: 'image/png', extension: 'png', virtualRecordId: 'v-2' },
    ];
    renderUserMessage({ attachments });

    expect(screen.getByText('invoice.pdf')).toBeTruthy();
    expect(screen.getByText('photo.png')).toBeTruthy();
  });

  it('renders applied filter chips for scoped apps/collections', () => {
    const appliedFilters: AppliedFilters = {
      apps: [{ id: 'a-1', name: 'Slack', nodeType: 'app', connector: 'SLACK' }],
      kb: [{ id: 'kb-1', name: 'Engineering Docs', nodeType: 'kb', connector: '' }],
    };
    renderUserMessage({ appliedFilters });

    expect(screen.getByText('Slack')).toBeTruthy();
    expect(screen.getByText('Engineering Docs')).toBeTruthy();
  });

  it('dispatches showEditQuery with the message id and question text when the edit icon is clicked', () => {
    const handler = vi.fn();
    useCommandStore.getState().register('showEditQuery', handler);

    renderUserMessage({ question: 'Edit me', messageId: 'msg-1', isStreaming: false });

    fireEvent.click(screen.getByLabelText('Edit message'));

    expect(handler).toHaveBeenCalledWith({ messageId: 'msg-1', text: 'Edit me' });
  });

  it('does not render an edit affordance while the paired answer is streaming', () => {
    renderUserMessage({ messageId: 'msg-1', isStreaming: true });
    expect(screen.queryByLabelText('Edit message')).toBeNull();
  });

  it('does not render an edit affordance without a messageId', () => {
    renderUserMessage({ messageId: undefined, isStreaming: false });
    expect(screen.queryByLabelText('Edit message')).toBeNull();
  });
});
