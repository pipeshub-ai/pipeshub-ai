/**
 * WP-66 (honest meter): a model call cut short before the provider reported its usage is charged an estimate.
 * WP66-UI-1/2 the run's cost line says so only when the budget counts such calls (an older server sends no count);
 * WP66-UI-3/4 the run summary's cost line says so only when the digest marks its cost as partly estimated.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { RunBudgetView, RunDigestView } from '../../../types';
import { RunCost } from '../run-cost';
import { runSummaryLines } from '../run-summary';

const BUDGET: RunBudgetView = {
  cap: { usdMicros: 5_000_000, tokens: null },
  committed: { usdMicros: 420_000, tokens: 1_234 },
  reserved: { usdMicros: 0, tokens: 0 },
  available: { usdMicros: 4_580_000, tokens: null },
  exhausted: false,
};

const DIGEST: RunDigestView = {
  outcome: 'cancelled',
  actions: { total: 0, failed: 0, refused: 0, tools: [] },
  commands: { total: 0, failed: 0 },
  records: { read: 0, changed: 0 },
  artifacts: [],
  files: { added: 0, modified: 0, deleted: 0 },
  approvals: { asked: 0, approved: 0, denied: 0, unanswered: 0 },
  questions: { asked: 0, open: false },
  delegated: 0,
  cost: { usdMicros: 41_500, tokens: 12_300 },
  durationMs: 1_000,
};

const HINT =
  'Part of this is an estimate: a step was stopped before the provider reported what it used. It is corrected if the provider reports it.';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const view = (budget: RunBudgetView) => render(<Theme><RunCost budget={budget} /></Theme>);

describe('WP-66: the run cost line', () => {
  it('WP66-UI-1: a budget with estimated calls says the cost is estimated, with the explanation', () => {
    view({ ...BUDGET, estimated: 2 });
    const label = screen.getByTestId('run-cost-estimated');
    expect(label.textContent).toBe('estimated (stopped early)');
    expect(label.getAttribute('title')).toBe(HINT);
    const described = document.getElementById(label.getAttribute('aria-describedby') ?? '');
    expect(described?.textContent).toBe(HINT);
    expect(screen.getByRole('group', { name: 'Cost' }).textContent).toContain('Spent $0.42 of $5.00');
  });

  it('WP66-UI-2: no label when nothing is estimated, or from an older server without the count', () => {
    view({ ...BUDGET, estimated: 0 });
    expect(screen.queryByTestId('run-cost-estimated')).toBeNull();
    cleanup();
    view(BUDGET);
    expect(screen.queryByTestId('run-cost-estimated')).toBeNull();
    expect(document.body.textContent).not.toContain('estimated');
  });
});

describe('WP-66: the run summary', () => {
  const t = i18n.t.bind(i18n);

  it('WP66-UI-3: a partly estimated cost says so on its line', () => {
    expect(runSummaryLines({ ...DIGEST, costEstimated: true }, t).at(-1)).toBe(
      'Cost $0.04 · 12,300 tokens · estimated (stopped early)',
    );
  });

  it('WP66-UI-4: a reported cost, or one from an older server, is not called an estimate', () => {
    expect(runSummaryLines({ ...DIGEST, costEstimated: false }, t).at(-1)).toBe('Cost $0.04 · 12,300 tokens');
    expect(runSummaryLines(DIGEST, t).at(-1)).toBe('Cost $0.04 · 12,300 tokens');
  });
});
