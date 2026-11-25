#!/usr/bin/env node
/**
 * Removes from package.json (and package-lock.json) the packages that only a
 * removed plug-in needs, so an image built without a plug-in does not install
 * its libraries either.
 *
 *   node scripts/remove-plugin-deps.mjs                 plug-ins whose directory is gone
 *   node scripts/remove-plugin-deps.mjs --remove a,b    plug-ins a and b, present or not
 *   node scripts/remove-plugin-deps.mjs --dry-run       print what would change, write nothing
 *
 * Which packages belong to which plug-in is data: `pluginDependencies` in
 * plugins.dependencies.json next to src (where plugins.profiles.json also
 * lives, so a tree built without a plug-in does not name it), plug-in name ->
 * package names. A plug-in is
 * named as in scripts/generate-plugins.mjs: a bare name is src/plugins/<name>,
 * a path (it contains a "/") is relative to src. A package that a plug-in still
 * in the tree also lists is kept.
 *
 * The lockfile is edited without the network: the package leaves the root
 * entry, and every locked package that only it brought in is dropped. Run it
 * before `npm ci`.
 *
 * Options: --src <dir> (default: ../src next to this script), --map <file>
 * (default: plugins.dependencies.json next to src), --package <file> (default:
 * package.json next to src), --lock <file> (default: package-lock.json next to
 * the package file; skipped when absent).
 *
 * Prints one JSON line: { removedPlugins, removedPackages, removedFromLock }.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SECTIONS = [
  'dependencies',
  'devDependencies',
  'optionalDependencies',
  'peerDependencies',
];

function fail(message) {
  process.stderr.write(`remove-plugin-deps: ${message}\n`);
  process.exit(2);
}

const args = process.argv.slice(2);
let srcDir = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'src');
let mapFile;
let packageFile;
let lockFile;
let dryRun = false;
let named;
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  const value = () => {
    const next = args[++i];
    if (next === undefined) fail(`${arg} needs a value`);
    return next;
  };
  if (arg === '--src') srcDir = resolve(value());
  else if (arg === '--map') mapFile = resolve(value());
  else if (arg === '--package') packageFile = resolve(value());
  else if (arg === '--lock') lockFile = resolve(value());
  else if (arg === '--dry-run') dryRun = true;
  else if (arg === '--remove')
    named = value()
      .split(',')
      .map((n) => n.trim())
      .filter((n) => n !== '');
  else fail(`unknown option ${arg}`);
}
mapFile ??= join(srcDir, '..', 'plugins.dependencies.json');
packageFile ??= join(srcDir, '..', 'package.json');
lockFile ??= join(dirname(packageFile), 'package-lock.json');

function readJson(file) {
  if (!existsSync(file)) fail(`${file} does not exist`);
  const text = readFileSync(file, 'utf8');
  try {
    return { text, json: JSON.parse(text) };
  } catch (error) {
    return fail(`${file} is not JSON: ${error.message}`);
  }
}

const map = readJson(mapFile).json.pluginDependencies;
if (map === null || typeof map !== 'object' || Array.isArray(map))
  fail(`${mapFile} needs a "pluginDependencies" object`);
for (const [plugin, packages] of Object.entries(map)) {
  if (
    !Array.isArray(packages) ||
    packages.some((name) => typeof name !== 'string' || name === '')
  )
    fail(`pluginDependencies.${plugin} must be a list of package names`);
}

const directoryOf = (plugin) =>
  plugin.includes('/') ? plugin.replace(/\/+$/, '') : `plugins/${plugin}`;
const present = (plugin) => existsSync(join(srcDir, directoryOf(plugin)));

for (const plugin of named ?? []) {
  if (!(plugin in map))
    fail(
      `"${plugin}" is not in pluginDependencies (${Object.keys(map).join(', ') || 'empty'})`,
    );
}
const removedPlugins = (
  named ?? Object.keys(map).filter((plugin) => !present(plugin))
).sort();
const keptPackages = new Set(
  Object.keys(map)
    .filter((plugin) => !removedPlugins.includes(plugin))
    .flatMap((plugin) => map[plugin]),
);
const wanted = [
  ...new Set(removedPlugins.flatMap((plugin) => map[plugin])),
].filter((name) => !keptPackages.has(name));

/** Rewrites a JSON file keeping its indentation and final newline. */
function write(file, original, json) {
  const indent = /^[{[]\r?\n([ \t]+)/.exec(original)?.[1] ?? '  ';
  const tail = original.endsWith('\n') ? '\n' : '';
  writeFileSync(file, JSON.stringify(json, null, indent) + tail);
}

const pkg = readJson(packageFile);
const removedPackages = [];
for (const name of wanted) {
  let found = false;
  for (const section of SECTIONS) {
    if (pkg.json[section] && name in pkg.json[section]) {
      delete pkg.json[section][name];
      found = true;
    }
  }
  if (found) removedPackages.push(name);
}
removedPackages.sort();

/**
 * The lock's entries reachable from the root, by Node's resolution: a
 * dependency of the package at `path` is the nearest `node_modules/<name>`
 * going up from it.
 */
function reachable(packages) {
  const seen = new Set(['']);
  const queue = [''];
  while (queue.length > 0) {
    const path = queue.pop();
    const entry = packages[path];
    const target = entry.link ? entry.resolved : path;
    if (entry.link) {
      if (packages[target] && !seen.has(target)) {
        seen.add(target);
        queue.push(target);
      }
      continue;
    }
    const names = SECTIONS.flatMap((section) =>
      // a package's own devDependencies are installed only for the root
      section === 'devDependencies' && path !== ''
        ? []
        : Object.keys(entry[section] ?? {}),
    );
    for (const name of names) {
      let base = target;
      for (;;) {
        const candidate = `${base === '' ? '' : `${base}/`}node_modules/${name}`;
        if (packages[candidate]) {
          if (!seen.has(candidate)) {
            seen.add(candidate);
            queue.push(candidate);
          }
          break;
        }
        if (base === '') break;
        const cut = base.lastIndexOf('node_modules/');
        base = cut <= 0 ? '' : base.slice(0, cut - 1);
      }
    }
  }
  return seen;
}

let lock;
const removedFromLock = [];
if (existsSync(lockFile) && removedPackages.length > 0) {
  lock = readJson(lockFile);
  const packages = lock.json.packages;
  if (!packages || !packages[''])
    fail(
      `${lockFile} is not a lockfile with a "packages" map (version 2 or 3)`,
    );
  const before = reachable(packages);
  for (const name of removedPackages) {
    for (const section of SECTIONS) {
      if (packages[''][section]) delete packages[''][section][name];
    }
  }
  const after = reachable(packages);
  // Entries nothing reached before are not this script's to judge.
  for (const path of Object.keys(packages)) {
    if (before.has(path) && !after.has(path)) {
      delete packages[path];
      removedFromLock.push(path);
    }
  }
  removedFromLock.sort();
  // lockfileVersion 2 repeats the tree in a legacy map npm 6 reads.
  if (lock.json.dependencies) {
    for (const path of removedFromLock) {
      const parts = path.split('node_modules/').slice(1);
      let node = lock.json;
      for (let i = 0; i < parts.length && node; i++) {
        const name = parts[i].replace(/\/$/, '');
        if (i === parts.length - 1) delete node.dependencies?.[name];
        else node = node.dependencies?.[name];
      }
    }
  }
}

if (!dryRun && removedPackages.length > 0) {
  write(packageFile, pkg.text, pkg.json);
  if (lock) write(lockFile, lock.text, lock.json);
}
process.stdout.write(
  `${JSON.stringify({ removedPlugins, removedPackages, removedFromLock })}\n`,
);
