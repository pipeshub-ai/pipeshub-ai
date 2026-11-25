// Generates zod (v3 API) schemas from one contract root with json-schema-to-zod.
// json-schema-to-zod does not resolve $ref or merge allOf, so each root schema is bundled first:
// refs are inlined (no recursive schemas exist; refs may cross roots) and an event's
// allOf[InlineEnvelope] is merged with the event's own narrower `type`/`writer`/`data` properties.
// Usage: node generate-zod.mjs <spec.json>   (cwd must be backend/nodejs/apps for module resolution)
// A root's `imports` are another root's schema directories generated into this one as well.
// spec: {root: "harness" | "control-plane", out, zodImport, types: "infer" | "emit", events, extra: {group: {file: text}}};
// `extra` holds generate.py's constant modules, written beside the schemas and re-exported by the barrel.
// `overlays` ([{dir, as, base}]) are an edition's schema directories for this root, generated under `as` with a barrel
// each; their refs may name any root's schemas. Their event types join the event catalog of the `base` barrel, the
// group they extend; a type that catalog already declares is refused.
// `extended` ({file: schema}) replaces what a file reads as: the schema with an edition overlay's additions.
// `types: "emit"` writes each type from the schema itself instead of `z.infer`: z.infer needs
// strictNullChecks to keep a property required, and the frontend compiles without it.
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CONTRACTS_ROOT = resolve(HERE, '..', '..');
const NODE_APP = resolve(CONTRACTS_ROOT, '..', 'backend', 'nodejs', 'apps');
const require = createRequire(join(NODE_APP, 'package.json'));
const { jsonSchemaToZod, parseSchema } = require('json-schema-to-zod');
const GENERATOR_VERSION = JSON.parse(
  readFileSync(join(NODE_APP, 'node_modules', 'json-schema-to-zod', 'package.json'), 'utf8'),
).version;
const PINNED_VERSION = '2.8.1';

if (GENERATOR_VERSION !== PINNED_VERSION) {
  throw new Error(`json-schema-to-zod ${PINNED_VERSION} required, found ${GENERATOR_VERSION}`);
}
if (!process.argv[2]) throw new Error('usage: generate-zod.mjs <spec.json>');

const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'));

/** Each root's schema directories; a group is a barrel. */
const ROOTS = {
  harness: {
    dir: join(CONTRACTS_ROOT, 'harness'),
    sources: ['core', 'ext'],
    group: (rel) => (rel.startsWith('core') ? 'core' : rel.split(sep).slice(0, 2).join('/')),
  },
  'control-plane': {
    dir: join(CONTRACTS_ROOT, 'control-plane'),
    sources: ['.'],
    group: () => '.',
    // The generic policy engine's types, which the control plane names but may not import from agent-harness
    // (03-interfaces §1): generated again here from the same schemas, under `policy/`.
    imports: [{ dir: join(CONTRACTS_ROOT, 'harness', 'core', 'policy'), as: 'policy' }],
  },
};

const root = ROOTS[spec.root];
if (!root) throw new Error(`unknown contract root ${spec.root}`);
const outDir = resolve(spec.out);

function walk(dir) {
  return readdirSync(dir)
    .sort()
    .flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return walk(full);
      return name.endsWith('.schema.json') ? [full] : [];
    });
}

const sourcesOf = (r) => r.sources.flatMap((s) => walk(join(r.dir, s)));
const imported = new Map((root.imports ?? []).flatMap((i) => walk(i.dir).map((f) => [f, join(i.as, relative(i.dir, f))])));
const overlays = spec.overlays ?? [];
const overlaid = new Map(
  overlays.flatMap((o) => walk(o.dir).map((f) => [f, { rel: join(o.as, relative(o.dir, f)), group: o.as, base: o.base }])),
);
const files = [...sourcesOf(root), ...imported.keys(), ...overlaid.keys()];
// `extended`: the schemas an overlay extends (codegen/extensions.py), as generate.py read them with the additions.
const extended = spec.extended ?? {};
// `refOverlays`: every overlay directory of the run, so a ref into another root's overlay resolves too.
const refOverlaid = (spec.refOverlays ?? []).flatMap((dir) => walk(dir));
const docs = new Map(
  [...Object.values(ROOTS).flatMap(sourcesOf), ...new Set([...overlaid.keys(), ...refOverlaid])].map((f) => [
    f,
    extended[f] ?? JSON.parse(readFileSync(f, 'utf8')),
  ]),
);
// A ref across roots names its target by `$id`; within a root, by relative path.
const byId = new Map([...docs].map(([f, doc]) => [doc.$id, f]));

function pointer(doc, frag) {
  if (!frag) return doc;
  return frag
    .replace(/^\//, '')
    .split('/')
    .reduce((node, part) => node[part.replace(/~1/g, '/').replace(/~0/g, '~')], doc);
}

const DROP = new Set(['$schema', '$id', '$defs', 'examples', 'x-writer', 'x-constant', 'title']);

function bundle(node, file, stack = []) {
  if (Array.isArray(node)) return node.map((n) => bundle(n, file, stack));
  if (node === null || typeof node !== 'object') return node;
  if (typeof node.$ref === 'string') {
    const [target, frag] = node.$ref.split('#');
    const targetFile = !target ? file : target.includes('://') ? byId.get(target) : resolve(dirname(file), target);
    if (targetFile === undefined) throw new Error(`unknown $id ${target} in ${file}`);
    const key = `${targetFile}#${frag ?? ''}`;
    if (stack.includes(key)) throw new Error(`recursive $ref not supported: ${key}`);
    const resolved = pointer(docs.get(targetFile), frag);
    if (resolved === undefined) throw new Error(`unresolved $ref ${node.$ref} in ${file}`);
    const { $ref: _ignored, ...siblings } = node;
    return bundle({ ...resolved, ...siblings }, targetFile, [...stack, key]);
  }
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    if (DROP.has(k)) continue;
    out[k] = k === 'properties' || k === 'patternProperties'
      ? Object.fromEntries(Object.entries(v).map(([pk, pv]) => [pk, bundle(pv, file, stack)]))
      : bundle(v, file, stack);
  }
  return mergeAllOf(out);
}

function mergeAllOf(schema) {
  if (!Array.isArray(schema.allOf)) return schema;
  const { allOf, ...own } = schema;
  let merged = {};
  for (const part of [...allOf, own]) {
    merged = {
      ...merged,
      ...part,
      properties: { ...(merged.properties ?? {}), ...(part.properties ?? {}) },
      required: [...new Set([...(merged.required ?? []), ...(part.required ?? [])])],
    };
  }
  return merged;
}

const literal = (value) => JSON.stringify(value);
const KEY = /^[A-Za-z_$][A-Za-z0-9_$]*$/;
const key = (name) => (KEY.test(name) ? name : JSON.stringify(name));

/** A TypeScript type for a bundled schema (refs inlined, allOf merged). */
function tsType(node) {
  if (node === true || node === undefined) return 'unknown';
  if (node === false) return 'never';
  if ('const' in node) return literal(node.const);
  if (Array.isArray(node.enum)) return node.enum.map(literal).join(' | ');
  const union = node.anyOf ?? node.oneOf;
  if (Array.isArray(union)) {
    const { anyOf: _a, oneOf: _o, ...rest } = node;
    const base = Object.keys(rest).some((k) => k === 'type' || k === 'properties') ? [tsType(rest)] : [];
    const parts = union.map((part) => `(${tsType(part)})`);
    return base.length > 0 ? `${base[0]} & (${parts.join(' | ')})` : parts.join(' | ');
  }
  if (Array.isArray(node.type)) {
    return node.type.map((t) => `(${tsType({ ...node, type: t })})`).join(' | ');
  }
  switch (node.type) {
    case 'string':
      return 'string';
    case 'integer':
    case 'number':
      return 'number';
    case 'boolean':
      return 'boolean';
    case 'null':
      return 'null';
    case 'array':
      return `Array<${tsType(node.items)}>`;
    default:
      break;
  }
  if (node.type === 'object' || node.properties || node.additionalProperties || node.patternProperties) {
    const required = new Set(node.required ?? []);
    const fields = Object.entries(node.properties ?? {}).map(
      ([name, value]) => `${key(name)}${required.has(name) ? '' : '?'}: ${tsType(value)}`,
    );
    const extra = [
      ...(node.additionalProperties && node.additionalProperties !== true ? [node.additionalProperties] : []),
      ...Object.values(node.patternProperties ?? {}),
    ];
    const open = node.additionalProperties === true || (node.additionalProperties === undefined && !node.properties);
    const index =
      extra.length > 0 ? `[key: string]: ${extra.map((e) => `(${tsType(e)})`).join(' | ')}` : open ? '[key: string]: unknown' : null;
    if (fields.length === 0) return index === null ? 'Record<string, never>' : `{ ${index} }`;
    const body = `{ ${fields.join('; ')} }`;
    return index === null ? body : `${body} & { ${index} }`;
  }
  return 'unknown';
}

// JSON Schema's minLength/maxLength count code points, as jsonschema and pydantic do; zod's .min()/.max()
// count UTF-16 code units, so a character outside the BMP (an emoji) counts twice and a string the
// schema admits is rejected. Every string length bound is checked by the shared helper instead.
const CODE_POINTS_MODULE = 'code-points';
const CODE_POINTS = 'codePointLength';

const HELPER = (zodImport) => `${HEADER}import type { RefinementCtx } from ${JSON.stringify(zodImport)}

/** A refinement that bounds a string's length in Unicode code points, the unit JSON Schema counts. */
export function ${CODE_POINTS}(min: number | undefined, max: number | undefined) {
  return (value: string, ctx: RefinementCtx): void => {
    let length = value.length;
    for (let i = 0; i < value.length - 1; i += 1) {
      const unit = value.charCodeAt(i);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const next = value.charCodeAt(i + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          length -= 1;
          i += 1;
        }
      }
    }
    if (min !== undefined && length < min) {
      ctx.addIssue({ code: "too_small", minimum: min, type: "string", inclusive: true, exact: false, message: \`String must contain at least \${min} character(s)\` });
    }
    if (max !== undefined && length > max) {
      ctx.addIssue({ code: "too_big", maximum: max, type: "string", inclusive: true, exact: false, message: \`String must contain at most \${max} character(s)\` });
    }
  };
}
`;

/** parserOverride: a string with a length bound, parsed without it and refined by code points. */
function codePointBounds(schema, refs) {
  if (schema.type !== 'string' || !('minLength' in schema || 'maxLength' in schema)) return undefined;
  if ('default' in schema || schema.readOnly) throw new Error('codePointBounds: default/readOnly on a bounded string is not supported');
  const { minLength, maxLength, ...rest } = schema;
  refs.usesCodePoints.value = true;
  const describe = schema.description === undefined ? '' : `.describe(${JSON.stringify(schema.description)})`;
  return `${parseSchema(rest, refs, true)}.superRefine(${CODE_POINTS}(${minLength}, ${maxLength}))${describe}`;
}

const camel = (s) => s.charAt(0).toLowerCase() + s.slice(1);
const stem = (file) => file.split(sep).pop().replace(/\.schema\.json$/, '').replace(/[._]/g, '-');
const posix = (p) => p.split(sep).join('/');
const ZOD_IMPORT = /^import \{ z \} from "zod"$/m;

const HEADER = [
  `// generated by contracts/harness/codegen/generate-zod.mjs (json-schema-to-zod ${PINNED_VERSION}) -- do not edit`,
  '/* eslint-disable */',
  '',
].join('\n');

const outputs = new Map();
const catalogs = new Map();
const catalogOf = (group) => {
  if (!catalogs.has(group)) catalogs.set(group, { dir: group, events: [], exports: [] });
  return catalogs.get(group);
};

for (const file of files) {
  const doc = docs.get(file);
  if (!doc.type && !doc.anyOf && !doc.allOf) continue;
  const rel = overlaid.get(file)?.rel ?? imported.get(file) ?? relative(root.dir, file);
  const group = overlaid.get(file)?.group ?? root.group(rel);
  const outRel = posix(join(dirname(rel), `${stem(file)}.ts`));
  const schemaName = `${camel(doc.title)}Schema`;
  const bundled = bundle(doc, file);
  const emit = spec.types === 'emit';
  const usesCodePoints = { value: false };
  let code = jsonSchemaToZod(bundled, {
    name: schemaName,
    module: 'esm',
    ...(emit ? {} : { type: doc.title }),
    zodVersion: 3,
    parserOverride: codePointBounds,
    usesCodePoints,
  });
  const helperPath = posix(relative(dirname(outRel), CODE_POINTS_MODULE));
  const helperImport = usesCodePoints.value
    ? `\nimport { ${CODE_POINTS} } from ${JSON.stringify(helperPath.startsWith('.') ? helperPath : `./${helperPath}`)}`
    : '';
  code = code.replace(ZOD_IMPORT, `import { z } from ${JSON.stringify(spec.zodImport)}${helperImport}`);
  const typeLine = emit ? `\nexport type ${doc.title} = ${tsType(bundled)}\n` : '\n';
  outputs.set(outRel, HEADER + code.trimEnd() + typeLine);
  const eventType = doc['x-writer'] ? doc.properties?.type?.const : undefined;
  const cat = catalogOf(group);
  cat.exports.push({ path: './' + posix(relative(group, outRel)).replace(/\.ts$/, ''), schemaName, title: doc.title });
  if (eventType) {
    const base = overlaid.get(file)?.base ?? group;
    const events = catalogOf(base).events;
    if (events.some((e) => e.type === eventType || e.schemaName === schemaName)) {
      throw new Error(`${file}: event ${eventType} (${schemaName}) is already defined`);
    }
    const path = posix(relative(base, outRel)).replace(/\.ts$/, '');
    events.push({ type: eventType, writers: doc['x-writer'], schemaName, path: path.startsWith('.') ? path : `./${path}` });
  }
}

outputs.set(`${CODE_POINTS_MODULE}.ts`, HELPER(spec.zodImport));

for (const [group, modules] of Object.entries(spec.extra ?? {})) {
  for (const [name, text] of Object.entries(modules)) {
    outputs.set(posix(join(group, name)), text);
  }
  catalogOf(group).constants = Object.keys(modules).sort().map((name) => './' + name.replace(/\.ts$/, ''));
}

for (const cat of catalogs.values()) {
  cat.events.sort((a, b) => (a.type < b.type ? -1 : a.type > b.type ? 1 : 0));
  const lines = [HEADER.trimEnd()];
  if (spec.events) lines.push(`import type { ZodTypeAny } from '${spec.zodImport}';`);
  const eventSchemas = new Set(cat.events.map((e) => e.schemaName));
  const own = new Set(cat.exports.map((e) => e.schemaName));
  for (const e of cat.exports) {
    if (eventSchemas.has(e.schemaName)) lines.push(`import { ${e.schemaName} } from '${e.path}';`);
  }
  // An overlay's event types, from its barrel's modules beneath this one.
  for (const e of cat.events) if (!own.has(e.schemaName)) lines.push(`import { ${e.schemaName} } from '${e.path}';`);
  lines.push('');
  for (const e of cat.exports) lines.push(`export { ${e.schemaName} } from '${e.path}';`);
  for (const e of cat.exports) lines.push(`export type { ${e.title} } from '${e.path}';`);
  for (const path of cat.constants ?? []) lines.push(`export * from '${path}';`);
  if (spec.events) {
    lines.push('', 'export type EventWriter = "node" | "worker";', '');
    lines.push('export const EVENT_WRITERS: Readonly<Record<string, readonly EventWriter[]>> = Object.freeze({');
    for (const e of cat.events) lines.push(`  ${JSON.stringify(e.type)}: Object.freeze(${JSON.stringify(e.writers)}) as readonly EventWriter[],`);
    lines.push('});', '');
    lines.push('export const EVENT_SCHEMAS: Readonly<Record<string, ZodTypeAny>> = Object.freeze({');
    for (const e of cat.events) lines.push(`  ${JSON.stringify(e.type)}: ${e.schemaName},`);
    lines.push('});');
  }
  lines.push('');
  outputs.set(posix(join(cat.dir, 'index.ts')), lines.join('\n'));
}

rmSync(outDir, { recursive: true, force: true });
for (const [rel, text] of [...outputs.entries()].sort()) {
  const target = join(outDir, rel);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, text);
}
