import { existsSync, readFileSync, readdirSync, statSync } from 'fs';
import { join, relative, resolve, sep } from 'path';
import type { ZodTypeAny } from 'zod/v3';

const CONTRACTS_ROOT = resolve(__dirname, '..', '..', '..', '..', '..', '..', 'contracts');
export const CONTRACTS_DIR = join(CONTRACTS_ROOT, 'harness');
const MODULES = resolve(__dirname, '..', '..', '..', 'src', 'modules');

/** An edition's schema overlay (codegen/extensions.py), when present: it adds, never redefines. */
export const OVERLAY_DIR = join(CONTRACTS_DIR, 'ee');

/** Each contract root: where its schemas are and where its zod is generated (contracts/harness/codegen/generate.py). */
const ROOTS = [
  {
    prefix: 'harness',
    // An overlay's harness groups generate beneath the root's own, under the same relative path.
    sources: [
      join(CONTRACTS_DIR, 'core'),
      join(CONTRACTS_DIR, 'ext'),
      ...[join(OVERLAY_DIR, 'core'), join(OVERLAY_DIR, 'ext')].filter((dir) => existsSync(dir)),
    ],
    generated: join(MODULES, 'agent-harness', 'contracts', 'generated'),
  },
  {
    prefix: 'control-plane',
    sources: [join(CONTRACTS_ROOT, 'control-plane')],
    generated: join(MODULES, 'control-plane', 'contracts', 'generated'),
  },
] as const;

export interface ContractSchema {
  /** Path relative to contracts/, e.g. harness/core/envelope.schema.json, control-plane/problem.schema.json. */
  path: string;
  title: string;
  examples: unknown[];
  invalid: Array<{ description: string; instance: unknown }>;
  writers?: string[];
  eventType?: string;
}

function walk(dir: string): string[] {
  return readdirSync(dir)
    .sort()
    .flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return walk(full);
      return name.endsWith('.schema.json') ? [full] : [];
    });
}

/** Every instance schema of every root (a schema with only $defs has no examples). */
export function loadContractSchemas(): ContractSchema[] {
  return ROOTS.flatMap((root) => root.sources.flatMap(walk))
    .map((file) => {
      const doc = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>;
      const invalidFile = file.replace(/\.schema\.json$/, '.invalid.json');
      let invalid: ContractSchema['invalid'];
      try {
        invalid = JSON.parse(readFileSync(invalidFile, 'utf8')) as ContractSchema['invalid'];
      } catch {
        invalid = [];
      }
      const properties = doc.properties as Record<string, { const?: string }> | undefined;
      const writers = doc['x-writer'] as string[] | undefined;
      return {
        path: relative(CONTRACTS_ROOT, file).split(sep).join('/'),
        title: doc.title as string,
        examples: (doc.examples as unknown[] | undefined) ?? [],
        invalid,
        ...(writers ? { writers, eventType: properties?.type?.const } : {}),
      };
    })
    .filter((s) => s.examples.length > 0);
}

/** The generated zod schema for a contract schema path (relative to contracts/). */
export function zodFor(schemaPath: string): ZodTypeAny {
  const root = ROOTS.find((r) => schemaPath.startsWith(`${r.prefix}/`));
  if (root === undefined) throw new Error(`no contract root for ${schemaPath}`);
  const parts = schemaPath.slice(root.prefix.length + 1).replace(/\.schema\.json$/, '').split('/');
  const stem = parts.pop()!.replace(/[._]/g, '-');
   
  const mod = require(join(root.generated, ...parts, stem)) as Record<string, ZodTypeAny>;
  const names = Object.keys(mod).filter((k) => k.endsWith('Schema'));
  if (names.length !== 1) throw new Error(`expected one schema export in ${schemaPath}, found ${names.join(',')}`);
  return mod[names[0]!]!;
}

const readJson = (file: string): unknown => JSON.parse(readFileSync(file, 'utf8'));

function overlayFiles(dir: string, suffix: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .sort()
    .flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return overlayFiles(full, suffix);
      return name.endsWith(suffix) ? [full] : [];
    });
}

/** The values the overlay adds to the enumeration at JSON pointer `at` of the schema `$id` (none without one). */
export function overlayEnumValues(id: string, at: string): string[] {
  return overlayFiles(OVERLAY_DIR, '.extends.json').flatMap((file) => {
    const extension = readJson(file) as { extends: string; add?: Array<{ at?: string; enum?: string[] }> };
    if (extension.extends !== id) return [];
    return (extension.add ?? []).flatMap((add) => ((add.at ?? '') === at ? (add.enum ?? []) : []));
  });
}

/** The event types the overlay adds to the catalog of `group` (`core`, `ext/pipeshub`). */
export function overlayEventTypes(group: string): string[] {
  return overlayFiles(join(OVERLAY_DIR, group), '.schema.json').flatMap((file) => {
    const doc = readJson(file) as { 'x-writer'?: string[]; properties?: { type?: { const?: string } } };
    const type = doc.properties?.type?.const;
    return doc['x-writer'] !== undefined && type !== undefined ? [type] : [];
  });
}

/** The error codes the overlay's catalog adds to that of `group`. */
export function overlayErrorCodes<T extends { code: string }>(group: string): T[] {
  const file = join(OVERLAY_DIR, group, 'errors.json');
  return existsSync(file) ? (readJson(file) as { codes: T[] }).codes : [];
}

/** A vector's sections with the entries the overlay's vector of the same name adds to them. */
export function withOverlayVector<T extends Record<string, unknown>>(name: string, base: T): T {
  const file = join(OVERLAY_DIR, 'vectors', name);
  if (!existsSync(file)) return base;
  const added = readJson(file) as Record<string, unknown>;
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(added)) {
    const own = base[key];
    if (Array.isArray(own) && Array.isArray(value)) {
      out[key] = [...own, ...value.filter((entry) => !own.includes(entry))];
    } else if (own !== null && typeof own === 'object' && !Array.isArray(own) && value !== null && typeof value === 'object') {
      out[key] = { ...(own as Record<string, unknown>), ...(value as Record<string, unknown>) };
    }
  }
  return out as T;
}
