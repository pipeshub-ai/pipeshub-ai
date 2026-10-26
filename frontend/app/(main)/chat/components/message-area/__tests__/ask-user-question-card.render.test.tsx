import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { AskUserQuestionPayload } from '../../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AskUserQuestionCard } from '../ask-user-question-card';

const project = {
  uuid: 'q-project',
  question: 'Which project should the bug go in?',
  header: 'Project',
  multiSelect: false,
  options: [
    // `recommended` is what cards saved before it was removed still carry.
    { id: 'plat', label: 'PLAT', isUserInput: false, description: 'Platform team board', recommended: true },
    { id: 'web', label: 'WEB', isUserInput: false, description: 'Web app board' },
  ],
};
const labels = {
  uuid: 'q-labels',
  question: 'Which labels apply?',
  multiSelect: true,
  options: [
    { id: 'bug', label: 'bug', isUserInput: false },
    { id: 'ui', label: 'ui', isUserInput: false },
  ],
};

function payload(overrides: Partial<AskUserQuestionPayload> = {}): AskUserQuestionPayload {
  return { name: 'ask_user_question', userIntent: 'You asked me to file a bug.', questions: [project, labels], ...overrides };
}

describe('AskUserQuestionCard', () => {
  beforeEach(() => installBrowserShims());
  afterEach(() => cleanup());

  it('shows the header, the kind of choice and what each option does, and no recommended mark', () => {
    renderInTheme(<AskUserQuestionCard payload={payload()} initialAnswers={{}} status="pending" />);

    expect(screen.getByText('Project')).toBeTruthy();
    expect(screen.getByText('Select one')).toBeTruthy();
    expect(screen.queryByText('Recommended')).toBeNull();
    expect(screen.getByText('Platform team board')).toBeTruthy();
    expect(screen.getByText('Web app board')).toBeTruthy();
  });

  it('labels the choices with the question so a screen reader announces it', () => {
    renderInTheme(<AskUserQuestionCard payload={payload()} initialAnswers={{}} status="pending" />);

    const group = screen.getByRole('radiogroup');
    const heading = screen.getByRole('heading', { name: /Which project should the bug go in\?/ });
    expect(group.getAttribute('aria-labelledby')).toBe(heading.id);
  });

  it('"Skip all" sends every unanswered question as no preference in one submit', () => {
    const onSubmit = vi.fn();
    renderInTheme(
      <AskUserQuestionCard payload={payload()} initialAnswers={{}} status="pending" onSubmit={onSubmit} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Skip all' }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const [message, answers] = onSubmit.mock.calls[0];
    expect(message).toBe(
      'User selections:\n'
      + '1. "Which project should the bug go in?" → [No preference]\n'
      + '2. "Which labels apply?" → [No preference]',
    );
    expect(answers['q-project'].selectedOptionIds).toEqual(['__no_preference__']);
    expect(answers['q-labels'].selectedOptionIds).toEqual(['__no_preference__']);
  });

  it('"Skip all" keeps an answer already picked', () => {
    const onSubmit = vi.fn();
    const picked = { 'q-project': { questionUuid: 'q-project', selectedOptionIds: ['web'], userInputs: {} } };
    renderInTheme(
      <AskUserQuestionCard payload={payload()} initialAnswers={picked} status="pending" onSubmit={onSubmit} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Skip all' }));

    expect(onSubmit.mock.calls[0][0]).toContain('1. "Which project should the bug go in?" → WEB');
    expect(onSubmit.mock.calls[0][0]).toContain('2. "Which labels apply?" → [No preference]');
  });

  it('offers no "Skip all" for a single question, where Skip already sends it', () => {
    renderInTheme(
      <AskUserQuestionCard payload={payload({ questions: [project] })} initialAnswers={{}} status="pending" />,
    );

    expect(screen.queryByRole('button', { name: 'Skip all' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Skip' })).toBeTruthy();
  });

  it('a card past its expiry is read-only and says to reply in the chat', () => {
    renderInTheme(
      <AskUserQuestionCard
        payload={payload({ expiresAt: new Date(Date.now() - 60_000).toISOString() })}
        initialAnswers={{}}
        status="pending"
      />,
    );

    expect(screen.getByText('This question has expired. Reply in the chat to continue.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });

  const allAnswered = {
    'q-project': { questionUuid: 'q-project', selectedOptionIds: ['plat'], userInputs: {} },
    'q-labels': { questionUuid: 'q-labels', selectedOptionIds: ['bug'], userInputs: {} },
  };

  it('opens on the last question when every one is answered, as a card with a reply waiting does', () => {
    renderInTheme(<AskUserQuestionCard payload={payload()} initialAnswers={allAnswered} status="pending" />);

    expect(screen.getByText(/Which labels apply\?/)).toBeTruthy();
  });

  it('opens on the question asked for, even when every one is answered', () => {
    renderInTheme(
      <AskUserQuestionCard payload={payload()} initialAnswers={allAnswered} status="pending" initialStep={0} />,
    );

    expect(screen.getByText(/Which project should the bug go in\?/)).toBeTruthy();
    expect(screen.queryByText(/Which labels apply\?/)).toBeNull();
  });

  it('a card that has not expired yet can still be answered', () => {
    renderInTheme(
      <AskUserQuestionCard
        payload={payload({ expiresAt: new Date(Date.now() + 60_000).toISOString() })}
        initialAnswers={{}}
        status="pending"
      />,
    );

    expect(screen.getByRole('radiogroup')).toBeTruthy();
  });

  it('a card answered by a typed message shows that, without controls', () => {
    renderInTheme(
      <AskUserQuestionCard payload={payload({ repliedInChat: true })} initialAnswers={{}} status="persisted" />,
    );

    expect(screen.getByText('You replied in the chat.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
  });

  it('shows markup from the model as text, never as HTML', () => {
    const hostile = '<img src=x onerror="window.__pwned=1">';
    const { container } = renderInTheme(
      <AskUserQuestionCard
        payload={payload({
          userIntent: hostile,
          questions: [{
            ...project,
            header: '<b>bold</b>',
            options: [{ id: 'x', label: hostile, isUserInput: false, description: '<script>1</script>' }, project.options[1]],
          }],
        })}
        initialAnswers={{}}
        status="pending"
      />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getAllByText(hostile).length).toBeGreaterThan(0);
  });

  it('caps a typed answer at 1000 characters', () => {
    const typed = {
      ...project,
      options: [...project.options, { id: 'key', label: 'Enter a project key', isUserInput: true }],
    };
    renderInTheme(
      <AskUserQuestionCard payload={payload({ questions: [typed] })} initialAnswers={{}} status="pending" />,
    );

    fireEvent.click(screen.getByText('Enter a project key'));

    expect(screen.getByRole('textbox').getAttribute('maxlength')).toBe('1000');
  });
});
