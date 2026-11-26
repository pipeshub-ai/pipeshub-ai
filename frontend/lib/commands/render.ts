/**
 * The composer's preview of a /command (SLASH1 D3): a port of
 * `backend/python/app/agent_loop_lib/commands/render.py`. Both pass
 * `contracts/harness/vectors/command-expansion.json`; the server's expansion
 * is the one that is sent.
 */

import { BUILTIN_COMMANDS } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';

export interface CommandArgument {
  name: string;
  description?: string;
  required?: boolean;
  enum?: string[];
  default?: string;
  maxLength?: number;
}

export type CommandErrorCode =
  | 'command_argument_missing'
  | 'command_argument_invalid'
  | 'command_too_long';

export type RenderResult =
  | { ok: true; text: string; values: Record<string, string> }
  | { ok: false; code: CommandErrorCode; names: string[] };

/** Names a user command may never take (SLASH2 §2.4): every built-in, its aliases, and the reserved words. */
export const RESERVED_COMMAND_NAMES: ReadonlySet<string> = new Set([
  ...BUILTIN_COMMANDS.commands.flatMap((command) => [command.name, ...command.aliases]),
  ...BUILTIN_COMMANDS.reservedWords.map((word) => word.name),
]);
/** Lengths count UTF-16 code units (`String.length`), as the chat routes' query limit does. */
export const EXPANDED_MAX_UNITS = 100_000;
export const ARGUMENT_MAX_UNITS = 4_000;
export const ARGUMENT_NAME_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;
export const COMMAND_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const TYPED = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/;
const TOKEN = /(?:([a-z][a-z0-9_]*)=)?(?:"((?:[^"\\]|\\[\s\S])*)"|(\S+))/g;
const ESCAPE = /\\(["\\])/g;
const PLACEHOLDER = /\$(ARGUMENTS|[1-9]|[a-z][a-z0-9_]*)/g;

/** `/name rest of line` as its name and rest; null when the text is not a command invocation. */
export function parseTyped(text: string): { name: string; args: string } | null {
  const match = TYPED.exec(text.trim());
  if (!match) return null;
  return { name: match[1], args: (match[2] ?? '').trim() };
}

function tokens(raw: string, declared: Set<string>): { named: Record<string, string>; positional: string[] } {
  const named: Record<string, string> = {};
  const positional: string[] = [];
  for (const match of raw.matchAll(TOKEN)) {
    const [whole, key, quoted, bare] = match;
    const value = quoted !== undefined ? quoted.replace(ESCAPE, '$1') : bare;
    if (key !== undefined && declared.has(key)) named[key] = value;
    else if (key !== undefined) positional.push(whole);
    else positional.push(value);
  }
  return { named, positional };
}

export function renderCommand(body: string, args: CommandArgument[], rawArgs = ''): RenderResult {
  const raw = rawArgs.trim();
  if (raw.length > EXPANDED_MAX_UNITS) return { ok: false, code: 'command_too_long', names: [] };
  const declared = new Map(args.map((a) => [a.name, a]));
  const { named, positional } = tokens(raw, new Set(declared.keys()));

  const unfilled = args.filter((a) => !(a.name in named)).map((a) => a.name);
  const values: Record<string, string> = { ...named };
  unfilled.forEach((name, index) => {
    if (index >= positional.length) return;
    // The last unfilled argument takes every remaining word.
    values[name] = index === unfilled.length - 1 ? positional.slice(index).join(' ') : positional[index];
  });

  const missing: string[] = [];
  for (const arg of args) {
    if (!(arg.name in values) && arg.default !== undefined) values[arg.name] = arg.default;
    if (!(arg.name in values) || values[arg.name] === '') {
      if (arg.required) missing.push(arg.name);
      values[arg.name] ??= '';
    }
  }
  if (missing.length > 0) return { ok: false, code: 'command_argument_missing', names: missing };
  for (const arg of args) {
    const value = values[arg.name];
    if (value && arg.enum && !arg.enum.includes(value)) {
      return { ok: false, code: 'command_argument_invalid', names: [arg.name] };
    }
    if (value.length > Math.min(arg.maxLength ?? ARGUMENT_MAX_UNITS, ARGUMENT_MAX_UNITS)) {
      return { ok: false, code: 'command_argument_invalid', names: [arg.name] };
    }
  }

  let used = false;
  let text = body.replace(PLACEHOLDER, (whole: string, key: string) => {
    if (key === 'ARGUMENTS') {
      used = true;
      return raw;
    }
    if (/^[1-9]$/.test(key)) {
      used = true;
      return positional[Number(key) - 1] ?? '';
    }
    if (declared.has(key)) {
      used = true;
      return values[key];
    }
    return whole;
  });
  if (!used && raw) text = `${body}\n\n${raw}`;
  if (text.length > EXPANDED_MAX_UNITS) return { ok: false, code: 'command_too_long', names: [] };
  return { ok: true, text, values };
}

/** The argument hint the menu shows: `incident · depth=quick|full`. */
export function argumentHint(args: CommandArgument[]): string {
  return args
    .map((a) => {
      const name = a.required ? a.name : `[${a.name}]`;
      return a.enum ? `${a.name}=${a.enum.join('|')}` : name;
    })
    .join(' · ');
}
