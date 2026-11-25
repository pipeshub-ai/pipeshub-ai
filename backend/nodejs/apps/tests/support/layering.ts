import { existsSync, readFileSync, readdirSync, statSync } from 'fs';
import { dirname, join, relative, resolve, sep } from 'path';
import * as ts from 'typescript';

/**
 * Layering rules for one Node module (docs/control-plane/03-interfaces.md §1,
 * NFR-46), checked from the import graph TypeScript itself reads. A layer is a
 * top-level directory of the module; `modules/<name>` are separate layers that
 * may not import one another.
 */
export interface LayerRules {
  /** The module's directory, relative to `src`, e.g. `modules/control-plane`. */
  module: string;
  /** Layer of a path relative to the module, or null when no layer owns it. */
  layerOf(path: string): string | null;
  /** The layers each layer may import inside the module. */
  allows(from: string, to: string): boolean;
  /** Whether a layer may import from the rest of `src` (libs, other modules). */
  mayLeaveModule(layer: string): boolean;
  /** npm packages a layer may import; null: any. */
  packages(layer: string): readonly string[] | null;
  /** Modules (relative to `src`) this module never imports. */
  forbiddenModules: readonly string[];
  /** The one file other modules may import this module through. */
  barrel: string;
  /** Directories (relative to `src`) outside the module that are one of its layers: its Mongo repositories are its adapters. */
  outsideLayers?: Readonly<Record<string, string>>;
  /** Files (relative to `src`) in an outside layer that other code may import too: its store constructors. */
  outsideBarrels?: readonly string[];
}

export interface Violation {
  file: string;
  imports: string;
  rule: string;
}

function sources(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .sort()
    .flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return sources(full);
      return name.endsWith('.ts') && !name.endsWith('.d.ts') ? [full] : [];
    });
}

function resolveImport(from: string, specifier: string): string | null {
  const base = resolve(dirname(from), specifier);
  for (const candidate of [base, `${base}.ts`, join(base, 'index.ts')]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

const posix = (path: string): string => path.split(sep).join('/');
const packageName = (specifier: string): string =>
  specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0]!;

function importsOf(file: string): string[] {
  return ts.preProcessFile(readFileSync(file, 'utf8'), true, true).importedFiles.map((f) => f.fileName);
}

/** Every import in `src` that breaks the module's rules. */
export function layeringViolations(src: string, rules: LayerRules): Violation[] {
  const moduleDir = join(src, rules.module);
  const barrels = new Set([join(moduleDir, rules.barrel), ...(rules.outsideBarrels ?? []).map((f) => join(src, f))]);
  const violations: Violation[] = [];
  const outside = Object.entries(rules.outsideLayers ?? {}).map(([dir, layer]) => [join(src, dir) + sep, layer] as const);
  const outsideLayer = (file: string): string | null => outside.find(([dir]) => file.startsWith(dir))?.[1] ?? null;
  const isInside = (file: string): boolean => file.startsWith(moduleDir + sep) || outsideLayer(file) !== null;
  const layerAt = (file: string): string | null => outsideLayer(file) ?? rules.layerOf(posix(relative(moduleDir, file)));
  for (const file of sources(src)) {
    const inside = isInside(file);
    const at = posix(relative(src, file));
    const layer = inside ? layerAt(file) : null;
    if (inside && layer === null) {
      violations.push({ file: at, imports: '', rule: 'a file outside the known layers' });
      continue;
    }
    for (const specifier of importsOf(file)) {
      if (!specifier.startsWith('.')) {
        const allowed = inside ? rules.packages(layer!) : null;
        if (allowed !== null && !allowed.includes(packageName(specifier))) {
          violations.push({ file: at, imports: specifier, rule: `${layer} imports no package but ${allowed.join(', ') || 'none'}` });
        }
        continue;
      }
      const target = resolveImport(file, specifier);
      if (target === null) continue;
      const targetInside = isInside(target);
      if (!inside) {
        if (targetInside && !barrels.has(target)) {
          violations.push({ file: at, imports: specifier, rule: `other modules import ${rules.module} only through ${rules.barrel}` });
        }
        continue;
      }
      if (targetInside) {
        const to = layerAt(target);
        if (to !== null && !rules.allows(layer!, to)) {
          violations.push({ file: at, imports: specifier, rule: `${layer} may not import ${to}` });
        }
        continue;
      }
      const targetAt = posix(relative(src, target));
      if (rules.forbiddenModules.some((m) => targetAt.startsWith(`${m}/`))) {
        violations.push({ file: at, imports: specifier, rule: `${rules.module} never imports ${targetAt.split('/').slice(0, 2).join('/')}` });
      } else if (!rules.mayLeaveModule(layer!)) {
        violations.push({ file: at, imports: specifier, rule: `${layer} imports nothing outside ${rules.module}` });
      }
    }
  }
  return violations;
}

const PURE = new Set(['contracts', 'domain', 'ports']);
const INSIDE: Readonly<Record<string, readonly string[]>> = {
  contracts: ['contracts'],
  domain: ['contracts', 'domain'],
  ports: ['contracts', 'domain', 'ports'],
  application: ['contracts', 'domain', 'ports', 'application'],
  adapters: ['contracts', 'domain', 'ports', 'adapters'],
  transport: ['contracts', 'domain', 'ports', 'application', 'transport'],
};

/** The control plane's rules (03-interfaces §1). */
export const CONTROL_PLANE_RULES: LayerRules = {
  module: 'modules/control-plane',
  barrel: 'index.ts',
  forbiddenModules: ['modules/agent-harness'],
  outsideLayers: { 'libs/repositories/control-plane': 'adapters' },
  outsideBarrels: ['libs/repositories/control-plane/stores.ts'],
  layerOf(path) {
    const [top, second] = path.split('/');
    if (path === 'index.ts') return 'barrel';
    if (path === 'composition.ts' || top === 'container') return 'composition';
    if (top === 'contracts' && second === 'generated') return 'contracts';
    if (top === 'modules' && second !== undefined && path.split('/').length > 2) return `module:${second}`;
    if (top === 'facades' || top === 'transport') return 'transport';
    return top !== undefined && top in INSIDE && path.includes('/') ? top : null;
  },
  allows(from, to) {
    if (from === 'barrel' || from === 'composition') return true;
    if (from.startsWith('module:')) return to === from || PURE.has(to);
    return (INSIDE[from] ?? []).includes(to);
  },
  mayLeaveModule: (layer) => !PURE.has(layer),
  packages: (layer) => (PURE.has(layer) ? ['zod'] : null),
};
