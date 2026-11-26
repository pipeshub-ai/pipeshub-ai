import { z } from 'zod';
import { trimRun } from './trim-run';
import type { PipeshubRunSpecExtension } from '../contracts/generated/ext/pipeshub/run-spec';

/**
 * GIT-FLOW (WP-26): the GitHub repository a chat works on, as its owner chose it and the run pins it
 * (`x.pipeshub.repository`). v1 is GitHub only, with the person's own GitHub toolset as the credential.
 */
export type SessionRepository = Required<
  Pick<
    NonNullable<PipeshubRunSpecExtension['repository']>,
    'url' | 'toolsetInstanceId'
  >
> &
  Pick<NonNullable<PipeshubRunSpecExtension['repository']>, 'baseRef'>;

/** What the chip and the conversation header read (`GET …/repository`). */
export interface SessionRepositoryView {
  url: string;
  /** null: the repository's default branch. */
  baseRef: string | null;
  sessionBranch: string;
  /** The chat's workspace holds the clone (`repo/.git/HEAD` at its head): it can no longer change. */
  cloned: boolean;
  /** The pull request the chat's engine opened, as its run journaled it; absent: none yet. */
  pullRequest?: SessionPullRequest;
}

/** A pull request as GitHub answered its creation: `state` is the one it had then. */
export interface SessionPullRequest {
  number: number;
  url: string;
  state: string;
}

/** The toolset type of a GitHub toolset instance, as the toolset store names it. */
export const GITHUB_TOOLSET = 'github';

/** The grant that offers the engine its pull request tool (`open_pull_request` wraps it, WP-26 GT-04). */
export const GITHUB_PULL_REQUEST_TOOL = 'create_pull_request';

/** The folder the setup phase clones into (`GitCheckout.directory`): its git HEAD says the clone is there. */
export const REPOSITORY_HEAD_PATH = 'repo/.git/HEAD';

// The egress proxy's own rules (`harness_core/sandbox/egress/git.py` repository_ok, branch_ok): a name either side
// accepts, the other accepts too.
const SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$/;
const BRANCH_CHARS = /^[A-Za-z0-9._/-]{1,200}$/;
const SESSION_ID_UNSAFE = /[^A-Za-z0-9._-]+/g;
const SESSION_BRANCH_PREFIX = 'pipeshub/session-';

/** `owner/name` of a GitHub repository's https URL, or null for any other host or shape. */
export function githubRepository(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (
    parsed.protocol !== 'https:' ||
    parsed.hostname !== 'github.com' ||
    parsed.port !== '' ||
    parsed.username !== '' ||
    parsed.password !== '' ||
    parsed.search !== '' ||
    parsed.hash !== '' ||
    `https://github.com${parsed.pathname}` !== url
  ) {
    return null;
  }
  const parts = parsed.pathname.slice(1).split('/');
  if (
    parts.length !== 2 ||
    parts.some(
      (part) => !SEGMENT.test(part) || part === '.' || part === '..',
    ) ||
    (parts[1] as string).endsWith('.git')
  ) {
    return null;
  }
  return parts.join('/');
}

/** A branch name `git check-ref-format --branch` accepts, narrowed to plain characters. */
export function branchNameOk(branch: string): boolean {
  if (
    !BRANCH_CHARS.test(branch) ||
    /^[-/.]/.test(branch) ||
    /[/.]$/.test(branch) ||
    branch.endsWith('.lock')
  ) {
    return false;
  }
  return (
    !branch.includes('..') &&
    !branch.includes('//') &&
    !branch.includes('/.') &&
    branch !== 'HEAD'
  );
}

/** The one branch a session may push: its own (`git_checkout.session_branch`). */
export function sessionBranch(sessionId: string): string {
  const safe = trimRun(sessionId.replace(SESSION_ID_UNSAFE, '-'), '-.');
  return SESSION_BRANCH_PREFIX + (safe || 'session').slice(0, 80);
}

export const sameRepository = (
  a: SessionRepository,
  b: SessionRepository,
): boolean =>
  a.url === b.url &&
  (a.baseRef ?? null) === (b.baseRef ?? null) &&
  a.toolsetInstanceId === b.toolsetInstanceId;

/**
 * A chat's repository as `PUT …/repository` and a new chat's first message take it. The URL's host and shape are
 * the service's refusal (`REPOSITORY_NOT_GITHUB`), not a validation error. The instance id is one a tool grant can
 * name (`<source>@<instance>/<tool>`).
 */
export const sessionRepositorySchema = z
  .object({
    url: z.string().min(1).max(2048),
    baseRef: z
      .string()
      .min(1)
      .max(200)
      .refine(branchNameOk, 'baseRef is not a branch name')
      .optional(),
    toolsetInstanceId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/),
  })
  .strict();
