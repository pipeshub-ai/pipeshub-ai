import type { JournalEvent } from '../domain/journal';
import type { SessionPullRequest } from '../domain/repository';
import type { ExecutionStore } from '../ports/execution-store';
import type { RunJournalReader } from './run-journal-reader';

/** shortcut: only the session's latest completed children are read; a pull request opened further back is not shown. */
export const PULL_REQUEST_CANDIDATES = 20;

const PROGRESS = 'x.pipeshub.engine.progress';
const OPEN_PULL_REQUEST = 'open_pull_request';
const PULL_URL = /^https:\/\/github\.com\/[^/\s"]+\/[^/\s"]+\/pull\/(\d+)$/;

export interface SessionPullRequestDeps {
  runs: Pick<ExecutionStore, 'completedRootChildren'>;
  journal: Pick<RunJournalReader, 'read'>;
}

const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

/** The created pull request in GitHub's answer: the object that carries a PR `html_url`, at any depth. */
function fromObject(value: unknown, depth = 0): SessionPullRequest | null {
  const object = record(value);
  if (object === null || depth > 4) return null;
  const url = object['html_url'];
  const number = object['number'];
  if (typeof url === 'string' && typeof number === 'number') {
    const match = PULL_URL.exec(url);
    if (match !== null && Number(match[1]) === number) {
      const state = object['state'];
      return { number, url, state: typeof state === 'string' ? state : 'open' };
    }
  }
  for (const nested of Object.values(object)) {
    const found = fromObject(nested, depth + 1);
    if (found !== null) return found;
  }
  return null;
}

/**
 * The pull request a gateway result names. The worker bounds a result's text, so a cut answer is read by its fields
 * (the first `html_url` of a pull, its number) when it is no longer JSON.
 */
export function pullRequestOf(result: string): SessionPullRequest | null {
  try {
    const parsed = fromObject(JSON.parse(result));
    if (parsed !== null) return parsed;
  } catch {
    // A bounded result is no longer JSON.
  }
  const url =
    /"html_url"\s*:\s*"(https:\/\/github\.com\/[^"\s]+\/pull\/(\d+))"/.exec(
      result,
    );
  if (url === null || !PULL_URL.test(url[1] as string)) return null;
  const state = /"state"\s*:\s*"([a-z]+)"/.exec(result);
  return {
    number: Number(url[2]),
    url: url[1] as string,
    state: state?.[1] ?? 'open',
  };
}

function lastOpened(
  events: readonly JournalEvent[],
): SessionPullRequest | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index] as JournalEvent;
    if (event.type !== PROGRESS) continue;
    const line = record(event.data);
    const data = record(line?.['data']);
    if (
      line?.['kind'] !== 'tool_result' ||
      typeof line['text'] !== 'string' ||
      !line['text'].endsWith(OPEN_PULL_REQUEST) ||
      data?.['status'] !== 'completed' ||
      typeof data['result'] !== 'string'
    ) {
      continue;
    }
    const opened = pullRequestOf(data['result']);
    if (opened !== null) return opened;
  }
  return null;
}

/**
 * GIT-FLOW (WP-26): the pull request a chat's engine opened, as its run journaled `open_pull_request`'s result (the
 * engine's progress line for the gateway call). The engine works in the chat's code_task children, each on its own
 * stream: the latest completed ones are read, newest first, and the latest pull request wins.
 */
export function sessionPullRequests(
  deps: SessionPullRequestDeps,
): (orgId: string, sessionId: string) => Promise<SessionPullRequest | null> {
  return async (orgId, sessionId) => {
    const children = await deps.runs.completedRootChildren(
      orgId,
      sessionId,
      PULL_REQUEST_CANDIDATES,
    );
    for (const child of children) {
      const opened = lastOpened(await deps.journal.read(orgId, child.runId));
      if (opened !== null) return opened;
    }
    return null;
  };
}
