/**
 * What every QA-21 race asserts, shared by its suites: who won, what the
 * loser was told, and that the session's journal is a valid history.
 */
import { expect } from 'chai';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { transportError } from '../../../../src/modules/agent-harness/transport/errors';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { ContractBackend } from './execution-store-contract';
import { testCipher } from './data-protection';
import { journalViolations } from './journal-history';
import { RaceOutcome, ReleaseOrder, StoreRace } from './store-race';

/** A refusal's code (`ok` for a success), or the error's name and message when it has none. */
export const codeOf = (outcome: RaceOutcome<unknown>): string => {
  if (outcome.ok) return 'ok';
  const error = outcome.error as { code?: unknown; name?: string; message?: string };
  return typeof error.code === 'string' ? error.code : `${String(error.name)}: ${String(error.message)}`;
};

/** The HTTP status the transport answers the outcome with. */
export const statusOf = (outcome: RaceOutcome<unknown>): number | undefined =>
  outcome.ok ? 200 : (transportError(outcome.error) as { statusCode?: number }).statusCode;

export const byParty = <T>(outcomes: ReadonlyArray<RaceOutcome<T>>): Record<string, RaceOutcome<T>> =>
  Object.fromEntries(outcomes.map((outcome) => [outcome.party, outcome]));

export const winners = <T>(outcomes: ReadonlyArray<RaceOutcome<T>>): Array<RaceOutcome<T>> =>
  outcomes.filter((outcome) => outcome.ok);

/** Both scripted orders of a two-party race, then both at once. */
export const ordersOf = (a: string, b: string): ReleaseOrder[] => [[a, b], [b, a], 'together'];

export const partyNames = (prefix: string, count: number): string[] =>
  Array.from({ length: count }, (_, i) => `${prefix}${String(i)}`);

/** The race happened as scripted: every party was held at the seam. */
export function expectForced(
  race: StoreRace,
  parties: readonly string[],
  outcomes: ReadonlyArray<RaceOutcome<unknown>> | Record<string, RaceOutcome<unknown>>,
): void {
  const all = Array.isArray(outcomes) ? outcomes : Object.values(outcomes);
  expect([...race.held].sort(), `every racer held at the seam (${all.map(codeOf).join()})`).to.deep.equal(
    [...parties].sort(),
  );
}

/** The session's journal, claim-checked payloads read back, is a valid history. */
export async function expectValidHistory(
  backend: Pick<ContractBackend, 'journal' | 'payloads'>,
  run: Pick<HarnessRun, 'orgId' | 'sessionId'>,
  /** Only these runs (a seeded session's other events were written by hand). */
  runIds?: readonly string[],
): Promise<void> {
  const events = await new JournalService(backend.payloads, testCipher()).resolve(
    await backend.journal.readStream(run.orgId, run.sessionId, 0, 5_000),
  );
  expect(
    journalViolations(events, runIds === undefined ? undefined : new Set(runIds)),
    'journal history',
  ).to.deep.equal([]);
}
