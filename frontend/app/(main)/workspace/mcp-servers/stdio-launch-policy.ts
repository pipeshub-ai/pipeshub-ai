// Client-side mirror of backend/python/app/agents/mcp/stdio_policy.py so the form can explain
// a rejection before saving. The backend stays authoritative.

type Ecosystem = 'npm' | 'python';

interface Launcher {
  ecosystem: Ecosystem;
  packageOptions: ReadonlySet<string>;
  valueOptions: ReadonlySet<string>;
  extraPackageOptions: ReadonlySet<string>;
  forbiddenOptions: ReadonlySet<string>;
}

const NPX: Launcher = {
  ecosystem: 'npm',
  packageOptions: new Set(['-p', '--package']),
  valueOptions: new Set(['--cache', '--registry', '--userconfig', '-w', '--workspace']),
  extraPackageOptions: new Set(),
  forbiddenOptions: new Set(['-c', '--call', '--shell-mode']),
};

const BUNX: Launcher = {
  ecosystem: 'npm',
  packageOptions: new Set(['-p', '--package']),
  valueOptions: new Set(['--cwd']),
  extraPackageOptions: new Set(),
  forbiddenOptions: new Set(),
};

const UVX: Launcher = {
  ecosystem: 'python',
  packageOptions: new Set(['--from']),
  valueOptions: new Set(['--python', '-p', '--index', '--index-url', '--extra-index-url', '--with-requirements']),
  extraPackageOptions: new Set(['--with']),
  forbiddenOptions: new Set(),
};

const PIPX_RUN: Launcher = {
  ecosystem: 'python',
  packageOptions: new Set(['--spec']),
  valueOptions: new Set(['--python', '--index-url', '--pip-args']),
  extraPackageOptions: new Set(),
  forbiddenOptions: new Set(),
};

const LAUNCHERS: { command: string; subcommand: string[]; launcher: Launcher }[] = [
  { command: 'npx', subcommand: [], launcher: NPX },
  { command: 'npm', subcommand: ['exec'], launcher: NPX },
  { command: 'pnpm', subcommand: ['dlx'], launcher: NPX },
  { command: 'pnpx', subcommand: [], launcher: NPX },
  { command: 'yarn', subcommand: ['dlx'], launcher: NPX },
  { command: 'bunx', subcommand: [], launcher: BUNX },
  { command: 'bun', subcommand: ['x'], launcher: BUNX },
  { command: 'uvx', subcommand: [], launcher: UVX },
  { command: 'uv', subcommand: ['tool', 'run'], launcher: UVX },
  { command: 'pipx', subcommand: ['run'], launcher: PIPX_RUN },
];

const NPM_NAME = /^(@[a-z0-9][a-z0-9._~-]*\/)?[a-z0-9][a-z0-9._~-]*$/;
const SEMVER_EXACT = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/;
const PY_NAME = /^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(\[[A-Za-z0-9._,-]+\])?$/;
const PEP440_EXACT = /^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$/;

export type StdioLaunchIssue =
  | { kind: 'unpinned'; spec: string; example: string }
  | { kind: 'forbiddenOption'; option: string }
  | { kind: 'missingPackage' };

export function isPinnedNpmSpec(spec: string): boolean {
  const at = spec.lastIndexOf('@');
  if (at <= 0) return false;
  return NPM_NAME.test(spec.slice(0, at)) && SEMVER_EXACT.test(spec.slice(at + 1));
}

export function isPinnedPythonSpec(spec: string): boolean {
  const sep = spec.includes('==') ? '==' : spec.includes('@') ? '@' : null;
  if (!sep) return false;
  const idx = spec.indexOf(sep);
  return PY_NAME.test(spec.slice(0, idx).trim()) && PEP440_EXACT.test(spec.slice(idx + sep.length).trim());
}

function commandName(command: string): string {
  const base = command.trim().split(/[\\/]/).pop()?.toLowerCase() ?? '';
  return base.replace(/\.(cmd|exe)$/, '');
}

/** The first reason `command args` would be rejected for fetching an unpinned package, if any. */
export function findStdioLaunchIssue(command: string, args: string[]): StdioLaunchIssue | null {
  const name = commandName(command);
  const match = LAUNCHERS.find(
    (l) => l.command === name && l.subcommand.every((part, i) => args[i] === part)
  );
  if (!match) return null;
  const { launcher } = match;
  const rest = args.slice(match.subcommand.length);

  const specs: string[] = [];
  let replacesPositional = false;
  let positional: string | null = null;
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (arg === '--') {
      positional = rest[i + 1] ?? null;
      break;
    }
    if (!arg.startsWith('-')) {
      positional = arg;
      break;
    }
    const eq = arg.indexOf('=');
    const option = eq >= 0 ? arg.slice(0, eq) : arg;
    if (launcher.forbiddenOptions.has(option)) return { kind: 'forbiddenOption', option };
    const namesPackage = launcher.packageOptions.has(option) || launcher.extraPackageOptions.has(option);
    if (namesPackage || launcher.valueOptions.has(option)) {
      let value: string | undefined;
      if (eq >= 0) {
        value = arg.slice(eq + 1);
      } else {
        i += 1;
        value = rest[i];
      }
      if (value === undefined) return { kind: 'missingPackage' };
      if (namesPackage) {
        specs.push(value);
        replacesPositional = replacesPositional || launcher.packageOptions.has(option);
      }
    }
  }
  if (!replacesPositional) {
    if (positional === null) return { kind: 'missingPackage' };
    specs.push(positional);
  }

  const isPinned = launcher.ecosystem === 'npm' ? isPinnedNpmSpec : isPinnedPythonSpec;
  const example = launcher.ecosystem === 'npm' ? 'name@1.2.3' : 'name==1.2.3';
  const unpinned = specs.find((spec) => !isPinned(spec));
  return unpinned === undefined ? null : { kind: 'unpinned', spec: unpinned, example };
}
