import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { AskUserQuestionCard } from '../ask-user-question-card';
import type { AskUserQuestionPayload } from '../../../types';

const PAYLOAD = {
  name: 'ask_user_question',
  questions: [{ uuid: 'q1', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }, { id: 'us', label: 'US' }] }],
} as AskUserQuestionPayload;

const ANSWERED = { q1: { questionUuid: 'q1', selectedOptionIds: ['eu'], userInputs: {} } };

function card(props: Partial<React.ComponentProps<typeof AskUserQuestionCard>> = {}) {
  const onSubmit = vi.fn();
  render(
    <Theme>
      <AskUserQuestionCard payload={PAYLOAD} initialAnswers={ANSWERED} status="pending" onSubmit={onSubmit} {...props} />
    </Theme>,
  );
  return onSubmit;
}

afterEach(cleanup);

describe('AskUserQuestionCard binding (CL-17)', () => {
  it('is interactive for the person asked', () => {
    const onSubmit = card({ readOnlyFor: undefined });

    expect(screen.queryByTestId('ask-card-waiting')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('waits for someone else: text shown, every input disabled, submit does nothing', () => {
    const onSubmit = card({ readOnlyFor: { name: 'Bob' } });

    expect(screen.getByTestId('ask-card-waiting').textContent).toContain('Waiting for Bob to answer');
    for (const radio of screen.getAllByRole('radio')) expect((radio as HTMLButtonElement).disabled).toBe(true);
    const submit = screen.getByRole('button', { name: /submit/i }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('names a former member', () => {
    card({ readOnlyFor: { name: null } });

    expect(screen.getByTestId('ask-card-waiting').textContent).toContain('Waiting for Former member to answer');
  });
});
