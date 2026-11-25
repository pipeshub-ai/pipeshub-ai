/**
 * Folds the journals given on stdin with Node's timeline and prints each run's summary (`fold-summary.ts`), so a
 * Python test can hold a journal it produced to the same fold on both sides (the long-task scenario's S1).
 *
 *   TS_NODE_PROJECT=tsconfig.test.json npx ts-node --transpile-only tests/contracts/support/fold-journal-cli.ts
 *
 * Reads {"journals": [{"runId": "...", "events": [<envelope>, ...]}]}; prints {"summaries": [<FoldSummary>, ...]}
 * in the same order.
 */
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { summary } from './fold-summary';

interface Input {
  journals: Array<{ runId: string; events: JournalEvent[] }>;
}

const chunks: Buffer[] = [];
process.stdin.on('data', (chunk: Buffer) => chunks.push(chunk));
process.stdin.on('end', () => {
  const input = JSON.parse(Buffer.concat(chunks).toString('utf-8')) as Input;
  const summaries = input.journals.map((j) => summary(foldTimeline(j.events, j.runId)));
  process.stdout.write(`${JSON.stringify({ summaries })}\n`);
});
