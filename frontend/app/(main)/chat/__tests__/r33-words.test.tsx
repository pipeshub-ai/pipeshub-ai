/**
 * R33-WORDS: the person always reads a plain, honest account of what happened.
 * - R32REPO-FU-1: above an answer, what the run tried and could not do (a declined clone), worded here from the
 *   answer's typed `limits` in every locale; the English line the answer text starts with is left out.
 * - R32SECCOMP-FU-4: a job that ran out of memory reads "ran out of memory (limit 4 GB)" on its card, the same words the
 *   run's own error uses, from its saved part or its stream's end.
 * - R32ENGFU-FU-1: an empty command whose engine can lose output reads "No output was captured.", never "No output.".
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { TFunction } from 'i18next';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { emptyJobStream, reduceJobFrame } from '@/lib/harness-jobs/job-stream';
import { couldNotDoOf, withoutLeadLine } from '../could-not-do';
import { withoutNotFoundLine } from '../subject-not-found';
import { CouldNotDoNotice } from '../components/message-area/could-not-do-notice';
import { jobCardState, secondLine } from '../components/message-area/job-card/job-card-state';
import { CommandBlock } from '../components/activity-panel/command-block';
import { reduceActivity } from '../activity';
import type { ActivityCommand, ActivityView, MessagePart } from '../types';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];
const LINE = "I couldn't read github.com/onyx-dot-app/onyx: the clone was declined.";
const REPO = 'github.com/onyx-dot-app/onyx';
const LIMITS = {
  items: [{ kind: 'repository.declined', repository: REPO }, { kind: 'repository.failed', count: 2 }, { kind: 'web' }],
  line: LINE,
};

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const t = ((key: string, vars?: Record<string, unknown>) => i18n.t(key, vars)) as unknown as TFunction;

describe('R32REPO-FU-1: what the run could not do, above the answer', () => {
  it('reads the saved message’s fact, else the final part’s, dropping a kind it cannot word', () => {
    const parts: MessagePart[] = [{ type: 'text', content: '', isFinal: true, limits: LIMITS }];
    const fact = couldNotDoOf(parts);
    expect(fact?.items).toEqual([{ kind: 'repository.declined', repository: REPO, count: 1 }, { kind: 'repository.failed', count: 2 }]);
    expect(couldNotDoOf(undefined, LIMITS)?.line).toBe(LINE);
    expect(couldNotDoOf(undefined, { items: [{ kind: 'web' }], line: LINE })).toBeNull();
    expect(couldNotDoOf(undefined, { items: LIMITS.items })).toBeNull();
  });

  it('the answer is shown without the English lines it starts with, the not-found one first', () => {
    const notFound = { subject: 'Onyx roadmap', hits: 0, line: '**I couldn\'t find anything.**', near: [], proceeded: false, places: [] };
    const answer = `${notFound.line}\n\n${LINE}\n\nOnyx's docs say X.`;
    expect(withoutLeadLine(withoutNotFoundLine(answer, notFound), LINE)).toBe("Onyx's docs say X.");
    expect(withoutLeadLine('Something else.', LINE)).toBe('Something else.');
  });

  it('words each thing in every locale', async () => {
    const fact = couldNotDoOf(undefined, LIMITS)!;
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      render(
        <Theme>
          <CouldNotDoNotice fact={fact} />
        </Theme>,
      );
      const text = screen.getByTestId('could-not-do-notice').textContent ?? '';
      expect(text, locale).toContain(REPO);
      expect(text, locale).toContain('2');
      expect(text, locale).not.toMatch(/\{\{|couldNotDo\./);
      cleanup();
    }
    await i18n.changeLanguage('en-US');
    render(
      <Theme>
        <CouldNotDoNotice fact={fact} />
      </Theme>,
    );
    expect(screen.getByText(`I couldn't read ${REPO}: the clone was declined.`)).toBeTruthy();
    expect(screen.getByText("I couldn't read 2 code repositories: the clone failed.")).toBeTruthy();
  });

  it('R41-CLONE: a named repository never cloned is worded in every locale', async () => {
    const fact = couldNotDoOf(undefined, {
      items: [{ kind: 'repository.not_cloned', repository: REPO }],
      line: `I couldn't read ${REPO}: it was never cloned, so its code was not checked.`,
    })!;
    expect(fact.items).toEqual([{ kind: 'repository.not_cloned', repository: REPO, count: 1 }]);
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      render(
        <Theme>
          <CouldNotDoNotice fact={fact} />
        </Theme>,
      );
      const text = screen.getByTestId('could-not-do-notice').textContent ?? '';
      expect(text, locale).toContain(REPO);
      expect(text, locale).not.toMatch(/\{\{|couldNotDo\./);
      cleanup();
    }
    await i18n.changeLanguage('en-US');
  });
});

describe('R32SECCOMP-FU-4: a job card that ran out of memory names its limit', () => {
  it('from the saved part and from the job’s own stream', () => {
    const fromPart = jobCardState({ part: { status: 'failed', errorType: 'out_of_memory', memoryLimitMb: 4096 }, isOwner: true });
    expect(secondLine(fromPart, { now: null, frames: [] }, t)).toBe(
      "This task ran out of memory (limit 4 GB) and was stopped. Send a smaller task, or ask your admin about the coding engine's memory.",
    );
    const stream = reduceJobFrame(emptyJobStream('r'), { type: 'RUN_ERROR', runId: 'r', code: 'out_of_memory', details: { memoryLimitMb: 1536 } });
    const fromStream = jobCardState({ part: { status: 'running' }, stream, isOwner: true });
    expect(secondLine(fromStream, { now: null, frames: [] }, t)).toContain('(limit 1536 MB)');
    const other = jobCardState({ part: { status: 'failed', errorType: 'crash_loop', memoryLimitMb: 4096 }, isOwner: true });
    expect(other.memoryLimitMb).toBeUndefined();
  });
});

describe('R32ENGFU-FU-1: an empty command whose engine can lose output', () => {
  const command = (overrides: Partial<ActivityCommand> = {}): ActivityCommand => ({
    commandId: 'c1', source: 'engine', command: 'head -n 4 SKILL.md', status: 'completed', exitCode: 0,
    startedAt: 1, durationMs: 5, stdout: '', stderr: '', truncated: false, liveDropped: 0, hasLog: false, ...overrides,
  });

  it('reads "No output was captured." in every locale; any other empty command keeps "No output."', async () => {
    render(<Theme><CommandBlock command={command({ outputMayBeLost: true })} onOpenLog={() => {}} /></Theme>);
    expect(screen.getByText('No output was captured.')).toBeTruthy();
    cleanup();
    render(<Theme><CommandBlock command={command()} onOpenLog={() => {}} /></Theme>);
    expect(screen.getByText('No output.')).toBeTruthy();
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      expect(i18n.t('activityPanel.command.noOutputCaptured'), locale).not.toBe(i18n.t('activityPanel.command.noOutput'));
    }
  });

  it('a live upsert without the mark keeps what the panel knew', () => {
    const view: ActivityView = {
      runId: 'r', status: 'running', engine: true, commands: [command({ status: 'running', outputMayBeLost: true })],
      omittedCommands: 0, tools: [], tasks: [], files: [], followups: [],
    };
    const next = reduceActivity(view, { type: 'command', command: command() });
    expect(next?.commands[0]?.outputMayBeLost).toBe(true);
  });
});
