import axios from 'axios';
import { NextFunction, Request, RequestHandler, Response } from 'express';
import { z } from 'zod/v3';
import {
  BadRequestError,
  HttpError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { isValidId } from '../../../libs/repositories/ids';
import {
  COMMAND_LOCAL,
  keepsTypedOnly,
  REGENERATE_QUERY_LOCAL,
  type RunCommand,
} from '../../agent-harness/domain/commands';
import type { IMessage } from '../types/conversation.interfaces';
import { getMessages, REGENERATE_TAIL_MESSAGES } from './utils';
import { runCommandSchema } from '../../agent-harness/transport/schemas';
import { identityString } from '../../agent-harness/transport/request-identity';
import type {
  PromptCommandRequest,
  PromptCommandResult,
} from '../../agent-harness/application/pipeshub-prompt-commands';

import { orgHarnessOn } from '../../agent-harness/config';

const logger = Logger.getInstance({ service: 'enterprise-search' });

/** The same grammar as `app/agent_loop_lib/commands/render.py` `parse_typed` (shared vectors). */
const TYPED = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/;

/** `/name rest of line` as its name and rest; null when the text is not a command invocation. */
export function parseTypedCommand(
  text: string,
): { name: string; args: string } | null {
  const match = TYPED.exec(text.trim());
  if (match === null) return null;
  return { name: match[1] as string, args: (match[2] ?? '').trim() };
}

const structuredSchema = z
  .object({
    name: z
      .string()
      .max(64)
      .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/),
    args: z
      .union([z.string().max(100_000), z.record(z.string().max(4000))])
      .optional(),
  })
  .strict();

const quote = (value: string): string =>
  /^[^\s"\\]+$/.test(value)
    ? value
    : `"${value.replace(/["\\]/g, (c) => `\\${c}`)}"`;

/** An API client's named arguments as the text a person would type (`name=value`, quoted when needed). */
export function argsText(
  args: string | Record<string, string> | undefined,
): string {
  if (args === undefined) return '';
  if (typeof args === 'string') return args.trim();
  return Object.entries(args)
    .map(([name, value]) => `${name}=${quote(value)}`)
    .join(' ');
}

export type Expansion =
  | {
      kind: 'expanded';
      version: string;
      contentDigest: string;
      owner: RunCommand['owner'];
      args: Record<string, string>;
      expanded: string;
      authorId?: string | null;
      authorFormer?: boolean;
      teamId?: string | null;
      files?: number;
    }
  | { kind: 'not_found' }
  /** The credential may not read the caller's skills (no `skill:read` scope, or skills are off). */
  | { kind: 'forbidden' }
  | { kind: 'refused'; code: string; message: string; names: string[] }
  | { kind: 'unavailable' };

const expandedSchema = z.object({
  version: z.string(),
  contentDigest: z.string(),
  owner: z.enum(['you', 'team', 'org', 'pipeshub']),
  args: z.record(z.string()),
  expanded: z.string().min(1),
  authorId: z.string().min(1).max(128).nullish(),
  authorFormer: z.boolean().optional(),
  teamId: z.string().min(1).max(128).nullish(),
});

export type ExpandCommand = (
  req: Request,
  name: string,
  args: string,
) => Promise<Expansion>;

/** Python renders the command as the caller (`POST /api/v1/skills/:name/expand`), so only their commands resolve. */
export const expandViaSkillsService =
  (config: { aiBackend: string }): ExpandCommand =>
  async (req, name, args) => {
    try {
      const response = await axios.post(
        `${config.aiBackend}/api/v1/skills/${encodeURIComponent(name)}/expand`,
        { args },
        {
          headers: {
            'Content-Type': 'application/json',
            ...(req.headers.authorization !== undefined
              ? { authorization: req.headers.authorization }
              : {}),
          },
          timeout: 10_000,
          validateStatus: () => true,
        },
      );
      const detail = (response.data as { detail?: unknown } | undefined)
        ?.detail as
        { code?: string; message?: string; names?: string[] } | undefined;
      if (response.status === 200) {
        const expanded = expandedSchema.safeParse(response.data);
        if (expanded.success) return { kind: 'expanded', ...expanded.data };
        logger.warn('Command expansion answered an unknown shape');
        return { kind: 'unavailable' };
      }
      if (response.status === 404) return { kind: 'not_found' };
      if (response.status === 401 || response.status === 403) {
        return { kind: 'forbidden' };
      }
      if (response.status === 422 && typeof detail?.code === 'string') {
        return {
          kind: 'refused',
          code: detail.code,
          message: detail.message ?? 'The command could not be expanded.',
          names: detail.names ?? [],
        };
      }
      logger.warn('Command expansion unavailable', { status: response.status });
      return { kind: 'unavailable' };
    } catch (error) {
      logger.warn('Command expansion unreachable', {
        message: (error as Error).message,
      });
      return { kind: 'unavailable' };
    }
  };

const refusal = (
  code: string,
  message: string,
  status: number,
  names: string[] = [],
): HttpError =>
  new HttpError(code.toUpperCase(), message, status, { code, names });

const orgIdOf = (req: Request): string | null =>
  identityString(
    (req as Request & { user?: Record<string, unknown> }).user?.['orgId'],
  );

/**
 * SLASH1 D2: a chat turn that is a /command is expanded here, before routing,
 * so the durable loop, every engine and legacy all receive the same text. The
 * typed `/name args` (or an API client's `command: {name, args}`) becomes the
 * turn's `query`; what produced it is left in `res.locals` for the turn's
 * record, never in the body a client controls. Typed text that names no
 * command of the caller's goes on as plain text, which no executor runs.
 */
export function expandChatCommand(
  expand: ExpandCommand,
  /** PipesHub's own prompt commands (SLASH2 `/review`, `/init`); null: not one of them. */
  builtin?: (
    req: Request,
    name: string,
    args: string,
  ) => Promise<Expansion | null>,
): RequestHandler {
  const handle = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const sent: unknown = req.body;
    if (sent === null || typeof sent !== 'object' || Array.isArray(sent)) {
      next();
      return;
    }
    const body = sent as Record<string, unknown>;
    const query = typeof body['query'] === 'string' ? body['query'] : '';
    let invocation: { name: string; args: string } | null;
    const explicit = body['command'] !== undefined;
    // Commands came with the agent harness: where it is off for the org, a message is sent as typed (ADR-OPS-08).
    // Asked only of a message that names a command, so a plain message reads no setting.
    if (
      (!explicit && parseTypedCommand(query) === null) ||
      !(await orgHarnessOn(orgIdOf(req)))
    ) {
      next();
      return;
    }
    if (explicit) {
      const parsed = structuredSchema.safeParse(body['command']);
      if (!parsed.success) {
        throw new BadRequestError('command must be { name, args }');
      }
      invocation = { name: parsed.data.name, args: argsText(parsed.data.args) };
    } else {
      invocation = parseTypedCommand(query);
    }
    if (invocation === null) {
      next();
      return;
    }
    const typed =
      invocation.args !== ''
        ? `/${invocation.name} ${invocation.args}`
        : `/${invocation.name}`;
    if (explicit && query.trim() !== '' && query.trim() !== typed) {
      throw new BadRequestError(
        'query must be empty or the command as typed when command is set',
      );
    }
    const result =
      (await builtin?.(req, invocation.name, invocation.args)) ??
      (await expand(req, invocation.name, invocation.args));
    if (result.kind === 'not_found') {
      if (!explicit) {
        next();
        return;
      }
      throw refusal(
        'command_not_found',
        `/${invocation.name} is not one of your commands.`,
        404,
      );
    }
    if (result.kind === 'refused') {
      throw refusal(result.code, result.message, 400, result.names);
    }
    if (result.kind === 'forbidden') {
      if (!explicit) {
        next();
        return;
      }
      throw refusal(
        'command_forbidden',
        'This credential cannot run commands: it needs the skill:read scope.',
        403,
      );
    }
    if (result.kind === 'unavailable') {
      if (!explicit) {
        next();
        return;
      }
      throw new ServiceUnavailableError('Commands are unavailable right now.');
    }
    const command = runCommandSchema.parse({
      name: invocation.name,
      version: result.version,
      contentDigest: result.contentDigest,
      args: result.args,
      typed,
      owner: result.owner,
      ...(typeof result.authorId === 'string'
        ? { authorId: result.authorId }
        : {}),
      ...(result.authorFormer === true ? { authorFormer: true } : {}),
      ...(typeof result.teamId === 'string' ? { teamId: result.teamId } : {}),
      ...(result.files !== undefined ? { files: result.files } : {}),
    });
    body['query'] = result.expanded;
    delete body['command'];
    res.locals[COMMAND_LOCAL] = command;
    next();
  };
  return (req, res, next) => {
    handle(req, res, next).catch(next);
  };
}

/** The command of the question a regenerate asks again, when its message keeps only what was typed. */
export type StoredCommandReader = (
  req: Request,
) => Promise<RunCommand | undefined>;

/**
 * LC2FIX: a `/review` message keeps only what was typed, so regenerating its
 * answer expands the command again, as the caller, from the workspace as it is
 * now; the text reaches the run's sealed input (or legacy's request) and never
 * the message store. A refusal (no changes left to review) answers before
 * anything is removed.
 */
export function regenerateChatCommand(
  stored: StoredCommandReader,
  builtin: (
    req: Request,
    name: string,
    args: string,
  ) => Promise<Expansion | null>,
): RequestHandler {
  const handle = async (req: Request, res: Response): Promise<void> => {
    const command = await stored(req);
    if (command === undefined || !keepsTypedOnly(command)) return;
    const typed = parseTypedCommand(command.typed);
    const result = await builtin(req, command.name, typed?.args ?? '');
    if (result?.kind === 'refused') {
      throw refusal(result.code, result.message, 400, result.names);
    }
    if (result?.kind !== 'expanded') {
      throw new ServiceUnavailableError('Commands are unavailable right now.');
    }
    res.locals[REGENERATE_QUERY_LOCAL] = result.expanded;
  };
  return (req, res, next) => {
    handle(req, res).then(() => {
      next();
    }, next);
  };
}

/** The regenerated question's fresh expansion, when the regenerate route made one. */
export const regenerateQuery = (res: Response): string | undefined => {
  const value = (res.locals as Record<string, unknown> | undefined)?.[
    REGENERATE_QUERY_LOCAL
  ];
  return typeof value === 'string' ? value : undefined;
};

/** The command of the question `messageId` answers, in the caller's org, read for `regenerateChatCommand`. */
export const storedRegenerateCommand: StoredCommandReader = async (req) => {
  const user = (req as Request & { user?: Record<string, unknown> }).user;
  const orgId = identityString(user?.['orgId']);
  const sessionId = req.params['conversationId'];
  const messageId = req.params['messageId'];
  if (orgId === null || !isValidId(sessionId ?? '')) {
    return undefined;
  }
  // The tail regenerate itself reads: only the last answer can be regenerated.
  const tail = (await getMessages(sessionId as string, {
    orgId,
    limit: REGENERATE_TAIL_MESSAGES,
    sort: -1,
  })) as Array<IMessage & { _id?: unknown }>;
  const answerAt = tail.findIndex((m) => String(m._id) === messageId);
  const answer = tail[answerAt];
  if (answer === undefined) return undefined;
  return tail.slice(answerAt + 1).find((m) => m.messageType === 'user_query')
    ?.command;
};

/** The command this turn was expanded from, for the legacy handler's record of the message. */
export const expandedChatCommand = (res: Response): RunCommand | undefined =>
  // A handler test may pass a bare response; Express always sets `locals`.
  (res.locals as Record<string, unknown> | undefined)?.[COMMAND_LOCAL] as
    RunCommand | undefined;

/** PipesHub's prompt commands for a chat route: the caller from the verified credential, the session from the path. */
export const pipeshubCommandsFor =
  (
    expand: (
      request: PromptCommandRequest,
    ) => Promise<PromptCommandResult | null>,
  ) =>
  async (
    req: Request,
    name: string,
    args: string,
  ): Promise<Expansion | null> => {
    const user = (req as Request & { user?: Record<string, unknown> }).user;
    const orgId = identityString(user?.['orgId']);
    const actorId = identityString(user?.['userId']);
    if (orgId === null || actorId === null) return null;
    const sessionId = req.params['conversationId'];
    const agentKey = req.params['agentKey'];
    return expand({
      name,
      args,
      scope: {
        orgId,
        actorId,
        ...(sessionId !== undefined ? { sessionId } : {}),
        ...(agentKey !== undefined ? { agentKey } : {}),
      },
    });
  };
