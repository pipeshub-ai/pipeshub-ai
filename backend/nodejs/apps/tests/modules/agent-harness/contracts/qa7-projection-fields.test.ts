import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, resolve } from 'path';
import { CONTRACTS_DIR } from '../../../support/harness/contracts';

/**
 * QA7-43 (PRJ-24, partial): every field Node's projections read off a
 * payload by name (`data['field']`) is a property some generated event schema
 * declares, so renaming a field in a schema fails here instead of silently
 * projecting `undefined`. Typed projections over the generated event types
 * stay WP-11's.
 */
const PROJECTION = resolve(__dirname, '..', '..', '..', '..', 'src', 'modules', 'agent-harness', 'projection');

/** Reads off payloads no event schema types yet; each names where its shape is defined. */
const UNTYPED_READS: Readonly<Record<string, string>> = {
  change: 'x.pipeshub.engine.progress data of a file/diff line (open object; app/harness/engines/progress.py)',
  diff: 'x.pipeshub.engine.progress data of a diff line (open object; app/harness/engines/progress.py)',
  stream: 'engine.progress output data and the live command_output frame (vectors/activity-frames.json)',
  dropped: 'the live command_output frame (vectors/activity-frames.json)',
  diffTruncated:
    'x.pipeshub.engine.progress data of an approval line, its effect preview (open object; harness_core/effects/preview.py)',
  costEstimate:
    'x.pipeshub.engine.progress data of an approval line, its effect preview (open object; harness_core/effects/preview.py)',
};

function schemaFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return schemaFiles(full);
    return name.endsWith('.schema.json') ? [full] : [];
  });
}

function propertiesOf(node: unknown, into: Set<string>): Set<string> {
  if (Array.isArray(node)) node.forEach((n) => propertiesOf(n, into));
  else if (node !== null && typeof node === 'object') {
    const record = node as Record<string, unknown>;
    const props = record['properties'];
    if (props !== null && typeof props === 'object') Object.keys(props).forEach((k) => into.add(k));
    Object.values(record).forEach((v) => propertiesOf(v, into));
  }
  return into;
}

const declared = schemaFiles(CONTRACTS_DIR)
  .filter((path) => path.includes(join('events', '')))
  .reduce((into, path) => propertiesOf(JSON.parse(readFileSync(path, 'utf8')), into), new Set<string>());

const READ = /\bdata\??\.?\[\s*'([A-Za-z_][A-Za-z0-9_]*)'\s*\]/g;
const reads = new Map<string, string>();
for (const name of readdirSync(PROJECTION).filter((n) => n.endsWith('.ts'))) {
  for (const match of readFileSync(join(PROJECTION, name), 'utf8').matchAll(READ)) {
    reads.set(match[1]!, name);
  }
}

describe('QA7-43 projections read declared event fields', () => {
  it('the scan finds the projections’ reads', () => {
    expect(reads.size).to.be.greaterThan(40);
    expect(declared.size).to.be.greaterThan(100);
  });

  it('every field read is declared by an event schema, or reviewed as untyped', () => {
    const undeclared = [...reads.keys()].filter((f) => !declared.has(f) && !(f in UNTYPED_READS));
    expect(undeclared.map((f) => `${reads.get(f)}: ${f}`)).to.deep.equal([]);
  });

  it('every reviewed untyped read is still read and still undeclared', () => {
    for (const field of Object.keys(UNTYPED_READS)) {
      expect(reads.has(field), field).to.equal(true);
      expect(declared.has(field), `${field} is declared now: drop it from UNTYPED_READS`).to.equal(false);
    }
  });
});
