'use client';

/**
 * What a tool step's row says after its action (WP-112): which page it read, what it searched for, which pattern or
 * file, what came of it, and the answer's citations it produced. Built from the step's typed facts
 * (`app/harness/step_display.py`), the same for the loop's steps and every engine's.
 */

import React from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import type { ToolStepFacts } from '../../types';
import type { CitationMaps } from './response-tabs/citations';

const SEP = ' · ';

const withoutFragment = (url: string): string => url.split('#', 1)[0] ?? url;

/** The answer's citation numbers whose link is one this step returned, in order. */
export function citedNumbers(step: ToolStepFacts, citationMaps: CitationMaps | undefined): number[] {
  if (!citationMaps || !step.sources?.length) return [];
  const sources = new Set(step.sources.map(withoutFragment));
  const numbers = new Set<number>();
  for (const citation of Object.values(citationMaps.citations)) {
    if (citation.webUrl && sources.has(withoutFragment(citation.webUrl))) numbers.add(citation.chunkIndex);
  }
  return [...numbers].sort((a, b) => a - b);
}

/** What came of the step in a few words: a search's hits, the files a find listed; none when nothing is known. */
export function stepOutcome(step: ToolStepFacts, t: TFunction): string | null {
  if (step.count === undefined) return null;
  switch (step.action) {
    case 'webSearch':
      return step.count === 0 ? t('agentActivity.step.noResults') : t('agentActivity.step.results', { count: step.count });
    case 'findFiles':
    case 'searchFiles':
    case 'listFiles':
      return t('agentActivity.step.files', { count: step.count });
    default:
      return null;
  }
}

/** The target as plain text (a summary or an accessible name): the page's title or host, the query, the path. */
export function stepTargetText(step: ToolStepFacts): string | null {
  if (step.action === 'readPage') return step.title ?? step.target ?? null;
  if (!step.target) return null;
  return step.action === 'webSearch' ? `“${step.target}”` : step.target;
}

const MUTED: React.CSSProperties = { color: 'var(--slate-11)', fontWeight: 400 };
const CODE: React.CSSProperties = { fontFamily: 'monospace', fontSize: '0.95em' };

/** The page a step read, as a link that opens it in a new tab; a click on it never toggles the row it sits in. */
export function StepLink({ step, text, testId }: { step: ToolStepFacts; text: string; testId?: string }) {
  if (!step.href) return <>{text}</>;
  return (
    <a
      href={step.href}
      target="_blank"
      rel="noopener noreferrer"
      title={step.href}
      data-testid={testId}
      onClick={(event) => event.stopPropagation()}
      style={{ color: 'var(--accent-11)' }}
    >
      {text}
    </a>
  );
}

/**
 * The row's target, outcome and citations, after its action label. A row that words the outcome on its own line and
 * is itself a button (the Activity panel's) passes `withOutcome` and `linked` false: a link cannot sit in a button,
 * so its body carries the page's link.
 */
export function StepTarget({
  step,
  citationMaps,
  withOutcome = true,
  linked = true,
}: {
  step: ToolStepFacts;
  citationMaps?: CitationMaps;
  withOutcome?: boolean;
  linked?: boolean;
}) {
  const { t } = useTranslation();
  const text = stepTargetText(step);
  const outcome = withOutcome ? stepOutcome(step, t) : null;
  const cited = citedNumbers(step, citationMaps);
  if (!text && !outcome && cited.length === 0) return null;
  const code = step.action === 'findFiles' || step.action === 'searchFiles' || step.action === 'readFile' || step.action === 'listFiles';
  return (
    <span data-testid="step-target" style={MUTED}>
      {text && SEP}
      {text &&
        (linked && step.action === 'readPage' && step.href ? (
          <StepLink step={step} text={text} />
        ) : (
          <span style={code ? CODE : undefined}>{text}</span>
        ))}
      {outcome && `${SEP}${outcome}`}
      {cited.length > 0 && `${SEP}${t('agentActivity.step.cited', { refs: cited.map((n) => `[${n}]`).join(' ') })}`}
    </span>
  );
}
