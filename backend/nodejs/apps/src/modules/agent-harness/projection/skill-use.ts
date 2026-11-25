/**
 * Which skills a step read (FIX-SKILLSHOW), so every view names it as its own step ("Read the PDF skill") whatever
 * the engine: Codex, opencode and ACP engines have no skill tool and read `skills/<name>/SKILL.md` with the shell or
 * run `ph skill show <name>`, often chained with other reads; Claude Code calls its `Skill` tool. One recognizer for
 * the activity's commands and for every engine progress line, live or saved, so the activity panel, the timeline
 * and any later view agree. It names the skill only: the step's command stays as it ran.
 */

const NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SKILL_FILE =
  /(?:^|\/)skills\/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\/SKILL\.md$/;
/** Programs that print a file: a segment running one on a skill's SKILL.md read that skill. */
const READERS: ReadonlySet<string> = new Set([
  'cat',
  'bat',
  'batcat',
  'sed',
  'head',
  'tail',
  'less',
  'more',
  'nl',
  'awk',
  'view',
]);
/** Words before the program that run it rather than name it. */
const PREFIXES: ReadonlySet<string> = new Set([
  'sudo',
  'command',
  'exec',
  'env',
  'time',
  'nice',
]);
const SHELLS: ReadonlySet<string> = new Set(['sh', 'bash', 'zsh', 'dash']);
/** Each engine's skill tool: Claude Code's, opencode's, Gemini CLI's. */
const SKILL_TOOLS: ReadonlySet<string> = new Set([
  'Skill',
  'skill',
  'activate_skill',
]);
/** File-reading tools (TOOL_DISPLAY's `readFile` built-ins and ACP's `read`) and the argument naming the file. */
const READ_TOOLS: ReadonlySet<string> = new Set([
  'Read',
  'read',
  'read_file',
  'read_sandbox_file',
  'view',
]);
const PATH_ARGS = ['file_path', 'filePath', 'path', 'absolute_path'] as const;
/** A line from a misbehaving engine is not walked past this; a skill read is a short command. */
const MAX_SCAN_CHARS = 8_000;
const MAX_SHELL_DEPTH = 3;

const basename = (word: string): string =>
  word.slice(word.lastIndexOf('/') + 1);

/** The command's segments (split at `&&`, `||`, `;`, `|`, `&` and newlines outside quotes), each as its words. */
function segments(script: string): string[][] {
  const out: string[][] = [];
  let words: string[] = [];
  let word = '';
  let quoted = false;
  let quote: '"' | "'" | null = null;
  const endWord = (): void => {
    if (word !== '' || quoted) words.push(word);
    word = '';
    quoted = false;
  };
  const endSegment = (): void => {
    endWord();
    if (words.length > 0) out.push(words);
    words = [];
  };
  for (let i = 0; i < script.length; i += 1) {
    const ch = script.charAt(i);
    if (quote !== null) {
      if (ch === quote) quote = null;
      else if (ch === '\\' && quote === '"' && i + 1 < script.length) {
        i += 1;
        word += script.charAt(i);
      } else word += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      quoted = true;
    } else if (ch === '\\' && i + 1 < script.length) {
      i += 1;
      if (script.charAt(i) !== '\n') word += script.charAt(i);
    } else if (ch === ' ' || ch === '\t') endWord();
    else if (ch === '\n' || ch === ';' || ch === '|' || ch === '&')
      endSegment();
    else word += ch;
  }
  endSegment();
  return out;
}

/** The words after env assignments and launcher prefixes, from the program on. */
function program(words: readonly string[]): readonly string[] {
  let at = 0;
  for (const word of words) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*=/.test(word) && !PREFIXES.has(word)) break;
    at += 1;
  }
  return words.slice(at);
}

function segmentSkills(
  words: readonly string[],
  depth: number,
  into: string[],
): void {
  const [first, ...rest] = program(words);
  if (first === undefined) return;
  const name = basename(first);
  // `bash -lc "<script>"`, as Codex runs every command: the script is the command.
  if (SHELLS.has(name) && depth < MAX_SHELL_DEPTH) {
    const flag = rest.findIndex((word) => /^-[A-Za-z]*c[A-Za-z]*$/.test(word));
    const script = flag >= 0 ? rest[flag + 1] : undefined;
    if (script !== undefined) scriptSkills(script, depth + 1, into);
    return;
  }
  if (name === 'ph') {
    const args = rest.filter((word) => !word.startsWith('-'));
    if (
      (args[0] === 'skill' || args[0] === 'skills') &&
      args[1] === 'show' &&
      args[2] !== undefined &&
      NAME.test(args[2])
    ) {
      into.push(args[2]);
    }
    return;
  }
  if (!READERS.has(name)) return;
  for (const word of rest) {
    const skill = SKILL_FILE.exec(word.replace(/^\d*</, ''))?.[1];
    if (skill !== undefined) into.push(skill);
  }
}

function scriptSkills(script: string, depth: number, into: string[]): void {
  for (const words of segments(script)) segmentSkills(words, depth, into);
}

const distinct = (names: readonly string[]): string[] => [...new Set(names)];

/** The skills a shell command read, in order, each once; none for a command that read no skill. */
export function commandSkills(command: string): string[] {
  if (!command.includes('SKILL.md') && !/\bph\b/.test(command)) return [];
  const found: string[] = [];
  scriptSkills(command.slice(0, MAX_SCAN_CHARS), 0, found);
  return distinct(found);
}

function parsedArgs(args: unknown): Record<string, unknown> {
  if (args !== null && typeof args === 'object' && !Array.isArray(args)) {
    return args as Record<string, unknown>;
  }
  if (typeof args !== 'string' || args.length > MAX_SCAN_CHARS) return {};
  try {
    const parsed: unknown = JSON.parse(args);
    return parsed !== null &&
      typeof parsed === 'object' &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/**
 * The skills an engine's own tool call read: its skill tool (Claude Code's `skill`, or an older build's `command`;
 * opencode's and Gemini CLI's `name`), a file-reading tool on a skill's SKILL.md, or a shell tool's `command`.
 */
export function toolSkills(toolName: string, args: unknown): string[] {
  const parsed = parsedArgs(args);
  if (SKILL_TOOLS.has(toolName)) {
    const skill = parsed['skill'] ?? parsed['name'] ?? parsed['command'];
    return typeof skill === 'string' && NAME.test(skill.trim())
      ? [skill.trim()]
      : [];
  }
  if (READ_TOOLS.has(toolName)) {
    for (const key of PATH_ARGS) {
      const path = parsed[key];
      const skill =
        typeof path === 'string' ? SKILL_FILE.exec(path)?.[1] : undefined;
      if (skill !== undefined) return [skill];
    }
    return [];
  }
  const command = parsed['command'];
  return typeof command === 'string' ? commandSkills(command) : [];
}

const TOOL_KINDS: ReadonlySet<string> = new Set(['tool', 'tool_update']);

/**
 * An engine progress line's `data` with the skills its step read as `skills`; unchanged for a line that read none.
 * A `command` line's command is its text; a tool line's tool is its text and its arguments `data.args`.
 */
export function withLineSkills(
  kind: string,
  text: string,
  data: Record<string, unknown>,
): Record<string, unknown> {
  const skills =
    kind === 'command'
      ? commandSkills(text)
      : TOOL_KINDS.has(kind)
        ? toolSkills(text, data['args'])
        : [];
  return skills.length > 0 ? { ...data, skills } : data;
}
