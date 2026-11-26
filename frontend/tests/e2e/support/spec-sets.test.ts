import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { specSets, underE2e } from './spec-sets';

// WP65-PW: a checkout under a folder named like a spec folder (`…-harness/`) must not ignore every spec.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const E2E = path.resolve(HERE, '..');

const matches = (patterns: RegExp[], file: string) => patterns.some((p) => p.test(file));

function specsUnder(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : specsUnder(full);
    return entry.name.endsWith('.spec.ts') ? [full] : [];
  });
}

describe('WP65-PW spec sets', () => {
  const checkout = '/home/dev/pipeshub-ai-agent-harness/frontend/tests/e2e';
  const sets = specSets(checkout);

  it('ENGINE-SPECS-1 no spec on disk runs in both harness and harness-engine (an engine spec on the harness stack fails)', () => {
    const real = specSets(E2E);
    const both = specsUnder(E2E).filter((file) => matches(real.harness, file) && matches(real.harnessEngine, file));
    expect(both.map((file) => path.relative(E2E, file))).toEqual([]);
    expect(matches(real.harnessEngine, path.join(E2E, 'harness', 'answer-contract-engine.spec.ts'))).toBe(true);
  });

  it('WP65-PW-1 a folder above the tests named like a spec folder matches nothing', () => {
    const chat = `${checkout}/chat/chat-new-thread.spec.ts`;
    expect(matches(sets.notAuthenticated, chat)).toBe(false);
    expect(matches(specSets('/srv/harness/agents/seed/tests/e2e').notAuthenticated,
      '/srv/harness/agents/seed/tests/e2e/chat/x.spec.ts')).toBe(false);
  });

  it('WP65-PW-2 each set still takes its own specs', () => {
    expect(matches(sets.harness, `${checkout}/harness/human-smoke.spec.ts`)).toBe(true);
    expect(matches(sets.harness, `${checkout}/chat/chat-timeline.spec.ts`)).toBe(true);
    expect(matches(sets.ai, `${checkout}/agents/agent-builder.spec.ts`)).toBe(true);
    expect(matches(sets.harnessEngine, `${checkout}/chat/chat-durable-engine-e2e.spec.ts`)).toBe(true);
    expect(sets.setup.test(`${checkout}/setup/auth.setup.ts`)).toBe(true);
    expect(sets.login.test(`${checkout}/auth/login.spec.ts`)).toBe(true);
    expect(matches(sets.notAuthenticated, `${checkout}/seed/seed-kb.spec.ts`)).toBe(true);
  });

  it('CJ-5 the classic jobs engine spec runs only on the engine lane; the other classic jobs specs on the harness one', () => {
    const engine = `${checkout}/harness/classic-jobs-engine.spec.ts`;
    expect(matches(sets.harnessEngine, engine)).toBe(true);
    expect(matches(sets.harness, engine)).toBe(false);
    expect(matches(sets.notAuthenticated, engine)).toBe(true);
    expect(matches(sets.harness, `${checkout}/harness/classic-jobs-loop.spec.ts`)).toBe(true);
    expect(matches(sets.harnessEngine, `${checkout}/harness/classic-jobs-loop.spec.ts`)).toBe(false);
  });

  it('QA-CLASSIC-FU-6 the agent build-and-chat journey runs with a real model and on the scripted lane', () => {
    const spec = `${checkout}/agents/agent-create-chat.spec.ts`;
    expect(matches(sets.ai, spec)).toBe(true);
    expect(matches(sets.harness, spec)).toBe(true);
    expect(matches(sets.notAuthenticated, spec)).toBe(true);
  });

  it('PF-17-PW the perf specs run in their own project, never the authenticated one', () => {
    const spec = `${checkout}/perf/chat-large-run.perf.spec.ts`;
    expect(matches(sets.perf, spec)).toBe(true);
    expect(matches(sets.notAuthenticated, spec)).toBe(true);
    expect(matches(sets.perf, `${checkout}/chat/chat-basic.spec.ts`)).toBe(false);
  });

  it('QA-CLASSIC each QA spec runs in exactly one project, and never in the authenticated one', () => {
    const where = (file: string) =>
      (['harness', 'harnessEngine', 'qaUx', 'qaUxEngine', 'qaLatency', 'qaIndexed'] as const).filter((set) => matches(sets[set], `${checkout}/${file}`));
    expect(where('qa/journey-needs-you-decide.spec.ts')).toEqual(['harness']);
    expect(where('qa/indexed-journey-ask-cite-followup.spec.ts')).toEqual(['qaIndexed']);
    expect(where('qa/engine-journey-code-job.spec.ts')).toEqual(['harnessEngine']);
    expect(where('qa/latency-engine.spec.ts')).toEqual(['harnessEngine']);
    expect(where('qa/latency.spec.ts')).toEqual(['qaLatency']);
    expect(where('qa/ux-shots.spec.ts')).toEqual(['qaUx']);
    // QAFINAL-FU-UX: the engine captures need baselines, so run.sh durable's harness-engine never runs them.
    expect(where('qa/ux-shots-engine.spec.ts')).toEqual(['qaUxEngine']);
    for (const file of ['qa/journey-x.spec.ts', 'qa/indexed-journey-x.spec.ts', 'qa/latency.spec.ts', 'qa/ux-shots.spec.ts', 'qa/ux-shots-engine.spec.ts']) {
      expect(matches(sets.notAuthenticated, `${checkout}/${file}`)).toBe(true);
    }
  });

  it('WP65-PW-3 a pattern is anchored on the folder, not a prefix of a longer one', () => {
    expect(underE2e(checkout, 'harness/.*').test(`${checkout}/harnessx/a.spec.ts`)).toBe(false);
    expect(underE2e(checkout, 'harness/.*').test(`${checkout}-old/harness/a.spec.ts`)).toBe(false);
  });

  it('WP65-PW-4 Windows paths match with either separator', () => {
    const win = String.raw`C:\work\pipeshub-ai-agent-harness\frontend\tests\e2e`;
    const winSets = specSets(win);
    expect(matches(winSets.harness, String.raw`${win}\harness\a.spec.ts`)).toBe(true);
    expect(matches(winSets.notAuthenticated, String.raw`${win}\chat\chat-new-thread.spec.ts`)).toBe(false);
  });

  it('WP65-PW-5 in this checkout, the authenticated project keeps the specs no other project takes', () => {
    const real = specSets(E2E);
    const specs = specsUnder(E2E);
    const authenticated = specs.filter((file) => !matches(real.notAuthenticated, file));
    expect(authenticated.length).toBeGreaterThan(10);
    const inHarness = (file: string) => path.relative(E2E, file).split(path.sep)[0] === 'harness';
    expect(authenticated.some(inHarness)).toBe(false);
    expect(specs.filter(inHarness).every((file) => matches(real.harness, file) || matches(real.harnessEngine, file))).toBe(true);
  });

  it('FIX-PEOPLE no spec changes the New interface switch, which left with the new interface (UI-ASIDE)', () => {
    // The org flag and the person's choice were shared by every worker signed in as the admin.
    const switches = /\b(useNewInterface|writeChoice|NEW_INTERFACE_FLAG)\b|me\/preferences/;
    const writers = specsUnder(E2E).filter((file) => switches.test(readFileSync(file, 'utf8')));
    expect(writers.map((file) => path.relative(E2E, file))).toEqual([]);
  });
});
