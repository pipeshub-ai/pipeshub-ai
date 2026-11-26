import { createHash } from 'node:crypto';
import { CHAT_QUERY_MAX_CHARS } from '../domain/admission';
import { HarnessNotFoundError } from '../domain/errors';
import { WorkspaceError } from '../domain/workspace';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import {
  SessionWorkspaceService,
  WorkspaceDiff,
} from './session-workspace-service';

/**
 * PipesHub's own prompt commands (SLASH2 C3 `/review`, C4 `/init`). Like a
 * SLASH1 command they become the turn's text on the server, before routing,
 * so the loop and every engine receive the same text and no `/x` reaches an
 * executor (S0). Their bodies are here, not in the skills store: a user can
 * neither see nor shadow them, and `contracts/harness/vectors/command-expansion.json`
 * holds the same bodies so the Python and TypeScript renderers agree.
 */

export const PIPESHUB_PROMPT_VERSION = '1.0.0';

export const REVIEW_BODY =
  "Review the changes in this chat's workspace, shown below as a unified diff. List bugs, risks and anything unclear, most important first; for each, name the file and line and say what to change. Say so plainly if you find nothing to fix. Do not change any file.";

export const INIT_BODY =
  'Draft instructions for this agent from our conversation so far, and from the files in its workspace if it has any: what it is for, the sources and tools it should use, how its answers should look, and what it must not do. Write them to the agent, in the second person, in at most 300 words. Reply with only the instructions, in one fenced block that starts with ```text, and nothing before or after it.';

export type PromptCommandResult =
  | {
      kind: 'expanded';
      version: string;
      contentDigest: string;
      owner: 'pipeshub';
      args: Record<string, string>;
      expanded: string;
      files?: number;
    }
  | { kind: 'refused'; code: string; message: string; names: string[] };

const digest = (body: string): string =>
  `sha256:${createHash('sha256').update(body, 'utf8').digest('hex')}`;

/** SLASH1's rule for a body with no placeholder: the arguments follow it after a blank line. */
export const withArgs = (body: string, args: string): string =>
  args === '' ? body : `${body}\n\n${args}`;

const notApplicable = (message: string): PromptCommandResult => ({
  kind: 'refused',
  code: 'command_not_applicable',
  message,
  names: [],
});

/** The diff a `/review` turn carries, cut to fit the turn's query limit. */
export function reviewText(focus: string, diff: WorkspaceDiff): string {
  const head = withArgs(REVIEW_BODY, focus);
  const count = diff.files.length + diff.omitted;
  const summary = `${String(count)} ${count === 1 ? 'file' : 'files'} changed since this chat began, +${String(diff.additions)} −${String(diff.deletions)}.`;
  const parts = diff.files.map(
    (file) =>
      file.diff ??
      `${file.change} ${file.path}${file.binary ? ' (binary)' : file.tooLarge ? ' (too large to show)' : ''}\n`,
  );
  const cut = '\n[The diff was cut here to fit one message.]\n';
  let body = parts.join('');
  const room = Math.max(
    0,
    CHAT_QUERY_MAX_CHARS - head.length - summary.length - 16 - cut.length,
  );
  if (body.length > room) body = `${body.slice(0, room)}${cut}`;
  return `${head}\n\n${summary}\n\n\`\`\`diff\n${body}\`\`\``;
}

export interface PromptCommandRequest {
  name: string;
  args: string;
  /** The caller, from the verified credential; the session from the route. */
  scope: Omit<SessionExecutionScope, 'sessionId'> & { sessionId?: string };
}

/**
 * Expands `/review` and `/init`; null for any other name (a SLASH1 command or
 * plain text). `/review` attaches the whole chat's diff and refuses when
 * there is none; `/init` applies only in a saved agent chat.
 */
export function pipeshubPromptCommands(
  workspaces: () => Pick<SessionWorkspaceService, 'diff'> | undefined,
): (request: PromptCommandRequest) => Promise<PromptCommandResult | null> {
  return async ({ name, args, scope }) => {
    if (name === 'init') {
      if (scope.agentKey === undefined || scope.sessionId === undefined) {
        return notApplicable(
          "/init drafts an agent's instructions: use it in a conversation with an agent you can edit.",
        );
      }
      return {
        kind: 'expanded',
        version: PIPESHUB_PROMPT_VERSION,
        contentDigest: digest(INIT_BODY),
        owner: 'pipeshub',
        args: {},
        expanded: withArgs(INIT_BODY, args),
      };
    }
    if (name !== 'review') return null;
    const service = workspaces();
    const sessionId = scope.sessionId;
    if (service === undefined || sessionId === undefined) {
      return notApplicable('This chat has no workspace yet.');
    }
    let diff: WorkspaceDiff;
    try {
      diff = await service.diff({ ...scope, sessionId }, 'chat');
    } catch (error) {
      if (error instanceof HarnessNotFoundError)
        return notApplicable('This chat has no workspace yet.');
      if (error instanceof WorkspaceError) return notApplicable(error.message);
      throw error;
    }
    if (diff.files.length === 0) {
      return notApplicable("This chat's workspace has no changes to review.");
    }
    return {
      kind: 'expanded',
      version: PIPESHUB_PROMPT_VERSION,
      contentDigest: digest(REVIEW_BODY),
      owner: 'pipeshub',
      args: args === '' ? {} : { focus: args.slice(0, 4000) },
      expanded: reviewText(args, diff),
      files: diff.files.length + diff.omitted,
    };
  };
}
