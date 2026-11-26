/**
 * An engine run's repository work as plain timeline rows (WP-26 GIT-FLOW): the copy PipesHub made before the engine
 * started, each `git push` it ran and the pull request it opened. Read from the run's own steps, never shown as the
 * command's output or the tool's JSON.
 */
import type { SSEEngineProgressEvent } from './types';

/** Why the egress proxy refused a push, by the rule it applies to every push from a chat's sandbox. */
export type PushRefusal = 'otherBranch' | 'overwrite' | 'delete' | 'tags' | 'other';

export type RepoActivity =
  | { action: 'copied'; repository: string; branch: string | null }
  | { action: 'pushed'; branch: string }
  | { action: 'pushRefused'; target: string; reason: PushRefusal }
  | { action: 'prOpened'; repository: string | null; number: number | null; url: string | null }
  | { action: 'prFailed' };

/** The chat's copy of its repository, from the run's `repository` progress frame; null when the run has none. */
export function copiedRepository(frames: readonly SSEEngineProgressEvent[]): Extract<RepoActivity, { action: 'copied' }> | null {
  const frame = frames.find((f) => f.kind === 'repository' && f.text);
  if (!frame) return null;
  const branch = frame.data?.['branch'];
  return { action: 'copied', repository: frame.text, branch: typeof branch === 'string' && branch ? branch : null };
}

const GIT_PUSH = /(?:^|[;&|(]\s*|\s)git(?:\s+-C\s+\S+)?\s+push\b([^;&|]*)/;

/** The arguments of the `git push` a command line runs; null when it runs none. */
export function gitPushArgs(command: string): string[] | null {
  const match = GIT_PUSH.exec(command.trim());
  if (!match) return null;
  return match[1]
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.replace(/^['"]|['"]$/g, ''));
}

const shortRef = (ref: string): string => ref.replace(/^refs\/heads\//, '');

interface PushPlan {
  /** The branch it pushes to: the last refspec's destination; null for `git push` with none (the current branch). */
  target: string | null;
  overwrite: boolean;
  remove: boolean;
  tags: boolean;
}

function planOf(args: readonly string[]): PushPlan {
  const flags = args.filter((a) => a.startsWith('-'));
  const positional = args.filter((a) => !a.startsWith('-'));
  const refspecs = positional.slice(1);
  const has = (...names: string[]) => flags.some((f) => names.includes(f.split('=')[0]));
  const overwrite =
    has('--force', '--force-with-lease', '--force-if-includes') ||
    flags.some((f) => /^-[a-zA-Z]*f/.test(f) && !f.startsWith('--')) ||
    refspecs.some((r) => r.startsWith('+'));
  const remove = has('--delete', '-d', '--prune', '--mirror') || refspecs.some((r) => r.startsWith(':'));
  const tags = has('--tags', '--follow-tags') || refspecs.some((r) => r.includes('refs/tags/')) || positional.includes('tag');
  const last = refspecs.at(-1);
  const target = last ? shortRef(last.replace(/^\+/, '').split(':').at(-1) || last.replace(/^[+:]/, '')) : null;
  return { target: target && target !== 'HEAD' ? target : null, overwrite, remove, tags };
}

/** Why the proxy refused this push: its target first, then what it would have done there. */
export function pushRefusal(args: readonly string[], sessionBranch: string | null): PushRefusal {
  const plan = planOf(args);
  if (plan.target && sessionBranch && plan.target !== sessionBranch) return 'otherBranch';
  if (plan.overwrite) return 'overwrite';
  if (plan.remove) return 'delete';
  if (plan.tags) return 'tags';
  return 'other';
}

/** One ended `git push`: pushed to the chat's branch, or refused with the rule's reason. */
export function pushActivity(command: string, failed: boolean, sessionBranch: string | null): RepoActivity | null {
  const args = gitPushArgs(command);
  if (!args) return null;
  const target = planOf(args).target;
  if (!failed) return { action: 'pushed', branch: sessionBranch ?? target ?? '' };
  return { action: 'pushRefused', target: target ?? sessionBranch ?? '', reason: pushRefusal(args, sessionBranch) };
}

/** Whether a tool step is the GitHub toolset's `open_pull_request`, whatever its prefix. */
export function isOpenPullRequest(toolName: string): boolean {
  return /(^|[._\-/])open_pull_request$/.test(toolName) || toolName === 'open_pull_request';
}

const PULL_URL = /https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/pull\/(\d+)/;

/**
 * The pull request an ended `open_pull_request` call made, read from GitHub's answer however much of it survived: a
 * result cut short is still matched, never parsed whole.
 */
export function pullRequestActivity(result: string | undefined, failed: boolean, copiedRepo: string | null): RepoActivity {
  const text = result ?? '';
  if (failed || /"success"\s*:\s*false/.test(text)) return { action: 'prFailed' };
  const link = PULL_URL.exec(text);
  const numbered = /"number"\s*:\s*(\d+)/.exec(text);
  const number = link ? Number(link[2]) : numbered ? Number(numbered[1]) : null;
  if (text && number === null) return { action: 'prFailed' };
  return {
    action: 'prOpened',
    repository: link ? link[1] : copiedRepo,
    number,
    url: link ? link[0] : null,
  };
}
