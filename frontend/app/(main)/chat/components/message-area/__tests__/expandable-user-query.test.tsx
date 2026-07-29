import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import {
  ExpandableUserQuery,
  QUESTION_CHAR_LIMIT,
  EXPANDED_MAX_HEIGHT_PX,
} from '../expandable-user-query';

afterEach(() => cleanup());

const h = React.createElement;

function renderQuery(props: Partial<React.ComponentProps<typeof ExpandableUserQuery>> & { question: string }) {
  return render(h(Theme, null, h(ExpandableUserQuery, props)));
}

function longQuestion(overBy = 20): string {
  return 'x'.repeat(QUESTION_CHAR_LIMIT + overBy);
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

  it('shows truncated heading and Show more for a long question (no bubble)', () => {
    const q = longQuestion();
    const { container } = renderQuery({ question: q });
    expect(screen.getByText('Show more')).toBeTruthy();
    expect(container.querySelector('[data-testid="user-query-bubble"]')).toBeNull();
    expect(container.querySelector('[data-testid="user-query-text"]')).toBeNull();
    const preview = q.slice(0, QUESTION_CHAR_LIMIT).trimEnd() + '…';
    expect(screen.getByText(preview)).toBeTruthy();
  });

  it('preserves newlines in the collapsed heading preview', () => {
    const q = `Line one\nLine two\n${'x'.repeat(QUESTION_CHAR_LIMIT)}`;
    const { container } = renderQuery({ question: q });
    const heading = container.querySelector('[data-testid="user-query-heading"]') as HTMLElement;
    expect(heading).toBeTruthy();
    expect(heading.style.whiteSpace).toBe('pre-wrap');
    expect(heading.textContent).toContain('Line one\nLine two');
  });

  it('shows polished bubble with actions only after Show more, then collapses', () => {
    const q = longQuestion();
    const { container } = renderQuery({
      question: q,
      messageId: 'msg-1',
      onEdit: vi.fn(),
    });
    fireEvent.click(screen.getByText('Show more'));
    expect(screen.getByText('Show less')).toBeTruthy();
    expect(container.querySelector('[data-testid="user-query-bubble"]')).toBeTruthy();
    const textBox = container.querySelector('[data-testid="user-query-text"]') as HTMLElement;
    expect(textBox.textContent).toBe(q);
    expect(textBox.style.maxHeight).toBe(`${EXPANDED_MAX_HEIGHT_PX}px`);
    expect(textBox.style.overflowY).toBe('auto');
    const actions = container.querySelector('[data-testid="user-query-actions"]') as HTMLElement;
    expect(actions).toBeTruthy();
    expect(actions.contains(screen.getByText('Show less'))).toBe(true);
    expect(actions.contains(screen.getByRole('button', { name: /edit/i }))).toBe(true);
    fireEvent.click(screen.getByText('Show less'));
    expect(screen.getByText('Show more')).toBeTruthy();
    expect(container.querySelector('[data-testid="user-query-bubble"]')).toBeNull();
  });

  it('keeps edit always visible in the bottom action row when expanded', () => {
    const q = longQuestion();
    renderQuery({
      question: q,
      messageId: 'msg-1',
      isStreaming: false,
      onEdit: vi.fn(),
    });
    fireEvent.click(screen.getByText('Show more'));
    const editBtn = screen.getByRole('button', { name: /edit/i });
    expect(editBtn.style.opacity).toBe('1');
  });

  it('resets expanded state when question changes', () => {
    const q1 = longQuestion(20);
    const q2 = longQuestion(30);
    const { container, rerender } = renderQuery({ question: q1 });
    fireEvent.click(screen.getByText('Show more'));
    expect(screen.getByText('Show less')).toBeTruthy();
    rerender(h(Theme, null, h(ExpandableUserQuery, { question: q2 })));
    expect(screen.getByText('Show more')).toBeTruthy();
    expect(screen.queryByText('Show less')).toBeNull();
    expect(container.querySelector('[data-testid="user-query-bubble"]')).toBeNull();
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

  it('shows copy beside edit in the expanded action row', () => {
    const q = longQuestion();
    const { container } = renderQuery({
      question: q,
      messageId: 'msg-1',
      onEdit: vi.fn(),
    });
    fireEvent.click(screen.getByText('Show more'));
    const actions = container.querySelector('[data-testid="user-query-actions"]') as HTMLElement;
    expect(actions.contains(screen.getByRole('button', { name: /copy/i }))).toBe(true);
    expect(actions.contains(screen.getByRole('button', { name: /edit/i }))).toBe(true);
  });
});
