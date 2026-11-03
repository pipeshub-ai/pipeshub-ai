import { readFileSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const valueType = (value) => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;

const PLURAL_SUFFIX = /^(.*?)(_ordinal)?_(zero|one|two|few|many|other)$/;

/** Splits `items_ordinal_other` into its group (`items_ordinal`), rule type and category. */
function pluralParts(key) {
  const match = PLURAL_SUFFIX.exec(key);
  if (!match) return null;
  return { group: `${match[1]}${match[2] ?? ''}`, type: match[2] ? 'ordinal' : 'cardinal', category: match[3] };
}

const categoryCache = new Map();
function pluralCategories(language, type) {
  const cacheKey = `${language}|${type}`;
  if (!categoryCache.has(cacheKey)) {
    categoryCache.set(cacheKey, new Intl.PluralRules(language, { type }).resolvedOptions().pluralCategories);
  }
  return categoryCache.get(cacheKey);
}

function leafPaths(value, path = '') {
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, child]) => leafPaths(child, path ? `${path}.${key}` : key));
  }
  return [path];
}

function interpolationVariables(value) {
  return [...new Set([...value.matchAll(/{{-?\s*([^{}]+?)\s*}}/g)]
    .map((match) => match[1].split(',')[0].trim()))].sort();
}

/**
 * Compares a locale against the source catalogue.
 *
 * Plural keys are judged against the target language's own CLDR categories, not
 * against the source's: Chinese and Korean need only `_other`, Spanish also needs
 * `_many`, so a literal key-by-key diff reports differences that are not errors.
 */
export function checkLocaleParity(source, target, targetLanguage = 'de-DE') {
  const errors = { missing: [], extra: [], types: [], placeholders: [], plurals: [] };

  function visit(expected, actual, path = '') {
    const expectedType = valueType(expected);
    const actualType = valueType(actual);
    if (expectedType !== actualType) {
      errors.types.push(`${path}: ${expectedType} / ${actualType}`);
      return;
    }
    if (expectedType === 'string') {
      const expectedVariables = interpolationVariables(expected);
      const actualVariables = interpolationVariables(actual);
      if (JSON.stringify(expectedVariables) !== JSON.stringify(actualVariables)) {
        errors.placeholders.push(`${path}: ${expectedVariables.join(', ')} / ${actualVariables.join(', ')}`);
      }
      return;
    }
    if (expected === null || typeof expected !== 'object') return;

    const prefix = path ? `${path}.` : '';
    const groups = new Map();
    for (const key of Object.keys(expected)) {
      const parts = pluralParts(key);
      if (parts) groups.set(parts.group, parts.type);
    }

    for (const [key, value] of Object.entries(expected)) {
      const childPath = `${prefix}${key}`;
      // Plural variants are covered by the group check below, which knows which
      // categories this language actually uses.
      if (pluralParts(key)) continue;
      if (!Object.hasOwn(actual, key)) {
        if (value !== null && typeof value === 'object') {
          // Walk the absent subtree against an empty container rather than
          // flattening it, so plural groups inside it are still judged by this
          // language's categories instead of the source's.
          const before = errors.missing.length + errors.plurals.length;
          visit(value, Array.isArray(value) ? [] : {}, childPath);
          if (errors.missing.length + errors.plurals.length === before) errors.missing.push(childPath);
        } else {
          errors.missing.push(childPath);
        }
      } else {
        visit(value, actual[key], childPath);
      }
    }

    for (const [key, value] of Object.entries(actual)) {
      if (Object.hasOwn(expected, key)) continue;
      const parts = pluralParts(key);
      // A category this language needs is legitimate even when the source lacks it.
      if (parts && groups.has(parts.group)) {
        const allowed = [...pluralCategories(targetLanguage, groups.get(parts.group)), 'zero'];
        if (allowed.includes(parts.category)) continue;
      }
      const childPath = `${prefix}${key}`;
      const extra = leafPaths(value, childPath);
      errors.extra.push(...(extra.length ? extra : [childPath]));
    }

    for (const [group, type] of groups) {
      // The source may not carry the category this language needs, so compare
      // placeholders against whichever variant it does define.
      const reference = Object.entries(expected)
        .find(([key, value]) => typeof value === 'string' && pluralParts(key)?.group === group)?.[1];
      const required = pluralCategories(targetLanguage, type);
      for (const category of required) {
        const key = `${group}_${category}`;
        if (typeof actual[key] !== 'string') {
          errors.plurals.push(`${prefix}${key}: missing ${type} string`);
        } else if (typeof expected[key] === 'string' || reference !== undefined) {
          visit(expected[key] ?? reference, actual[key], `${prefix}${key}`);
        }
      }

      // An override for a category this language does not require, such as
      // `_zero`, stays optional — but once it is there it still has to work.
      for (const key of Object.keys(actual)) {
        const parts = pluralParts(key);
        if (!parts || parts.group !== group || required.includes(parts.category)) continue;
        if (typeof expected[key] === 'string') {
          visit(expected[key], actual[key], `${prefix}${key}`);
          continue;
        }
        if (typeof actual[key] !== 'string') {
          errors.types.push(`${prefix}${key}: string / ${valueType(actual[key])}`);
          continue;
        }
        // Compared against a sibling rather than like-for-like, so it may drop a
        // variable ("None yet") but never introduce one the group does not have.
        if (reference === undefined) continue;
        const allowed = interpolationVariables(reference);
        const unknown = interpolationVariables(actual[key]).filter((v) => !allowed.includes(v));
        if (unknown.length > 0) {
          errors.placeholders.push(`${prefix}${key}: ${allowed.join(', ')} / ${unknown.join(', ')} unknown`);
        }
      }
    }
  }

  visit(source, target);
  return {
    sourceLeaves: leafPaths(source).length,
    targetLeaves: leafPaths(target).length,
    errors,
    valid: Object.values(errors).every((items) => items.length === 0),
  };
}

const LOCALE_DIR = new URL('../lib/i18n/locales/', import.meta.url);

export function loadPolicy() {
  return JSON.parse(readFileSync(new URL('../lib/i18n/locale-policy.json', import.meta.url), 'utf8'));
}

export function localeNames() {
  return readdirSync(fileURLToPath(LOCALE_DIR))
    .filter((name) => name.endsWith('.json'))
    .map((name) => name.slice(0, -'.json'.length))
    .sort();
}

export function readLocale(name) {
  return JSON.parse(readFileSync(join(fileURLToPath(LOCALE_DIR), `${name}.json`), 'utf8'));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const policy = loadPolicy();
    const only = process.argv.slice(2).filter((arg) => !arg.startsWith('-'));
    const source = readLocale(policy.source);
    const targets = (only.length ? only : localeNames()).filter((name) => name !== policy.source);

    let failed = 0;
    for (const language of targets) {
      const result = checkLocaleParity(source, readLocale(language), language);
      const gated = policy.gated.includes(language);
      const total = Object.values(result.errors).reduce((sum, items) => sum + items.length, 0);
      console.log(
        `${result.valid ? 'ok  ' : gated ? 'FAIL' : 'warn'}  ${language.padEnd(24)} ` +
        `${result.targetLeaves}/${result.sourceLeaves} leaves` +
        (total ? `  ${Object.entries(result.errors).filter(([, i]) => i.length).map(([c, i]) => `${c} ${i.length}`).join(', ')}` : '')
      );
      if (!result.valid) {
        for (const [category, items] of Object.entries(result.errors)) {
          for (const item of items) console.error(`        ${category}: ${item}`);
        }
        if (gated) failed += 1;
      }
    }
    process.exitCode = failed ? 1 : 0;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
