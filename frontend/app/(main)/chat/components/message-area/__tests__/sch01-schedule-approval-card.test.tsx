/**
 * ADR-SCH-01 D4: the approval card of `pipeshub@schedules/create` says what will happen in plain words: "Set up a
 * schedule", its name, when ("Once, today at 3:47 PM", "Every weekday at 9:00 AM (America/Los_Angeles)") and what
 * each run is asked; never raw JSON, every secret masked as on any card.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { TFunction } from 'i18next';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { interactionAction, interactionTitle, isScheduleCreate } from '../../../interaction-wording';
import { askQuestion } from '@/lib/harness-jobs/approval-words';
import type { Words } from '@/lib/harness-jobs/words';
import type { PendingApproval } from '../../../types';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '../../../__tests__/token-vector';

process.env.TZ = 'America/Los_Angeles';

const h = React.createElement;
let t: TFunction;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  t = i18n.t.bind(i18n) as TFunction;
});
afterEach(() => cleanup());

const plain = (text: string) => text.replace(/[  ]/g, ' ');

function held(args: Record<string, unknown> | string, toolName = 'pipeshub@schedules/create'): PendingApproval {
  return {
    interactionId: 'ix.approve.kabc',
    toolCallId: 'call-1',
    toolName,
    args: typeof args === 'string' ? args : JSON.stringify(args),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
  };
}

const inMinutes = (minutes: number) => new Date(Date.now() + minutes * 60_000);

describe('the schedule card', () => {
  it('a one-off: title, name, "Once, today at …" and the ask, no JSON', () => {
    // Late in the day the "today" would roll over; pin a time that is today in LA, a few minutes on.
    const at = inMinutes(5);
    const sameDay = at.toDateString() === new Date().toDateString();
    const card = render(
      h(Theme, null,
        h(ApprovalCard, {
          approval: held({ name: 'Joke in five minutes', ask: 'Tell me a short, clean joke.', when: { kind: 'once', at: at.toISOString(), timeZone: 'America/Los_Angeles' } }),
          onDecide: () => {},
        }),
      ),
    );
    expect(screen.getByTestId('interaction-heading').textContent).toBe('Set up a schedule');
    const summary = screen.getByTestId('schedule-summary');
    expect(summary.textContent).toContain('Joke in five minutes');
    expect(summary.textContent).toContain('Tell me a short, clean joke.');
    expect(summary.textContent).toContain('Each run is asked');
    const when = plain(screen.getByTestId('schedule-when').textContent ?? '');
    expect(when).toMatch(sameDay ? /^Once, today at \d{1,2}:\d{2} (AM|PM)$/ : /^Once, tomorrow at /);
    expect(card.container.textContent).not.toContain('{');
    expect(screen.queryByTestId('approval-preview')).toBeNull();
    // Deny and Allow name what they decide in words.
    expect(screen.getAllByRole('button', { name: /Set up a schedule/ })).toHaveLength(2);
  });

  it('a cron: "Every weekday at 9:00 AM (America/Los_Angeles)" and its notices', () => {
    render(
      h(Theme, null,
        h(ApprovalCard, {
          approval: held(
            { name: 'Jira digest', ask: 'Summarise my open Jira issues.', when: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'America/Los_Angeles' }, notify: ['in_app', 'email'] },
            'schedule_create',
          ),
          onDecide: () => {},
        }),
      ),
    );
    expect(plain(screen.getByTestId('schedule-when').textContent ?? '')).toBe('Every weekday at 9:00 AM (America/Los_Angeles)');
    expect(screen.getByTestId('schedule-summary').textContent).toContain('In PipesHub, Email');
  });

  it('an uncommon cron shows its text marked cron', () => {
    render(h(Theme, null, h(ApprovalCard, { approval: held({ name: 'n', ask: 'a', when: { kind: 'cron', cron: '0 9 1-15 * *', timeZone: 'UTC' } }) })));
    expect(screen.getByTestId('schedule-when').textContent).toBe('0 9 1-15 * * (cron) (UTC)');
  });

  it('arguments that are not a schedule are listed as any call’s', () => {
    render(h(Theme, null, h(ApprovalCard, { approval: held({ name: 'n', when: 'tomorrow' }) })));
    expect(screen.queryByTestId('schedule-summary')).toBeNull();
    expect(screen.getByTestId('approval-preview')).toBeTruthy();
  });

  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))('masks %s in the name and the ask', (_id, secret) => {
    const { container } = render(
      h(Theme, null,
        h(ApprovalCard, {
          approval: held({ name: `Digest ${secret}`, ask: `Post with ${secret}`, when: { kind: 'cron', cron: '0 9 * * *', timeZone: 'UTC' } }),
          onDecide: () => {},
        }),
      ),
    );
    const attributes = [...container.querySelectorAll('*')].flatMap((el) => [...el.attributes].map((a) => a.value));
    expect([container.textContent ?? '', ...attributes].join('\n')).not.toContain(secret);
    expect(screen.getByTestId('schedule-summary').textContent).toContain(VECTOR_MASK);
  });
});

describe('the schedule tool’s words', () => {
  it('is matched by its id or model name only, never an MCP lookalike', () => {
    expect(isScheduleCreate({ toolName: 'pipeshub@schedules/create' })).toBe(true);
    expect(isScheduleCreate({ toolName: 'schedule_create' })).toBe(true);
    expect(isScheduleCreate({ toolName: 'mcp_evil_schedule_create' })).toBe(false);
    expect(interactionTitle(t, { toolName: 'mcp_evil_schedule_create' })).not.toBe('Set up a schedule');
  });

  it('titles and actions, on the card and on the job screens', () => {
    expect(interactionTitle(t, { toolName: 'schedule_create', displayName: 'Anything' })).toBe('Set up a schedule');
    expect(interactionAction(t, { toolName: 'pipeshub@schedules/create' })).toBe('Set up a schedule');
    expect(askQuestion(i18n.t.bind(i18n) as unknown as Words, { toolName: 'pipeshub@schedules/create' })).toBe('Set up a schedule?');
  });
});
