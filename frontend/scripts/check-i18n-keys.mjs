import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPolicy, readLocale } from './check-i18n-parity.mjs';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SEARCH_DIRS = ['app', 'lib'];
const SKIP_DIRS = new Set(['node_modules', '.next', '__tests__', 'coverage']);
const SOURCE_FILE = /\.tsx?$/;

/** `t('a.b.c')` and `<Trans i18nKey="a.b.c">`, in every quoting JSX allows. */
const KEY_PATTERNS = [
  /\bt\(\s*'([A-Za-z][\w.-]*)'/g,
  /\bt\(\s*"([A-Za-z][\w.-]*)"/g,
  /\bi18nKey=\s*"([A-Za-z][\w.-]*)"/g,
  /\bi18nKey=\s*'([A-Za-z][\w.-]*)'/g,
  /\bi18nKey=\s*\{\s*'([A-Za-z][\w.-]*)'\s*\}/g,
  /\bi18nKey=\s*\{\s*"([A-Za-z][\w.-]*)"\s*\}/g,
];

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) yield* sourceFiles(full);
    else if (SOURCE_FILE.test(entry) && !/\.test\.tsx?$/.test(entry)) yield full;
  }
}

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

function resolves(catalogue, key) {
  const direct = key.split('.').reduce((node, part) => (node == null ? node : node[part]), catalogue);
  if (direct !== undefined) return true;
  // A counted call resolves through a plural group rather than the bare key.
  const parts = key.split('.');
  const parent = parts.slice(0, -1).reduce((node, part) => (node == null ? node : node[part]), catalogue);
  if (parent == null || typeof parent !== 'object') return false;
  const leaf = parts[parts.length - 1];
  return Object.keys(parent).some((k) => PLURAL_SUFFIX.test(k) && k.replace(PLURAL_SUFFIX, '') === leaf);
}

export function findUnresolvedKeys(catalogue, files) {
  const unresolved = [];
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    const seen = new Set();
    for (const pattern of KEY_PATTERNS) {
      for (const match of text.matchAll(pattern)) {
        const key = match[1];
        // Interpolated or single-segment identifiers are not catalogue paths.
        if (seen.has(key) || !key.includes('.')) continue;
        seen.add(key);
        if (!resolves(catalogue, key)) unresolved.push({ file, key });
      }
    }
  }
  return unresolved;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const policy = loadPolicy();
    const catalogue = readLocale(policy.source);
    const files = SEARCH_DIRS.flatMap((dir) => [...sourceFiles(join(ROOT, dir))]);
    const unresolved = findUnresolvedKeys(catalogue, files);

    console.log(`Scanned ${files.length} source files for translation keys against ${policy.source}.`);
    if (unresolved.length === 0) {
      console.log('Every literal key resolves.');
    } else {
      console.error(`${unresolved.length} key(s) do not resolve:`);
      for (const { file, key } of unresolved) {
        console.error(`  ${relative(ROOT, file).replace(/\\/g, '/')}: ${key}`);
      }
    }
    process.exitCode = unresolved.length ? 1 : 0;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
