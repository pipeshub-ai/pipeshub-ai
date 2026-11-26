import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { ERROR_CODES, type ErrorCode } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';
import { ANSWER_ERROR_CODES } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';
import { NEXT_ACTION_KINDS, TOOL_ERROR_CODES, type NextActionKind } from '@/lib/contracts/harness/generated/core/enums';
import {
  admissionRefusalText,
  answerErrorText,
  NEXT_ACTION_TABLES,
  nextActionKind,
  oneClickStep,
  runErrorText,
  toolFailureText,
} from '../harness-errors';

/**
 * UX-06 (C10.02, WP-18): every typed error the chat shows has a next action from the contract (a catalogue
 * code's `nextAction`, or `vectors/error-next-actions.json` for run endings, error answers and failed tool
 * calls), and the words the chat shows for it name that step.
 */

const VECTORS = join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors');
type NextAction = { kind: NextActionKind; text: string };
type Section = Record<string, NextAction>;
const NEXT: { runErrors: Section; answerErrors: Section; toolErrors: Section } = JSON.parse(
  readFileSync(join(VECTORS, 'error-next-actions.json'), 'utf8'),
);
const RUN_ERROR_CODES: string[] = JSON.parse(readFileSync(join(VECTORS, 'run-error-codes.json'), 'utf8')).codes;

/** How the chat's English names each kind of step; `none` asks nothing of the person. */
const NAMES_STEP: Record<NextActionKind, RegExp | null> = {
  none: null,
  internal: null,
  retry: /again|once more/i,
  wait_then_retry: /wait|in a (minute|moment|few minutes)/i,
  reload: /reload/i,
  change_request: /pick|rephrase|shorten|change|fix|choose|remove|smaller|without/i,
  new_conversation: /new chat|new conversation|continue privately/i,
  reconnect: /connect|add it/i,
  ask_owner: /owner/i,
  ask_admin: /admin/i,
};

/** Words that name no step. The five WP-18 left open got approved copy (C10.02), so none is left. */
const SHOWN_WITHOUT_ITS_STEP = new Set<string>();

type Shown = { where: string; kind: NextActionKind; words: string };

const shown = (): Shown[] => {
  const generic = admissionRefusalText('HTTP_AGENT_HARNESS_NO_SUCH_CODE');
  const out: Shown[] = [];
  for (const code of Object.keys(ERROR_CODES) as ErrorCode[]) {
    const words = admissionRefusalText(`HTTP_AGENT_HARNESS_${code}`);
    // A code the chat words with the generic refusal is shown as that refusal, checked once below.
    if (words !== generic) out.push({ where: `admission:${code}`, kind: ERROR_CODES[code].nextAction.kind, words });
  }
  out.push({ where: 'admission:generic', kind: 'retry', words: generic });
  out.push({ where: 'admission:network', kind: 'retry', words: admissionRefusalText(null) });
  for (const [code, action] of Object.entries(NEXT.runErrors)) {
    out.push({ where: `run:${code}`, kind: action.kind, words: runErrorText(code) ?? '' });
  }
  for (const [code, action] of Object.entries(NEXT.answerErrors)) {
    out.push({ where: `answer:${code}`, kind: action.kind, words: answerErrorText(code) ?? '' });
  }
  for (const [code, action] of Object.entries(NEXT.toolErrors)) {
    out.push({ where: `tool:${code}`, kind: action.kind, words: toolFailureText({ errorCode: code }, 'Jira') });
  }
  return out;
};

describe('UX-06: every typed error the chat shows has a next action', () => {
  const start = i18n.language;
  beforeAll(async () => {
    await i18n.changeLanguage('en-US');
  });
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it('every catalogue code carries a next action of a known kind, with words', () => {
    for (const [code, entry] of Object.entries(ERROR_CODES)) {
      expect(NEXT_ACTION_KINDS, code).toContain(entry.nextAction.kind);
      expect(entry.nextAction.text.length, code).toBeGreaterThan(0);
    }
  });

  it('the vector names every run ending, error answer and tool failure code of the contract, no more', () => {
    expect(Object.keys(NEXT.runErrors).sort()).toEqual([...RUN_ERROR_CODES].sort());
    expect(Object.keys(NEXT.answerErrors).sort()).toEqual([...ANSWER_ERROR_CODES].sort());
    expect(Object.keys(NEXT.toolErrors).sort()).toEqual([...TOOL_ERROR_CODES].sort());
  });

  it('the chat has its own words for every run ending and error answer of the vector', () => {
    for (const code of Object.keys(NEXT.runErrors)) expect(runErrorText(code), code).not.toBeNull();
    for (const code of Object.keys(NEXT.answerErrors)) expect(answerErrorText(code), code).not.toBeNull();
  });

  it("UX-06: the chat's own next-action tables equal the vector, code for code", () => {
    const kinds = (section: Section) => Object.fromEntries(Object.entries(section).map(([code, a]) => [code, a.kind]));
    expect(NEXT_ACTION_TABLES.runErrors).toEqual(kinds(NEXT.runErrors));
    expect(NEXT_ACTION_TABLES.answerErrors).toEqual(kinds(NEXT.answerErrors));
  });

  it('UX-06: an admission refusal reads its catalogue next action; no response and an unnamed refusal retry', () => {
    for (const [code, entry] of Object.entries(ERROR_CODES)) {
      expect(nextActionKind('admission', `HTTP_AGENT_HARNESS_${code}`), code).toBe(entry.nextAction.kind);
    }
    expect(nextActionKind('admission', null)).toBe('retry');
    expect(nextActionKind('admission', 'HTTP_AGENT_HARNESS_NO_SUCH_CODE')).toBe('retry');
    expect(nextActionKind('run', 'no_such_ending')).toBeNull();
  });

  it('UX-06: a one-click button only for retry, wait then retry, reload and a new chat', () => {
    expect(oneClickStep('admission', 'HTTP_AGENT_HARNESS_ACCESS_POLICY_UNAVAILABLE')).toBe('wait_then_retry');
    expect(oneClickStep('admission', 'HTTP_AGENT_HARNESS_SESSION_ERASED')).toBe('new_conversation');
    expect(oneClickStep('run', 'sandbox_unavailable')).toBe('wait_then_retry');
    expect(oneClickStep('run', 'gvisor_unavailable')).toBeNull();
    expect(oneClickStep('answer', 'budget_exhausted')).toBeNull();
    expect(oneClickStep('answer', 'budget_insufficient')).toBeNull();
    expect(oneClickStep('answer', 'timeout')).toBe('retry');
    expect(oneClickStep('answer', 'content_filter')).toBeNull();
  });

  it("UX-06: an outage of the org's AI rules says to wait, never to pick another model", () => {
    const words = admissionRefusalText('HTTP_AGENT_HARNESS_ACCESS_POLICY_UNAVAILABLE');
    expect(words).toMatch(/wait a moment/i);
    expect(words).not.toMatch(/pick another model/i);
  });

  it('the words the chat shows name the step of their kind', () => {
    const missing = shown()
      .filter(({ kind, words }) => {
        const step = NAMES_STEP[kind];
        return step !== null && !step.test(words);
      })
      .map(({ where }) => where);
    expect(new Set(missing)).toEqual(SHOWN_WITHOUT_ITS_STEP);
  });
});
