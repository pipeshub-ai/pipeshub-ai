import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { ExpandableUserQuery } from '../expandable-user-query';
import { questionDisplay } from '../question-display';

afterEach(() => cleanup());

const h = React.createElement;

function renderQuery(props: Partial<React.ComponentProps<typeof ExpandableUserQuery>> & { question: string }) {
  return render(h(Theme, null, h(ExpandableUserQuery, props)));
}

function longQuestion(chars = 4990): string {
  return 'x'.repeat(chars);
}

describe('ExpandableUserQuery', () => {
  it('renders a short question as a heading without Show more', () => {
    renderQuery({ question: 'How do I reset my password?' });
    expect(screen.getByText('How do I reset my password?')).toBeTruthy();
    expect(screen.queryByText('Show more')).toBeNull();
    expect(screen.queryByText('Show less')).toBeNull();
  });

  it('treats whitespace-only as short (no Show more)', () => {
    renderQuery({ question: '   \n\t  ' });
    expect(screen.queryByText('Show more')).toBeNull();
  });

  it('calls onEdit when the edit control is clicked (not streaming, has messageId)', () => {
    const onEdit = vi.fn();
    renderQuery({
      question: 'Edit me',
      messageId: 'msg-1',
      isStreaming: false,
      onEdit,
    });
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('does not render the edit control while streaming', () => {
    renderQuery({
      question: 'Edit me',
      messageId: 'msg-1',
      isStreaming: true,
      onEdit: vi.fn(),
    });
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
  });

  it("keeps Edit's place while the answer runs, unseen and out of reach, so the question does not re-wrap when it ends", () => {
    const view = renderQuery({ question: 'Edit me', isStreaming: true, onEdit: vi.fn() });
    const holder = screen.getByTestId('user-query-edit-placeholder');
    expect(holder.getAttribute('aria-hidden')).toBe('true');
    expect(holder.hasAttribute('inert')).toBe(true);
    expect(holder.style.visibility).toBe('hidden');
    // The same box as the real Edit: the same button inside.
    const ghost = holder.querySelector('button') as HTMLButtonElement;
    expect(ghost.className).toContain('touch-hit');
    expect(ghost.className).toContain('rt-r-size-1');
    view.rerender(h(Theme, null, h(ExpandableUserQuery, { question: 'Edit me', messageId: 'msg-1', isStreaming: false, onEdit: vi.fn() })));
    expect(screen.queryByTestId('user-query-edit-placeholder')).toBeNull();
    expect(screen.getByRole('button', { name: /edit/i })).toBeTruthy();
    cleanup();

    // No place is kept where Edit will not be offered.
    renderQuery({ question: 'Edit me', isStreaming: true });
    expect(screen.queryByTestId('user-query-edit-placeholder')).toBeNull();
  });

  it('copies the full question when the copy control is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const q = 'Copy this query please';
    renderQuery({ question: q, messageId: 'msg-1', onEdit: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith(q);
  });

  it('shows a pasted URL as an underlined short link with the full URL on hover', () => {
    const url = 'https://github.com/pipeshub-ai/pipeshub-ai/pull/1234';
    renderQuery({ question: `Review ${url}, please` });
    const link = screen.getByRole('link', { name: '#1234' });
    expect(link.getAttribute('href')).toBe(url);
    expect(link.getAttribute('title')).toBe(url);
    expect(link.getAttribute('target')).toBe('_blank');
    expect(screen.getByTestId('user-query-heading').textContent).toContain(', please');
  });

  it('copies the original URL, not its short label', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const q = 'See https://github.com/o/r/issues/7';
    renderQuery({ question: q, messageId: 'msg-1', onEdit: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith(q);
  });

  it('Q5: draws every source the question named with @ as its chip in the heading, icon and name, of every kind', () => {
    const named = [
      { id: 'a1', name: 'S3', nodeType: 'app', connector: 'S3' },
      { id: 'k1', name: 'Engineering handbook', nodeType: 'kb', connector: 'KB' },
      { id: 'g1', name: 'Platform space', nodeType: 'recordGroup', connector: 'CONFLUENCE' },
      { id: 'f1', name: 'Q3 plans', nodeType: 'folder', connector: 'KB' },
      { id: 'r1', name: 'report.pdf', nodeType: 'record', connector: 'KB', extension: 'pdf' },
    ];
    renderQuery({
      // As sent: the composer leaves a space after every chip.
      question: 'Compare S3 with Engineering handbook and Platform space and Q3 plans and report.pdf against https://acme.atlassian.net/browse/PROJ-12',
      mentions: named,
    });
    const heading = screen.getByTestId('user-query-heading');
    const chips = Array.from(heading.querySelectorAll('[data-testid="source-chip"]')) as HTMLElement[];
    expect(chips.map((c) => [c.getAttribute('data-source-id'), c.textContent])).toEqual(named.map((n) => [n.id, n.name]));
    // Each carries its icon, drawn as the composer draws it.
    for (const chip of chips) expect(chip.querySelector('[aria-hidden] svg, [aria-hidden] img, [aria-hidden] .material-icons-outlined, [aria-hidden] span')).toBeTruthy();
    // The name is said once: as the chip, not also as words.
    expect(heading.textContent?.match(/report\.pdf/g)).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'PROJ-12' })).toBeTruthy();
  });

  it('leaves a name that is not in the question, or a question that names no source, as it was', () => {
    renderQuery({ question: 'What changed?', mentions: [{ id: 'r1', name: 'report.pdf', nodeType: 'record', connector: 'KB' }] });
    expect(screen.queryByTestId('source-chip')).toBeNull();
    expect(screen.getByTestId('user-query-heading').textContent).toBe('What changed?');
  });

  it('is an h1 holding only the question: Copy and Edit sit beside it, not inside it', () => {
    renderQuery({ question: 'How do I reset my password?', messageId: 'msg-1', onEdit: vi.fn() });
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading.textContent).toBe('How do I reset my password?');
    expect(heading.contains(screen.getByRole('button', { name: 'Copy' }))).toBe(false);
    expect(heading.contains(screen.getByRole('button', { name: 'Edit' }))).toBe(false);
  });

  it('under a byline is 20 px whatever its length, and 18 px when compact, with no space above it', () => {
    for (const question of ['How do I reset my password?', 'word '.repeat(60).trim()]) {
      renderQuery({ question, headline: { compact: false } });
      const heading = screen.getByTestId('user-query-heading');
      expect(heading.className).toContain('rt-r-size-5');
      expect(heading.className).not.toMatch(/rt-r-size-[67]/);
      expect(heading.style.paddingTop).toBe('');
      cleanup();
      renderQuery({ question, headline: { compact: true } });
      expect(screen.getByTestId('user-query-heading').className).toContain('rt-r-size-4');
      cleanup();
    }
  });

  it('without a byline is 18 px below the desktop width and 20 px from it, whatever its length', () => {
    for (const question of ['How do I reset my password?', 'word '.repeat(60).trim()]) {
      renderQuery({ question });
      const heading = screen.getByTestId('user-query-heading');
      expect(heading.className).toContain('rt-r-size-4');
      expect(heading.className).toContain('md:rt-r-size-5');
      expect(heading.className).not.toMatch(/rt-r-size-[67]/);
      cleanup();
    }
  });

  it('tracks the question by the size it resolves to: 18 px is -0.04 px and 20 px is -0.08 px', () => {
    renderQuery({ question: 'How do I reset my password?' });
    const heading = screen.getByTestId('user-query-heading');
    expect(heading.style.letterSpacing).toBe('');
    expect(heading.style.getPropertyValue('--letter-spacing-4')).toBe('-0.04px');
    expect(heading.style.getPropertyValue('--letter-spacing-5')).toBe('-0.08px');
  });

  it('lifts a long question into a Pasted text chip under a short heading, with no Show more', () => {
    const q = `Summarise this transcript. ${'x'.repeat(600)}`;
    renderQuery({ question: q });
    expect(screen.getByTestId('user-query-heading').textContent).toBe('Summarise this transcript.');
    expect(screen.getByRole('button', { name: /^Pasted text, / })).toBeTruthy();
    expect(screen.queryByText('Show more')).toBeNull();
  });

  it('opens the whole question from the chip', async () => {
    const q = longQuestion();
    renderQuery({ question: q });
    fireEvent.click(screen.getByRole('button', { name: /^Pasted text, / }));
    expect(await screen.findByText(q)).toBeTruthy();
  });

  it('copies the whole question, not its heading', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const q = `Summarise this transcript. ${'x'.repeat(600)}`;
    renderQuery({ question: q, messageId: 'msg-1', onEdit: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith(q);
  });

  it('keeps a 300-character question whole, with no chip', () => {
    const q = `${'word '.repeat(60)}`.trim();
    renderQuery({ question: q });
    expect(screen.getByTestId('user-query-heading').textContent).toBe(q);
    expect(screen.queryByTestId('user-query-pastes')).toBeNull();
  });

  it('shows a code-only question as a Pasted code chip with a hidden heading, no backticks on screen', () => {
    renderQuery({ question: '```js\nconst a = 1;\n```' });
    expect(screen.getByRole('button', { name: /^Pasted code, / })).toBeTruthy();
    const heading = screen.getByTestId('user-query-heading');
    expect(heading.textContent).toBe('const a = 1;');
    expect(heading.textContent).not.toContain('`');
  });

  it('keeps the words around a fence as the heading', () => {
    renderQuery({ question: 'Fix this: ```\nlet x = ;\n```' });
    expect(screen.getByTestId('user-query-heading').textContent).toBe('Fix this:');
    expect(screen.getByRole('button', { name: /^Pasted code, / })).toBeTruthy();
  });

});

describe('questionDisplay', () => {
  it('takes the first line as the heading when a body follows it', () => {
    const body = 'line\n'.repeat(10);
    expect(questionDisplay(`What changed here?\n${body}`)).toEqual({
      heading: 'What changed here?',
      pastes: [{ kind: 'text', text: body.trim() }],
      headingHidden: false,
    });
  });

  it('clips a single long line at 140 characters', () => {
    const d = questionDisplay('y'.repeat(4990));
    expect(d.heading).toBe(`${'y'.repeat(140)}…`);
    expect(d.pastes[0].text.length).toBe(4990);
  });

});

describe('ExpandableUserQuery on touch', () => {
  it('shows Copy without hovering when isMobile', () => {
    renderQuery({ question: 'How do I reset my password?', isMobile: true });
    expect(screen.getByRole('button', { name: 'Copy' }).style.opacity).toBe('1');
  });

  it('keeps Copy and Edit apart with a mouse, so their 28 px boxes do not overlap', () => {
    renderQuery({ question: 'How do I reset my password?', messageId: 'msg-1', onEdit: vi.fn() });
    expect(screen.getByRole('button', { name: 'Copy' }).parentElement?.className).toContain('rt-r-gap-2');
  });

  it('keeps Copy hidden until hover on desktop', () => {
    renderQuery({ question: 'How do I reset my password?' });
    expect(screen.getByRole('button', { name: 'Copy' }).style.opacity).toBe('0');
  });

  it('shows Copy without hovering on a touch tablet (no hover pointer)', () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) =>
      ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }) as unknown as MediaQueryList);
    try {
      renderQuery({ question: 'How do I reset my password?' });
      expect(screen.getByRole('button', { name: 'Copy' }).style.opacity).toBe('1');
    } finally {
      window.matchMedia = original;
    }
  });

  it('spreads Copy and Edit on touch so their 44 px tap areas do not overlap, and keeps them close with a mouse', () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) =>
      ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }) as unknown as MediaQueryList);
    try {
      renderQuery({ question: 'How do I reset my password?', messageId: 'msg-1', onEdit: vi.fn() });
      expect(screen.getByRole('button', { name: 'Copy' }).parentElement?.className).toContain('rt-r-gap-5');
    } finally {
      window.matchMedia = original;
    }
  });
});
