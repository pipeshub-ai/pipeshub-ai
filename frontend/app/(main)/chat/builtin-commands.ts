/**
 * PipesHub's built-in /commands (SLASH2). The list is the generated registry
 * (`contracts/harness/ext/pipeshub/commands/builtin-commands.json`); this module
 * decides, from what the chat is now, whether each one applies (O8) and when
 * it acts during a run (O7). A built-in never reaches a model: the composer
 * runs it with the same API or action as its button.
 */
import { BUILTIN_COMMANDS } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';
import type { BuiltinCommands } from '@/lib/contracts/harness/generated/ext/pipeshub/commands/builtin-commands';

export type BuiltinCommand = BuiltinCommands['commands'][number];

/** The menu group a built-in is listed under (SLASH3 §3.9): the Sandbox group follows Built-in. */
export type BuiltinGroup = 'builtin' | 'sandbox';
export const builtinGroup = (command: BuiltinCommand): BuiltinGroup => (command.group === 'sandbox' ? 'sandbox' : 'builtin');

/** Listed in the menu, in registry order. Planned ones are reserved but never offered. */
export const SHIPPED_BUILTINS: readonly BuiltinCommand[] = BUILTIN_COMMANDS.commands.filter((c) => c.shipped);

/** Command-bus names the chat registers for the built-ins that act on what is on screen. */
export const BUILTIN_BUS = {
  newChat: 'newChat',
  openSearch: 'openCommandPalette',
  copyLastAnswer: 'copyLastAnswer',
  feedbackLastAnswer: 'feedbackLastAnswer',
  continuePrivately: 'continuePrivately',
} as const;

/** Shown while the query is empty; the rest sit behind "More built-ins" (§4 Risks, menu length). */
export const BUILTIN_FIRST_PAGE = 8;

const TERMINAL_WORDS: ReadonlySet<string> = new Set(BUILTIN_COMMANDS.reservedWords.map((w) => w.name));

/** What the chat is now, as far as the built-ins care. */
export interface BuiltinContext {
  /** A reply is running in this chat. */
  running: boolean;
  /** The chat is saved (it has a conversation id). */
  conversation: boolean;
  /** There is a finished answer to copy or rate. */
  answer: boolean;
  /** The chat runs on the agent harness (durable runs). */
  harness: boolean;
  /** "Continue privately" is offered for this chat (SS3). */
  forkable: boolean;
  /** The selected model has a reasoning-effort setting. */
  reasoningModel: boolean;
  /** An agent chat whose strategy chip is shown (not an engine agent). */
  planStrategy: boolean;
  mcp: boolean;
  /** The knowledge selector applies here: not web search, and not an agent whose knowledge is fixed. */
  knowledge: boolean;
  memory: boolean;
  skills: boolean;
  /** This chat's workspace has files (its head revision is above 0). */
  workspace: boolean;
  /** A chat with an agent (not the assistant). */
  agentChat: boolean;
  /** The caller may edit that agent (`/init`, O4). */
  agentEditor: boolean;
  /** Finished answers the chat shows: `/compact` needs an earlier one to summarise beside the last. */
  answers: number;
  /** The viewer is a participant of someone else's shared chat: `/compact` is the owner's (the server refuses it too). */
  notOwner?: boolean;
  /**
   * Whether `/run` may run code here (SLASH3 §3.5), from `GET …/sandbox`: `denied` when a rule blocks
   * `run_code`, `missing` when the chat has no code tool; absent until known (the server is the backstop).
   */
  runCode?: 'granted' | 'missing' | 'denied';
  /** Whether `/engine` may choose here (SLASH3 §3.7): an agent fixes it, or Quick chat has no engine. */
  engineChoice?: 'choose' | 'fixed' | 'quickChat';
  /**
   * Why `/repo` cannot pick a repository here (WP-26): the org turned no engine on, this chat has none, or it already
   * works on one. Absent or null: it can.
   */
  repo?: 'orgOff' | 'noEngine' | 'hasRepository' | null;
}

export type BuiltinReason =
  | 'notRunning'
  | 'wait'
  | 'noConversation'
  | 'noAnswer'
  | 'legacy'
  | 'noFork'
  | 'noEffort'
  | 'agentOnly'
  | 'fixed'
  | 'off'
  | 'noWorkspace'
  | 'noAgent'
  | 'notEditor'
  | 'nothingToCompact'
  | 'notOwner'
  | 'noRunCode'
  | 'runCodeDenied'
  | 'engineFixed'
  | 'quickChat'
  | 'repoOrgOff'
  | 'repoNoEngine'
  | 'repoTaken';

/** `/repo`'s reason (and the + menu row's) for why the picker cannot be used here. */
export function repoBuiltinReason(repo: BuiltinContext['repo']): BuiltinReason | null {
  switch (repo) {
    case 'orgOff':
      return 'repoOrgOff';
    case 'noEngine':
      return 'repoNoEngine';
    case 'hasRepository':
      return 'repoTaken';
    default:
      return null;
  }
}

/** Why a built-in does not apply here (O8), or null when it does. */
export function builtinReason(command: BuiltinCommand, ctx: BuiltinContext): BuiltinReason | null {
  if (command.needs.includes('run') && !ctx.running) return 'notRunning';
  // A participant can never use the owner's sandbox; that outranks a reason that would pass (SLASH3 §3).
  if (command.needs.includes('owner') && ctx.notOwner) return 'notOwner';
  if (command.duringRun === 'wait' && ctx.running) return 'wait';
  if (command.needs.includes('conversation') && !ctx.conversation) return 'noConversation';
  if (command.needs.includes('answer') && !ctx.answer) return 'noAnswer';
  if (command.needs.includes('harness') && !ctx.harness) return 'legacy';
  if (command.needs.includes('workspace') && !ctx.workspace) return 'noWorkspace';
  if (command.needs.includes('runCode') && ctx.runCode === 'denied') return 'runCodeDenied';
  if (command.needs.includes('runCode') && ctx.runCode === 'missing') return 'noRunCode';
  if (command.needs.includes('engineChoice') && ctx.engineChoice === 'fixed') return 'engineFixed';
  if (command.needs.includes('engineChoice') && ctx.engineChoice === 'quickChat') return 'quickChat';
  switch (command.name) {
    case 'fork':
      return ctx.forkable ? null : 'noFork';
    case 'effort':
      return ctx.reasoningModel ? null : 'noEffort';
    case 'plan':
      return ctx.planStrategy ? null : 'agentOnly';
    case 'mcp':
      return ctx.mcp ? null : 'off';
    case 'knowledge':
      return ctx.knowledge ? null : 'fixed';
    case 'memory':
      return ctx.memory ? null : 'off';
    case 'skills':
      return ctx.skills ? null : 'off';
    case 'init':
      return !ctx.agentChat ? 'noAgent' : !ctx.agentEditor ? 'notEditor' : !ctx.conversation ? 'noConversation' : null;
    case 'repo':
      return repoBuiltinReason(ctx.repo);
    case 'compact':
      if (ctx.notOwner) return 'notOwner';
      return ctx.answers >= 2 ? null : 'nothingToCompact';
    default:
      return null;
  }
}

/** A built-in by its name or an alias (`/clear` is `/new`). */
export function findBuiltin(name: string): BuiltinCommand | undefined {
  return SHIPPED_BUILTINS.find((c) => c.name === name || c.aliases.includes(name));
}

/** A terminal-only or unmapped word (`/login`): sent as text, with a hint that PipesHub has no such command. */
export function isTerminalWord(name: string): boolean {
  return TERMINAL_WORDS.has(name);
}

/**
 * Selecting it from the menu waits for an argument (`/rename title`); otherwise it runs at once. A prompt
 * command (`/review`, `/init`) starts a reply, so it waits for Enter too.
 */
export function takesArgsFirst(command: BuiltinCommand): boolean {
  return isPromptBuiltin(command) || (command.args !== '' && !command.args.endsWith('?'));
}

/** PipesHub's prompt commands are sent as the next message and expanded by the server (SLASH1 D2); no `/x` reaches a model. */
export function isPromptBuiltin(command: BuiltinCommand): boolean {
  return command.kind === 'prompt';
}

export interface BuiltinMatch {
  command: BuiltinCommand;
  /** The alias the query matched, when it was not the name. */
  alias?: string;
}

/** The built-ins a menu query matches, name prefix first, in registry order otherwise. */
export function matchBuiltins(query: string): BuiltinMatch[] {
  const out: Array<BuiltinMatch & { rank: number }> = [];
  SHIPPED_BUILTINS.forEach((command, index) => {
    if (command.name.includes(query)) {
      out.push({ command, rank: (command.name.startsWith(query) ? 0 : 1000) + index });
      return;
    }
    const alias = command.aliases.find((a) => a.includes(query));
    if (alias) out.push({ command, alias, rank: (alias.startsWith(query) ? 0 : 1000) + index });
  });
  return out.sort((a, b) => a.rank - b.rank).map(({ rank: _rank, ...match }) => match);
}
