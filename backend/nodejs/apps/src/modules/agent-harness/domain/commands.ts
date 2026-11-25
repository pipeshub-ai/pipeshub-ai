import { createHash } from 'node:crypto';
import type { JournalEventInput } from './journal';

/** Whose command it is, as the composer's / menu shows it (SLASH1 D4). */
export type CommandOwner = 'you' | 'team' | 'org' | 'pipeshub';

/**
 * The /command a turn's query was expanded from (SLASH1 D2). The query is
 * already the expanded text; this is the record of what produced it, set by
 * the server's expansion and never taken from a request body.
 */
export interface RunCommand {
  name: string;
  version: string;
  contentDigest: string;
  args: Record<string, string>;
  /** What the user typed: `/name args`. */
  typed: string;
  owner: CommandOwner;
  /**
   * A team or org command's author and team as ids (SLASH1 S4): names are read
   * when shown, so an erased author is never named in someone else's history.
   * The stored message keeps them; the worker never receives them (`workerCommand`).
   */
  authorId?: string;
  /** Its author had left the org when it was sent: the command passed to the org (O3). */
  authorFormer?: boolean;
  teamId?: string;
  /** `/review`: how many files its diff named, for the chip the stored message shows in place of the diff (LC2FIX). */
  files?: number;
}

/**
 * Whether the message store keeps only what was typed (LC2FIX): `/review`'s
 * expansion is the workspace's diff, which is sealed everywhere else, so only
 * the sealed run input and the journal carry it. Every other command expands
 * to its body and the typed arguments alone (SLASH1 D3, `/init`), which the
 * message keeps for "What was sent".
 */
export const keepsTypedOnly = (command: RunCommand | undefined): boolean =>
  command?.owner === 'pipeshub' && command.name === 'review';

/** A user message's stored content: the text sent, or what was typed when the expansion must not be kept. */
export const storedQuestion = (query: string, command?: RunCommand): string =>
  command !== undefined && keepsTypedOnly(command) ? command.typed : query;

/**
 * The command as a run's input carries it to the worker: S1's fields only.
 * Who wrote it is display data the stored message keeps, and a worker built
 * before S4 refuses a command with fields it does not know.
 */
export function workerCommand(command: RunCommand): RunCommand {
  const {
    authorId: _a,
    authorFormer: _f,
    teamId: _t,
    files: _n,
    ...worker
  } = command;
  return worker;
}

/** `x.pipeshub.command.expanded`: written with run.admitted, so every executor's run starts from the same record. */
export function commandExpandedEvent(
  command: RunCommand,
  expanded: string,
): JournalEventInput {
  return {
    type: 'x.pipeshub.command.expanded',
    schemaVersion: 1,
    critical: false,
    visibility: 'owner',
    data: {
      name: command.name,
      version: command.version,
      contentDigest: command.contentDigest,
      args: command.args,
      expandedDigest: `sha256:${createHash('sha256').update(expanded, 'utf8').digest('hex')}`,
      expandedChars: expanded.length,
      owner: command.owner,
    },
  };
}

/** Where the chat routes' expansion leaves the turn's command (`res.locals`), out of the client's reach. */
export const COMMAND_LOCAL = 'expandedCommand';

/** Where a regenerate route leaves a stored `/review`'s fresh expansion (`res.locals`, LC2FIX), out of the client's reach. */
export const REGENERATE_QUERY_LOCAL = 'regenerateQuery';
