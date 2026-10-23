import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

// The edition's seams, as an edition that draws `@Bea` itself would fill them.
vi.mock('@/config', async () => {
  const shared = await vi.importActual<typeof import('../../../chat-extension')>('../../../chat-extension');
  const mark = shared.ANSWER_INLINE_MARK;
  return {
    chatExtension: {
      ...shared.defaultChatExtension,
      answer: {
        toMarkdown: (text: string) => text.replace(/@Bea\b/g, `${mark}Bea${mark}`),
        toPlainText: (text: string) => text,
      },
    },
    chatSlots: {
      ...shared.defaultChatSlots,
      AnswerInlineText: ({ text }: { text: string }) =>
        React.createElement('span', { 'data-testid': 'inline' }, text.split(mark).join('|')),
    },
  };
});

import { AnswerContent } from '../answer-content';

afterEach(() => cleanup());

const h = React.createElement;
const renderAnswer = (content: string) => render(h(Theme, null, h(AnswerContent, { content })));

describe('AnswerContent: text the chat extension draws itself', () => {
  it('hands the marked text of paragraphs, list items and table cells to AnswerInlineText', () => {
    const { getAllByTestId, container } = renderAnswer(
      ['Thanks @Bea, see below.', '', '- **@Bea** owns PROJ-7', '', '| Who |', '| --- |', '| @Bea |'].join('\n'),
    );
    const inline = getAllByTestId('inline');
    expect(inline.length).toBeGreaterThanOrEqual(3);
    expect(inline[0]!.textContent).toBe('Thanks |Bea|, see below.');
    expect(container.textContent).not.toContain('');
  });

  it('leaves code and unmarked text as plain text', () => {
    const { queryAllByTestId, container } = renderAnswer('Use `@Bea` in code.\n\nNobody here.');
    expect(queryAllByTestId('inline')).toHaveLength(0);
    expect(container.textContent).toContain('Nobody here.');
  });
});
