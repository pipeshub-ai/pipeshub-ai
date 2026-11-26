/**
 * QA-AUTO support: where the QA specs write what scripts/qa_full.py reads (latency samples, UX gallery captures),
 * and the latency budgets they are held to.
 *
 * On the stack, deployment/e2e/run.sh sets PXH_QA_RESULTS to a folder of the run (/work/pw/qa); anywhere else the
 * files go under the test's own output folder.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { TestInfo } from '@playwright/test';

export function resultsDir(info: TestInfo, ...sub: string[]): string {
  const dir = path.join(process.env.PXH_QA_RESULTS || info.outputDir, ...sub);
  mkdirSync(dir, { recursive: true });
  return dir;
}

export interface Budget {
  title: string;
  lane: string;
  p50_ms: number;
  p95_ms: number;
  samples: number;
}

export interface Budgets {
  metrics: Record<string, Budget>;
}

export const BUDGETS: Budgets = JSON.parse(readFileSync(path.join(__dirname, 'latency-budgets.json'), 'utf8')) as Budgets;

/** Nearest-rank percentile, as scripts/qa_full.py computes it (`percentile`). */
export function percentile(values: number[], q: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.max(1, Math.ceil((Math.round(q * 100) * sorted.length) / 100));
  return sorted[Math.min(rank, sorted.length) - 1];
}

/** Writes one spec's samples for the report, and returns each metric's p50 and p95. */
export function recordSamples(info: TestInfo, name: string, samples: Record<string, number[]>): Record<string, { p50: number; p95: number }> {
  writeFileSync(path.join(resultsDir(info), `latency-${name}.json`), `${JSON.stringify({ samples }, null, 1)}\n`);
  return Object.fromEntries(Object.entries(samples).map(([metric, values]) => [metric, { p50: percentile(values, 0.5), p95: percentile(values, 0.95) }]));
}
