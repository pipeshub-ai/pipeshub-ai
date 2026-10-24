import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { AskUserQuestionAnswer, AskUserQuestionPayload } from '../../../types';
import { AskUserQuestionCard } from '../ask-user-question-card';

afterEach(cleanup);

function payload(questions: AskUserQuestionPayload['questions']): AskUserQuestionPayload {
  return { name: 'ask_user_question', questions };
}

function question(
  uuid: string,
  text: string,
  label: string,
): AskUserQuestionPayload['questions'][number] {
  return {
    uuid,
    question: text,
    multiSelect: false,
    options: [{ id: `${uuid}-a`, label, isUserInput: false }],
  };
}

function selected(uuid: string): AskUserQuestionAnswer {
  return {
    questionUuid: uuid,
    selectedOptionIds: [`${uuid}-a`],
    userInputs: {},
  };
}

const region = question('q-region', 'Which region?', 'Europe');
const year = question('q-year', 'Which year?', '2024');

function renderCard(
  props: Partial<React.ComponentProps<typeof AskUserQuestionCard>> &
    Pick<React.ComponentProps<typeof AskUserQuestionCard>, 'payload' | 'status'>,
) {
  const ui = (
    <Theme>
      <AskUserQuestionCard initialAnswers={{}} {...props} />
    </Theme>
  );
  return render(ui);
}

describe('AskUserQuestionCard once every question is answered', () => {
  it('opens collapsed, the way Show less leaves it', () => {
    renderCard({
      payload: payload([region]),
      status: 'submitted',
      initialAnswers: { 'q-region': selected('q-region') },
    });

    expect(screen.getByRole('button', { name: /show more/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /show less/i })).toBeNull();
    expect(screen.queryByText(/Which region\?/)).toBeNull();
    expect(screen.queryByText('Europe')).toBeNull();
    expect(screen.queryByText('Answered')).toBeNull();
  });

  it('shows the answers again from Show more, then closes from Show less', () => {
    renderCard({
      payload: payload([region, year]),
      status: 'persisted',
      initialAnswers: {
        'q-region': selected('q-region'),
        'q-year': selected('q-year'),
      },
    });

    fireEvent.click(screen.getByRole('button', { name: /show more/i }));

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(screen.getByText(/Which year\?/)).toBeTruthy();
    expect(screen.getByText('Europe')).toBeTruthy();
    expect(screen.getByText('Answered')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /show less/i }));

    expect(screen.queryByText(/Which region\?/)).toBeNull();
    expect(screen.queryByText(/Which year\?/)).toBeNull();
    expect(screen.getByRole('button', { name: /show more/i })).toBeTruthy();
  });

  it('stays open while a question is still unanswered', () => {
    renderCard({
      payload: payload([region, year]),
      status: 'persisted',
      initialAnswers: { 'q-region': selected('q-region') },
    });

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(screen.getByText(/Which year\?/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /show less/i })).toBeTruthy();
  });

  it('keeps the question open while the user is still answering', () => {
    renderCard({
      payload: payload([region]),
      status: 'pending',
    });

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /show more/i })).toBeNull();
  });

  it('collapses when a pending card becomes submitted with every answer', () => {
    const answers = { 'q-region': selected('q-region') };
    const { rerender } = renderCard({
      payload: payload([region]),
      status: 'pending',
      initialAnswers: answers,
    });

    expect(screen.getByText(/Which region\?/)).toBeTruthy();

    rerender(
      <Theme>
        <AskUserQuestionCard
          payload={payload([region])}
          status="submitted"
          initialAnswers={answers}
        />
      </Theme>,
    );

    expect(screen.getByRole('button', { name: /show more/i })).toBeTruthy();
    expect(screen.queryByText(/Which region\?/)).toBeNull();
  });

  it('keeps a manual Show more open across a re-render', () => {
    const props = {
      payload: payload([region]),
      status: 'submitted' as const,
      initialAnswers: { 'q-region': selected('q-region') },
    };
    const { rerender } = renderCard(props);

    fireEvent.click(screen.getByRole('button', { name: /show more/i }));
    rerender(
      <Theme>
        <AskUserQuestionCard {...props} />
      </Theme>,
    );

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /show less/i })).toBeTruthy();
  });

  it('opens again when a new question is still unanswered', () => {
    const { rerender } = renderCard({
      payload: payload([region]),
      status: 'submitted',
      initialAnswers: { 'q-region': selected('q-region') },
    });

    expect(screen.queryByText(/Which region\?/)).toBeNull();

    rerender(
      <Theme>
        <AskUserQuestionCard
          payload={payload([region, year])}
          status="submitted"
          initialAnswers={{ 'q-region': selected('q-region') }}
        />
      </Theme>,
    );

    expect(screen.getByText(/Which region\?/)).toBeTruthy();
    expect(screen.getByText(/Which year\?/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /show less/i })).toBeTruthy();
  });
});
