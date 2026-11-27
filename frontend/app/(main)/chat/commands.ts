/**
 * /commands in the chat composer (SLASH1 D6). A command is a skill marked
 * "Usable as a /command"; the server expands it, so what this module renders
 * is a preview and the server's record is what the sent message shows.
 */
import { create } from 'zustand';
import { apiClient } from '@/lib/api';
import {
  RESERVED_COMMAND_NAMES,
  parseTyped,
  renderCommand,
  type CommandArgument,
  type RenderResult,
} from '@/lib/commands/render';
import {
  BUILTIN_FIRST_PAGE,
  builtinGroup,
  findBuiltin,
  isTerminalWord,
  matchBuiltins,
  type BuiltinCommand,
  type BuiltinReason,
} from './builtin-commands';

export type CommandOwner = 'you' | 'team' | 'org' | 'pipeshub';

/** Whose text a command is; author and team only for a shared (team or org) one (SLASH1 S4). */
export interface CommandProvenance {
  owner: CommandOwner;
  author?: string;
  /** The author left the organization; the command passed to it (O3). */
  authorFormer?: boolean;
  team?: string;
}

export interface ChatCommand extends CommandProvenance {
  name: string;
  description: string;
  version: string;
  arguments: CommandArgument[];
}

/**
 * What a sent message records of the command it was expanded from (`IMessage.command`).
 * A shared command's author and team are ids; their names are read when shown (S4).
 */
export interface SentCommand {
  name: string;
  version: string;
  args: Record<string, string>;
  typed: string;
  owner: CommandOwner;
  authorId?: string;
  authorFormer?: boolean;
  teamId?: string;
  /** `/review`: how many files its diff named (LC2FIX). */
  files?: number;
  /**
   * The text that was sent: the stored message's content. Absent while the turn is still being admitted,
   * and for `/review`, whose message keeps only what was typed (LC2FIX).
   */
  expanded?: string;
}

const OWNERS: ReadonlySet<string> = new Set(['you', 'team', 'org', 'pipeshub']);
const ownerOf = (value: unknown): CommandOwner =>
  typeof value === 'string' && OWNERS.has(value) ? (value as CommandOwner) : 'you';

const nonEmpty = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() !== '' ? value : undefined;

function provenanceOf(v: Record<string, unknown>): CommandProvenance {
  const author = nonEmpty(v.author);
  const team = nonEmpty(v.team);
  return {
    owner: ownerOf(v.owner),
    ...(author ? { author } : {}),
    ...(v.authorFormer === true ? { authorFormer: true } : {}),
    ...(team ? { team } : {}),
  };
}

function toCommand(value: unknown): ChatCommand | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.name !== 'string') return null;
  return {
    name: v.name,
    description: typeof v.description === 'string' ? v.description : '',
    version: typeof v.version === 'string' ? v.version : '',
    ...provenanceOf(v),
    arguments: Array.isArray(v.arguments) ? (v.arguments as CommandArgument[]) : [],
  };
}

/** A stored message's `command`, or null when it was not sent as one. */
export function toSentCommand(value: unknown, expanded?: string): SentCommand | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.name !== 'string' || typeof v.typed !== 'string') return null;
  const args: Record<string, string> = {};
  if (v.args && typeof v.args === 'object') {
    for (const [key, arg] of Object.entries(v.args as Record<string, unknown>)) {
      if (typeof arg === 'string') args[key] = arg;
    }
  }
  return {
    name: v.name,
    version: typeof v.version === 'string' ? v.version : '',
    args,
    typed: v.typed,
    owner: ownerOf(v.owner),
    ...(nonEmpty(v.authorId) ? { authorId: v.authorId as string } : {}),
    ...(v.authorFormer === true ? { authorFormer: true } : {}),
    ...(nonEmpty(v.teamId) ? { teamId: v.teamId as string } : {}),
    ...(typeof v.files === 'number' ? { files: v.files } : {}),
    ...(expanded !== undefined && expanded !== v.typed
      ? { expanded }
      : typeof v.expanded === 'string'
        ? { expanded: v.expanded }
        : {}),
  };
}

export const CommandsApi = {
  /** The caller's commands (before CPD-10's effective set reaches clients). */
  async list(): Promise<ChatCommand[]> {
    const { data } = await apiClient.get('/api/v1/skills', { params: { invocation: 'user' } });
    return (Array.isArray(data?.skills) ? data.skills : []).map(toCommand).filter(Boolean) as ChatCommand[];
  },
  /** The names a sent shared command shows, read now: a message keeps only ids (S4). */
  async provenance(command: SentCommand): Promise<CommandProvenance> {
    const { data } = await apiClient.get('/api/v1/skills/commands/provenance', {
      params: {
        ...(command.authorId ? { authorId: command.authorId } : {}),
        ...(command.teamId ? { teamId: command.teamId } : {}),
      },
    });
    return provenanceOf({ ...data, owner: command.owner });
  },
  async body(name: string): Promise<string> {
    const { data } = await apiClient.get(`/api/v1/skills/${encodeURIComponent(name)}`);
    return typeof data?.body === 'string' ? data.body : '';
  },
};

const CATALOG_TTL_MS = 60_000;

interface CommandCatalog {
  commands: ChatCommand[];
  loadedAt: number;
  bodies: Record<string, string>;
  /** Fetches the menu's list unless it is fresh; a failure leaves the menu empty, so `/` stays plain text. */
  load: () => Promise<void>;
  loadBody: (name: string) => Promise<void>;
  /** A command was just saved: the next `/` lists it. */
  invalidate: () => void;
}

export const useCommandCatalog = create<CommandCatalog>((set, get) => ({
  commands: [],
  loadedAt: 0,
  bodies: {},
  load: async () => {
    if (Date.now() - get().loadedAt < CATALOG_TTL_MS) return;
    set({ loadedAt: Date.now() });
    try {
      set({ commands: await CommandsApi.list(), bodies: {} });
    } catch {
      set({ commands: [] });
    }
  },
  loadBody: async (name) => {
    if (name in get().bodies) return;
    try {
      const body = await CommandsApi.body(name);
      set((s) => ({ bodies: { ...s.bodies, [name]: body } }));
    } catch {
      // The preview stays unavailable; the server still expands on send.
    }
  },
  invalidate: () => set({ loadedAt: 0 }),
}));

/** A text the composer opens a new chat with ("Try it"), taken once by the composer. */
interface ComposerPrefill {
  prefill: { text: string; key: number } | null;
  request: (text: string) => void;
}

export const useComposerPrefill = create<ComposerPrefill>((set) => ({
  prefill: null,
  request: (text) => set({ prefill: { text, key: Date.now() } }),
}));

/**
 * The menu's groups, in order (SLASH2 §2.4, SLASH3 §3.9): PipesHub's own first, then its Sandbox commands, then
 * the caller's, the team's, the org's.
 */
export type CommandGroup = 'builtin' | 'sandbox' | 'you' | 'team' | 'org';

export type MenuEntry =
  | { kind: 'builtin'; id: string; group: 'builtin' | 'sandbox'; command: BuiltinCommand; alias?: string; reason: BuiltinReason | null }
  | { kind: 'user'; id: string; group: CommandGroup; command: ChatCommand }
  | { kind: 'more'; id: string; group: 'builtin'; count: number };

/**
 * Where the composer is in a /command: picking its name, filling a user
 * command's arguments, a built-in, or a word PipesHub has no command for.
 */
export type ComposerCommandState =
  | { phase: 'none' }
  | { phase: 'menu'; query: string; entries: MenuEntry[] }
  | { phase: 'args'; command: ChatCommand; args: string; preview: RenderResult | null }
  /** `typed`: the name as typed, an alias (`/env`) or the command's own. */
  | { phase: 'builtin'; command: BuiltinCommand; typed: string; args: string; reason: BuiltinReason | null }
  | { phase: 'word'; word: string };

export interface ComposerCommandOptions {
  /** Why a built-in does not apply here; absent: every built-in applies. */
  reason?: (command: BuiltinCommand) => BuiltinReason | null;
  /** "More built-ins" was chosen: list them all while the query is empty. */
  showAll?: boolean;
}

const groupOf = (owner: CommandOwner): CommandGroup => (owner === 'pipeshub' ? 'builtin' : owner);
const GROUP_ORDER: readonly CommandGroup[] = ['builtin', 'sandbox', 'you', 'team', 'org'];

/**
 * The composer's text as a /command. Only text that starts with `/` is one.
 * A built-in's name or alias wins over a user command of the same name (O2);
 * a name that matches nothing is plain text (SLASH1 D6).
 */
export function composerCommandState(
  text: string,
  commands: ChatCommand[],
  bodies: Record<string, string>,
  options: ComposerCommandOptions = {},
): ComposerCommandState {
  if (!text.startsWith('/')) return { phase: 'none' };
  const reason = options.reason ?? (() => null);
  // A user command under a reserved name is hidden by the built-in; the skills editor says so (O2).
  const usable = commands.filter((c) => !RESERVED_COMMAND_NAMES.has(c.name));
  const naming = /^\/([a-z0-9-]*)$/.exec(text);
  if (naming) {
    const query = naming[1];
    const matches = matchBuiltins(query);
    const builtins = matches.filter((m) => builtinGroup(m.command) === 'builtin');
    // The Sandbox group is listed whole: it never counts toward Built-in's first page (SLASH3 §3.9).
    const sandbox = matches.filter((m) => builtinGroup(m.command) === 'sandbox');
    const shown = query === '' && !options.showAll ? builtins.slice(0, BUILTIN_FIRST_PAGE) : builtins;
    const entryOf = ({ command, alias }: (typeof matches)[number]): MenuEntry => ({
      kind: 'builtin',
      id: `builtin-${command.name}`,
      group: builtinGroup(command),
      command,
      ...(alias ? { alias } : {}),
      reason: reason(command),
    });
    const entries: MenuEntry[] = shown.map(entryOf);
    if (shown.length < builtins.length) {
      entries.push({ kind: 'more', id: 'builtin-more', group: 'builtin', count: builtins.length - shown.length });
    }
    const users = usable
      .filter((c) => c.name.includes(query))
      .sort((a, b) => Number(!a.name.startsWith(query)) - Number(!b.name.startsWith(query)) || a.name.localeCompare(b.name));
    for (const group of GROUP_ORDER) {
      if (group === 'sandbox') entries.push(...sandbox.map(entryOf));
      for (const command of users) {
        if (groupOf(command.owner) === group) entries.push({ kind: 'user', id: `user-${command.name}`, group, command });
      }
    }
    if (entries.length > 0) return { phase: 'menu', query, entries };
    return isTerminalWord(query) ? { phase: 'word', word: query } : { phase: 'none' };
  }
  const typed = parseTyped(text);
  if (!typed) return { phase: 'none' };
  const builtin = findBuiltin(typed.name);
  if (builtin) return { phase: 'builtin', command: builtin, typed: typed.name, args: typed.args, reason: reason(builtin) };
  if (isTerminalWord(typed.name)) return { phase: 'word', word: typed.name };
  const command = usable.find((c) => c.name === typed.name);
  if (!command) return { phase: 'none' };
  const body = bodies[command.name];
  // Without the body the arguments still validate; the preview waits for it.
  const preview = renderCommand(body ?? '', command.arguments, typed.args);
  return {
    phase: 'args',
    command,
    args: typed.args,
    preview: body === undefined && preview.ok ? null : preview,
  };
}

/** The live chip of a message just sent, before the server's record of it arrives. */
export function liveSentCommand(text: string, commands: ChatCommand[]): SentCommand | null {
  const typed = parseTyped(text);
  const command = typed ? commands.find((c) => c.name === typed.name) : undefined;
  if (!typed || !command) return null;
  return { name: command.name, version: command.version, args: {}, typed: text.trim(), owner: command.owner };
}
