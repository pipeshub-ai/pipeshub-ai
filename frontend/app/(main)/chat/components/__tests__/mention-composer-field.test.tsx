import React, { useState } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import { MENTION_PAD_CLOSE, type MentionNode } from '@/chat/utils/node-mentions';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn(), getNodeChildren: vi.fn(), searchNodes: vi.fn() }));

vi.mock('@/chat/api', () => ({
  ChatApi: { listCollectionsForChat: (...args: unknown[]) => api.listCollectionsForChat(...args) },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: {
    getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args),
    searchNodes: (...args: unknown[]) => api.searchNodes(...args),
  },
}));

import { MentionComposerField } from '../mention-composer-field';

const report = { id: 'q3', name: 'Q3 Report', nodeType: 'record', hasChildren: false, connector: 'KB' };

function Composer() {
  const [value, setValue] = useState('');
  const [mentions, setMentions] = useState<MentionNode[]>([]);
  const [selection, setSelection] = useState<ChatKnowledgeFilters>({ apps: [], kb: [] });
  return (
    <Theme>
      <MentionComposerField
        value={value}
        mentions={mentions}
        onChange={setValue}
        onMentionsChange={setMentions}
        selection={selection}
        onSelectionChange={setSelection}
        aria-label="composer"
      />
    </Theme>
  );
}

const type = (field: HTMLTextAreaElement, value: string) => fireEvent.change(field, { target: { value } });

// The full suite runs in parallel; under that load a search or level can take longer than the 1 s default.
const WAIT = { timeout: 5000 };
// A test waits several times, so it gets more than the 5 s default in all.
vi.setConfig({ testTimeout: 20000 });

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
});

beforeEach(() => {
  // Where the node sits is known, so its checkbox is live.
  useChatStore.setState({
    collectionMetaCache: { q3: { name: 'Q3 Report', nodeType: 'record', connector: 'KB', ancestorIds: ['kb-1'] } },
    collectionNamesCache: {},
  });
  api.listCollectionsForChat.mockResolvedValue({ knowledgeBases: [], nextCursor: null });
  api.getNodeChildren.mockResolvedValue({ items: [], pagination: { nextCursor: null } });
  api.searchNodes.mockImplementation(({ q }: { q: string }) =>
    Promise.resolve({ items: q === 'Q3' ? [report] : [], pagination: { nextCursor: null } })
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('@ in the composer', () => {
  it('after a tick, drops only the ticked @query and keeps the words typed on after it', async () => {
    render(<Composer />);
    const field = screen.getByLabelText('composer') as HTMLTextAreaElement;
    type(field, '@Q3');
    const option = await screen.findByRole('option', { name: /Q3 Report/ }, WAIT);
    const box = option.querySelector('button[role=checkbox]') as HTMLButtonElement;
    await waitFor(() => expect(box.disabled).toBe(false), WAIT);
    fireEvent.click(box);
    await waitFor(() => expect(box.getAttribute('aria-checked')).toBe('true'), WAIT);
    type(field, '@Q3 what changed');
    await waitFor(() => expect(field.value).toBe('what changed'), WAIT);
  });

  async function embedReport(field: HTMLTextAreaElement) {
    type(field, 'see @Q3');
    fireEvent.click(await screen.findByRole('option', { name: /Q3 Report/ }, WAIT));
    await waitFor(() => expect(field.value).toContain('Q3 Report'), WAIT);
    type(field, `${field.value}today`);
  }

  it('deletes a chip whole on Backspace right after it', async () => {
    render(<Composer />);
    const field = screen.getByLabelText('composer') as HTMLTextAreaElement;
    await embedReport(field);
    const end = field.value.indexOf(MENTION_PAD_CLOSE) + MENTION_PAD_CLOSE.length;
    field.setSelectionRange(end, end);
    fireEvent.keyDown(field, { key: 'Backspace' });
    expect(field.value).toBe('see today');
  });

  it('deletes a chip from the remove button shown over it on hover', async () => {
    render(<Composer />);
    const field = screen.getByLabelText('composer') as HTMLTextAreaElement;
    await embedReport(field);
    const rect = { left: 0, top: 0, right: 200, bottom: 20, width: 200, height: 20, x: 0, y: 0 } as DOMRect;
    const rects = vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([rect] as unknown as DOMRectList);
    fireEvent.mouseMove(field, { clientX: 50, clientY: 10 });
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Q3 Report' }, WAIT));
    rects.mockRestore();
    expect(field.value).toBe('see today');
    expect(screen.queryByRole('button', { name: 'Remove Q3 Report' })).toBeNull();
  });
});
