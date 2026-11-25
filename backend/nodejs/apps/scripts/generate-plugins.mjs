#!/usr/bin/env node
/**
 * Writes src/plugins.generated.ts: the list of plug-ins this build contains.
 *
 *   node scripts/generate-plugins.mjs                 the default (see below)
 *   node scripts/generate-plugins.mjs a b             only plug-ins a and b
 *   node scripts/generate-plugins.mjs --none          no plug-ins
 *   PLATFORM_PLUGINS="a,b" node scripts/generate-plugins.mjs   same, from the environment
 *   PLATFORM_PLUGINS="" node scripts/generate-plugins.mjs      no plug-ins
 *   node scripts/generate-plugins.mjs --profile <name>         the plug-ins of a build profile
 *   node scripts/generate-plugins.mjs --build KEY=full --build OTHER=stub
 *                                                     the plug-ins those build switches turn on
 *
 * A plug-in is named by its directory: a bare name is src/plugins/<name>, a
 * path (it contains a "/") is relative to src.
 *
 * Build profiles are optional. When plugins.profiles.json exists next to src it
 * holds, as plain data:
 *   profiles     profile name -> plug-in directories, in registration order
 *   builds       build switch -> the plug-in directories it adds when "full"
 *   default      the profile the checked-in list is generated from
 *   plugins      per plug-in directory, optional: `owns` (other directories under
 *                src that exist only for it) and `dependencies` (package names or
 *                `scope/*` patterns only it needs)
 * A directory a profile lists must exist; one that has no index.ts yet is a
 * placeholder and is left out of the list.
 *
 * The default, with no selection: the `default` profile when the profiles file
 * exists, otherwise every directory under src/plugins.
 *
 * Options: --src <dir> (default: ../src next to this script), --profiles <file>
 * (default: plugins.profiles.json next to src), --check (exit 1 when the file
 * on disk differs from what would be written, write nothing), --describe (print
 * the selection as JSON and write nothing: the plug-ins kept, and the
 * directories and dependencies a build with this selection must remove).
 *
 * An image that must not contain a plug-in deletes its directories and runs
 * this with the same selection (or with no arguments, or --none) before compiling.
 */
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const NAME = /^[a-z][a-z0-9-]*$/;
const BUILD_VALUES = ['stub', 'full'];

function fail(message) {
  process.stderr.write(`generate-plugins: ${message}\n`);
  process.exit(2);
}

const args = process.argv.slice(2);
let srcDir = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'src');
let profilesFile;
let check = false;
let describe = false;
let none = false;
let profile;
const builds = new Map();
const named = [];
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  const value = () => {
    const next = args[++i];
    if (next === undefined) fail(`${arg} needs a value`);
    return next;
  };
  if (arg === '--src') srcDir = resolve(value());
  else if (arg === '--profiles') profilesFile = resolve(value());
  else if (arg === '--profile') profile = value();
  else if (arg === '--build') {
    const [key, setting, ...rest] = value().split('=');
    if (!key || setting === undefined || rest.length > 0)
      fail('--build needs KEY=stub or KEY=full');
    if (!BUILD_VALUES.includes(setting))
      fail(`${key} must be stub or full, not "${setting}"`);
    builds.set(key, setting);
  } else if (arg === '--check') check = true;
  else if (arg === '--describe') describe = true;
  else if (arg === '--none') none = true;
  else if (arg.startsWith('--')) fail(`unknown option ${arg}`);
  else named.push(...arg.split(','));
}

profilesFile ??= join(srcDir, '..', 'plugins.profiles.json');
const config = existsSync(profilesFile)
  ? JSON.parse(readFileSync(profilesFile, 'utf8'))
  : undefined;
const profiles = config?.profiles ?? {};
const buildSwitches = config?.builds ?? {};
const pluginInfo = config?.plugins ?? {};

/** A plug-in's directory relative to src, from a bare name or a path. */
const directoryOf = (entry) =>
  entry.includes('/') ? entry.replace(/\/+$/, '') : `plugins/${entry}`;
const hasIndex = (dir) => existsSync(join(srcDir, dir, 'index.ts'));

const selections = [
  none,
  named.length > 0,
  profile !== undefined,
  builds.size > 0,
].filter(Boolean).length;
if (selections > 1)
  fail('choose one of: plug-in names, --none, --profile, --build');

const pluginsDir = join(srcDir, 'plugins');
const discovered = () =>
  existsSync(pluginsDir)
    ? readdirSync(pluginsDir, { withFileTypes: true })
        .filter(
          (entry) => entry.isDirectory() && hasIndex(`plugins/${entry.name}`),
        )
        .map((entry) => entry.name)
        .sort()
    : [];

function profileDirectories(name) {
  const list = profiles[name];
  if (!Array.isArray(list)) {
    const known = Object.keys(profiles).join(', ') || 'none defined';
    fail(`unknown profile "${name}" (${known})`);
  }
  return list;
}

/** Whether the selection came from the profiles file: its placeholders are skipped. */
let fromProfiles = false;
let entries;
if (none) entries = [];
else if (named.length > 0) entries = [...new Set(named)].sort();
else if (profile !== undefined) {
  fromProfiles = true;
  entries = profileDirectories(profile);
} else if (builds.size > 0) {
  fromProfiles = true;
  for (const key of builds.keys()) {
    if (!(key in buildSwitches)) {
      const known = Object.keys(buildSwitches).join(', ') || 'none defined';
      fail(`unknown build switch "${key}" (${known})`);
    }
  }
  // Every switch must be given: a forgotten one must not silently mean "stub".
  for (const key of Object.keys(buildSwitches)) {
    if (!builds.has(key)) fail(`--build ${key}=stub|full is missing`);
  }
  const wanted = new Set(
    Object.keys(buildSwitches)
      .filter((key) => builds.get(key) === 'full')
      .flatMap((key) => buildSwitches[key]),
  );
  // Registration order is the order of the profile that lists the most plug-ins.
  const order = Object.values(profiles)
    .slice()
    .sort((a, b) => b.length - a.length)
    .flat();
  entries = [...new Set([...order, ...wanted])].filter((dir) =>
    wanted.has(dir),
  );
  profile = Object.keys(profiles).find(
    (name) =>
      profiles[name].length === entries.length &&
      profiles[name].every((dir) => wanted.has(dir)),
  );
} else if (process.env.PLATFORM_PLUGINS !== undefined)
  entries = [...new Set(process.env.PLATFORM_PLUGINS.split(/[\s,]+/))].sort();
else if (config?.default !== undefined) {
  fromProfiles = true;
  profile = config.default;
  entries = profileDirectories(profile);
} else entries = discovered();

entries = [...new Set(entries.map((n) => n.trim()).filter((n) => n !== ''))];

const kept = [];
for (const entry of entries) {
  const dir = directoryOf(entry);
  if (!dir.split('/').every((part) => NAME.test(part)))
    fail(`"${entry}" is not a plug-in name (${NAME})`);
  if (hasIndex(dir)) kept.push(dir);
  else if (fromProfiles && existsSync(join(srcDir, dir))) continue;
  // --describe also answers before the source tree is there (what to install).
  else if (describe) continue;
  else fail(`src/${dir}/index.ts does not exist`);
}

const baseName = (dir) => dir.slice(dir.lastIndexOf('/') + 1);
const identifier = (dir) =>
  `${baseName(dir).replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())}Plugin`;
const identifiers = new Map();
for (const dir of kept) {
  const id = identifier(dir);
  if (identifiers.has(id))
    fail(`src/${dir} and src/${identifiers.get(id)} would both be "${id}"`);
  identifiers.set(id, dir);
}

if (describe) {
  // Everything the profiles file knows about, whether or not this tree still has it.
  const known = [
    ...new Set([
      ...Object.values(profiles).flat(),
      ...Object.values(buildSwitches).flat(),
      ...Object.keys(pluginInfo),
    ]),
  ];
  const selected = new Set(entries.map(directoryOf));
  const excluded = known.filter((dir) => !selected.has(dir));
  const needed = new Set(
    [...selected].flatMap((dir) => pluginInfo[dir]?.dependencies ?? []),
  );
  const removeDependencies = [
    ...new Set(excluded.flatMap((dir) => pluginInfo[dir]?.dependencies ?? [])),
  ].filter((dependency) => !needed.has(dependency));
  const removeDirectories = [
    ...new Set(
      excluded.flatMap((dir) => [dir, ...(pluginInfo[dir]?.owns ?? [])]),
    ),
  ];
  process.stdout.write(
    `${JSON.stringify(
      {
        profile: profile ?? null,
        builds: Object.fromEntries(
          Object.keys(buildSwitches).map((key) => [
            key,
            buildSwitches[key].every((dir) => selected.has(dir))
              ? 'full'
              : 'stub',
          ]),
        ),
        plugins: kept,
        excludedPlugins: excluded,
        removeDirectories,
        removeDependencies,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(0);
}

const lines = [
  '// GENERATED by scripts/generate-plugins.mjs. Do not edit by hand.',
  '// The plug-ins this build contains; `loadPlugins()` registers them in this order.',
  "import type { PlatformPlugin } from './libs/plugins/types';",
  ...kept.map((dir) => `import ${identifier(dir)} from './${dir}';`),
  '',
  kept.length === 0
    ? 'export const plugins: readonly PlatformPlugin[] = [];'
    : `// prettier-ignore\nexport const plugins: readonly PlatformPlugin[] = [\n${kept
        .map((dir) => `  ${identifier(dir)},\n`)
        .join('')}];`,
  '',
];
const content = lines.join('\n');
const target = join(srcDir, 'plugins.generated.ts');

if (check) {
  const current = existsSync(target) ? readFileSync(target, 'utf8') : '';
  if (current !== content) {
    process.stderr.write(
      `generate-plugins: ${target} is out of date; run node scripts/generate-plugins.mjs\n`,
    );
    process.exit(1);
  }
} else {
  writeFileSync(target, content);
  process.stdout.write(
    `generate-plugins: wrote ${kept.length} plug-in(s) to ${target}\n`,
  );
}
