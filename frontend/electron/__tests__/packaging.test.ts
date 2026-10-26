import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as path from 'path';

// Runs from electron/compile/__tests__; sources live two levels up.
const ELECTRON_DIR = path.resolve(__dirname, '..', '..');
const BUILDER_CONFIG = path.join(ELECTRON_DIR, '..', 'electron-builder.yml');

/** `files:` entries that ship compiled main-process code. */
function packagedPatterns(): string[] {
  const lines = fs.readFileSync(BUILDER_CONFIG, 'utf8').split('\n');
  return lines
    .map((line) => /^\s+-\s+"?([^"\s]+)"?\s*$/.exec(line)?.[1])
    .filter((entry): entry is string => !!entry && entry.startsWith('electron/compile/'));
}

/** Top-level electron/*.ts modules reachable from the main and preload entry points. */
function reachableModules(): Set<string> {
  const seen = new Set<string>();
  const queue = ['main', 'preload'];
  while (queue.length) {
    const name = queue.pop() as string;
    if (seen.has(name)) continue;
    seen.add(name);
    // Directory modules (./local-sync, ...) ship as a glob; don't walk into them.
    if (name.includes('/') || !fs.existsSync(path.join(ELECTRON_DIR, `${name}.ts`))) continue;
    const source = fs.readFileSync(path.join(ELECTRON_DIR, `${name}.ts`), 'utf8');
    for (const [, spec] of source.matchAll(/from '\.\/([^']+)'/g)) queue.push(spec);
  }
  return seen;
}

// electron-builder ships an explicit allow-list; a module missing from it only
// fails when the packaged app starts ("Cannot find module").
test('every module the main process loads is packaged', () => {
  const patterns = packagedPatterns();
  for (const mod of reachableModules()) {
    const [dir] = mod.split('/');
    const covered =
      patterns.includes(`electron/compile/${mod}.js`) ||
      patterns.includes(`electron/compile/${dir}/**/*.js`);
    assert.ok(covered, `electron/compile/${mod}.js is not in electron-builder.yml files`);
  }
});
