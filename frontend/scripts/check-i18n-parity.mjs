import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const valueType = (value) => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;

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

    for (const [key, value] of Object.entries(expected)) {
      const childPath = path ? `${path}.${key}` : key;
      if (!Object.hasOwn(actual, key)) {
        const missing = leafPaths(value, childPath);
        errors.missing.push(...(missing.length ? missing : [childPath]));
      } else {
        visit(value, actual[key], childPath);
      }
    }
    for (const [key, value] of Object.entries(actual)) {
      if (!Object.hasOwn(expected, key)) {
        const childPath = path ? `${path}.${key}` : key;
        const extra = leafPaths(value, childPath);
        errors.extra.push(...(extra.length ? extra : [childPath]));
      }
    }

    const pluralGroups = new Map();
    for (const key of Object.keys(expected)) {
      const match = /^(.*?)(_ordinal)?_(zero|one|two|few|many|other)$/.exec(key);
      if (match) pluralGroups.set(`${match[1]}${match[2] ?? ''}`, match[2] ? 'ordinal' : 'cardinal');
    }
    for (const [base, type] of pluralGroups) {
      const categories = new Intl.PluralRules(targetLanguage, { type }).resolvedOptions().pluralCategories;
      for (const category of categories) {
        const key = `${base}_${category}`;
        if (typeof actual[key] !== 'string') {
          errors.plurals.push(`${path ? `${path}.` : ''}${key}: missing ${type} string`);
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

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const source = JSON.parse(readFileSync(new URL('../lib/i18n/locales/en-US.json', import.meta.url), 'utf8'));
    const target = JSON.parse(readFileSync(new URL('../lib/i18n/locales/de-DE.json', import.meta.url), 'utf8'));
    const result = checkLocaleParity(source, target);
    console.log(`Locale parity: en-US ${result.sourceLeaves} leaves / de-DE ${result.targetLeaves} leaves`);
    for (const [category, items] of Object.entries(result.errors)) {
      console.log(`${category}: ${items.length}`);
      for (const item of items) console.error(`  ${item}`);
    }
    process.exitCode = result.valid ? 0 : 1;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
