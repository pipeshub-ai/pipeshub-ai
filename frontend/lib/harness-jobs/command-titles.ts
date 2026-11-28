import type { Words } from './words';

/**
 * A command a job ran, named for what it did (Activity, the trail): "Copied the payments repository", "Ran the
 * refund tests", "Installed packages". A small table of common tools (git, test runners, package managers, build
 * tools, file reads and searches, a script); the raw command is always shown under the title, so the person can
 * check. Nothing is guessed beyond what the command says: an unknown tool reads "Ran a command".
 *
 * In a chain (`cd payments && pytest`) the first part that is not a `cd`, an `export` or an `env` assignment names it.
 */

export interface CommandFacts {
  command: string;
  status: 'running' | 'completed' | 'failed' | string;
  exitCode?: number;
  output?: string;
}

export interface CommandTitle {
  title: string;
  /** How a test run came out ("1 failed", "All passed"), once it ended. */
  result?: string;
}

type Kind =
  | { kind: 'clone'; name?: string }
  | { kind: 'gitStatus' | 'gitCommit' | 'gitPush' | 'gitSwitch' | 'install' | 'build' | 'typecheck' | 'look' | 'search' | 'mkdir' }
  | { kind: 'tests'; name?: string }
  | { kind: 'script'; script: string };

const SEPARATORS = /\s*(?:&&|\|\||;|\|)\s*/;
const SKIP = /^(?:cd|export|set|source|\.|true)$/;
const words = (part: string): string[] => part.trim().split(/\s+/).filter((w) => w && !/^[A-Z_][A-Z0-9_]*=/.test(w));

const TEST_RUNNERS = new Set(['pytest', 'jest', 'vitest', 'mocha', 'rspec', 'phpunit', 'tox', 'nox']);
const LOOKS = new Set(['ls', 'cat', 'head', 'tail', 'less', 'more', 'find', 'tree', 'wc', 'stat', 'file', 'du']);
const SEARCHES = new Set(['grep', 'rg', 'ag', 'ack', 'egrep', 'fgrep']);
const NODE_PMS = new Set(['npm', 'pnpm', 'yarn', 'bun']);
const RUN_LAUNCHERS = new Set(['npx', 'pnpx', 'bunx', 'uvx']);
const SCRIPT_EXT = /\.(?:py|js|mjs|cjs|ts|sh|rb|pl)$/;

/** "tests/test_refunds.py" → "refund"; "src/totals.test.ts" → "total". Only from a single file the command names. */
function testName(args: string[]): string | undefined {
  const files = args.filter((a) => !a.startsWith('-') && /(?:^|\/)(?:test_[\w-]+|[\w-]+_test|[\w-]+\.(?:test|spec))\.\w+$/.test(a));
  if (files.length !== 1) return undefined;
  const base = files[0].split('/').pop() as string;
  const stem = base.replace(/\.\w+$/, '').replace(/\.(?:test|spec)$/, '').replace(/^test_/, '').replace(/_test$/, '');
  const name = stem.replace(/[_-]+/g, ' ').trim().toLowerCase();
  if (!name) return undefined;
  return /[^s]s$/.test(name) ? name.slice(0, -1) : name;
}

function repoName(url: string | undefined): string | undefined {
  if (!url) return undefined;
  const last = url.replace(/\/+$/, '').split(/[/:]/).pop() ?? '';
  const name = last.replace(/\.git$/, '');
  return /^[\w.-]+$/.test(name) ? name : undefined;
}

function classify(part: string): Kind | null {
  let w = words(part);
  if (RUN_LAUNCHERS.has(w[0])) w = w.slice(1);
  if ((w[0] === 'python' || w[0] === 'python3') && w[1] === '-m') w = w.slice(2);
  if (w[0] === 'uv' && w[1] === 'pip') w = w.slice(1);
  const [tool, sub, ...rest] = w;
  if (!tool) return null;
  if (tool === 'git') {
    if (sub === 'clone') return { kind: 'clone', name: repoName(rest.filter((a) => !a.startsWith('-'))[0]) };
    if (sub === 'status' || sub === 'diff' || sub === 'log' || sub === 'show') return { kind: 'gitStatus' };
    if (sub === 'commit') return { kind: 'gitCommit' };
    if (sub === 'push') return { kind: 'gitPush' };
    if (sub === 'checkout' || sub === 'switch') return { kind: 'gitSwitch' };
    return null;
  }
  if (TEST_RUNNERS.has(tool)) return { kind: 'tests', name: testName([sub, ...rest].filter(Boolean)) };
  if ((tool === 'go' || tool === 'cargo') && sub === 'test') return { kind: 'tests' };
  if (NODE_PMS.has(tool)) {
    if (sub === 'test' || (sub === 'run' && rest[0] === 'test')) return { kind: 'tests' };
    if (sub === 'install' || sub === 'ci' || sub === 'add' || sub === 'i' || (tool === 'yarn' && sub === undefined)) return { kind: 'install' };
    if (sub === 'run' && rest[0] === 'build') return { kind: 'build' };
    if (sub === 'build') return { kind: 'build' };
    return null;
  }
  if ((tool === 'pip' || tool === 'pip3' || tool === 'poetry') && (sub === 'install' || sub === 'add')) return { kind: 'install' };
  if (tool === 'go' && (sub === 'build' || sub === 'install')) return { kind: 'build' };
  if (tool === 'cargo' && sub === 'build') return { kind: 'build' };
  if (tool === 'make' || tool === 'gradle' || tool === 'mvn') return { kind: 'build' };
  if (tool === 'tsc' || tool === 'mypy') return { kind: 'typecheck' };
  if (LOOKS.has(tool)) return { kind: 'look' };
  if (SEARCHES.has(tool)) return { kind: 'search' };
  if (tool === 'mkdir') return { kind: 'mkdir' };
  if ((tool === 'python' || tool === 'python3' || tool === 'node' || tool === 'bash' || tool === 'sh' || tool === 'ruby') && sub && SCRIPT_EXT.test(sub)) {
    return { kind: 'script', script: sub.split('/').pop() as string };
  }
  return null;
}

/** The first part of a chain that is not setting up (cd, export), classified; null when it names nothing known. */
function kindOf(command: string): Kind | null {
  for (const part of command.split(SEPARATORS)) {
    const first = words(part)[0];
    if (!first || SKIP.test(first)) continue;
    return classify(part);
  }
  return null;
}

/** "1 failed" from the runner's own summary; "All passed" from a clean exit; "Some failed" when it says no more. */
function testResult(facts: CommandFacts, t: Words): string | undefined {
  if (facts.status === 'running') return undefined;
  const failed = /(\d+)\s+(?:failed|failing|failures?)\b/i.exec(facts.output ?? '');
  if (failed && Number(failed[1]) > 0) return t('ui.command.result.failed', { count: Number(failed[1]) });
  if (facts.exitCode === 0 || (facts.exitCode === undefined && facts.status === 'completed')) return t('ui.command.result.passed');
  return t('ui.command.result.someFailed');
}

export function commandTitle(facts: CommandFacts, t: Words): CommandTitle {
  const running = facts.status === 'running';
  const tense = running ? 'running' : 'done';
  const kind = kindOf(facts.command ?? '');
  if (!kind) return { title: running ? t('ui.trail.runningCommand') : t('ui.trail.ranCommand') };
  switch (kind.kind) {
    case 'clone':
      return { title: kind.name ? t(`ui.command.${tense}.cloneNamed`, { name: kind.name }) : t(`ui.command.${tense}.clone`) };
    case 'tests': {
      const title = kind.name ? t(`ui.command.${tense}.testsNamed`, { name: kind.name }) : t(`ui.command.${tense}.tests`);
      const result = testResult(facts, t);
      return result ? { title, result } : { title };
    }
    case 'script':
      return { title: t(`ui.command.${tense}.script`, { script: kind.script }) };
    default:
      return { title: t(`ui.command.${tense}.${kind.kind}`) };
  }
}
