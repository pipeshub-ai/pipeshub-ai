import { expect } from 'chai';
import { existsSync, readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import * as coreEnums from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import * as extEnums from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import * as modelAccess from '../../../../src/modules/agent-harness/domain/model-access';
import {
  NODE_AUTHORED_YIELD_REASONS,
  RUN_STATUSES,
} from '../../../../src/modules/agent-harness/domain/execution';
import {
  commitRunSchema,
  spawnChildRunSchema,
} from '../../../../src/modules/agent-harness/transport/schemas';
import { CONTRACTS_DIR, overlayEnumValues } from '../../../support/harness/contracts';

type Defs = Record<string, { 'x-constant'?: string; enum?: string[] }>;

const files = (d: string, suffix: string): string[] =>
  existsSync(d)
    ? readdirSync(d).flatMap((name) => {
        const full = join(d, name);
        if (statSync(full).isDirectory()) return files(full, suffix);
        return name.endsWith(suffix) ? [full] : [];
      })
    : [];

/**
 * Every `x-constant` enumeration of a schema directory: constant name -> values; with the `$defs` and values an
 * edition's overlay adds to one of its schemas (`contracts/harness/ee/**\/*.extends.json`, codegen/extensions.py).
 */
function declared(dir: string): Map<string, string[]> {
  const found = new Map<string, string[]>();
  const ids = new Set<string>();
  const add = (defs: Defs, id?: string): void => {
    for (const [name, def] of Object.entries(defs)) {
      if (def['x-constant'] === undefined) continue;
      const added = id === undefined ? [] : overlayEnumValues(id, `/$defs/${name}`);
      found.set(def['x-constant'], [...(def.enum ?? []), ...added]);
    }
  };
  for (const full of files(dir, '.schema.json')) {
    const schema = JSON.parse(readFileSync(full, 'utf8')) as { $id: string; $defs?: Defs };
    ids.add(schema.$id);
    add(schema.$defs ?? {}, schema.$id);
  }
  for (const full of files(join(CONTRACTS_DIR, 'ee'), '.extends.json')) {
    const extension = JSON.parse(readFileSync(full, 'utf8')) as { extends: string; $defs?: Defs };
    if (ids.has(extension.extends)) add(extension.$defs ?? {});
  }
  return found;
}

const commit = (transition: Record<string, unknown>) =>
  commitRunSchema.safeParse({
    params: { runId: 'run-1' },
    body: { commandId: 'c-1', attemptId: 'a-1', fence: 1, transition },
  }).success;

describe('agent-harness contracts: generated constants (CPD-0)', () => {
  for (const [label, dir, generated] of [
    ['core', join(CONTRACTS_DIR, 'core'), coreEnums],
    ['ext/pipeshub', join(CONTRACTS_DIR, 'ext', 'pipeshub'), extEnums],
  ] as const) {
    it(`CPD0-14 ${label}: every x-constant enumeration is emitted with the schema's values`, () => {
      const schema = declared(dir);
      expect(schema.size).to.be.greaterThan(1);
      const emitted = Object.fromEntries(
        Object.entries(generated).filter(([name]) => /^[A-Z][A-Z0-9_]*$/.test(name)),
      );
      expect(Object.keys(emitted).sort()).to.deep.equal([...schema.keys()].sort());
      for (const [name, values] of schema) {
        expect([...(emitted[name] as readonly string[])], name).to.deep.equal(values);
        expect(Object.isFrozen(emitted[name]), name).to.equal(true);
      }
    });
  }

  it('CPD0-21 the worker may suspend for every suspend reason', () => {
    for (const reason of coreEnums.SUSPEND_REASONS) {
      expect(commit({ status: 'paused', detail: { kind: 'suspend', reason } }), reason).to.equal(true);
    }
    expect(commit({ status: 'paused', detail: { kind: 'suspend', reason: 'nap' } })).to.equal(false);
  });

  it('CPD0-21 the worker may yield for every reason but the Node-authored one', () => {
    const notBefore = '2026-10-09T00:00:00.000Z';
    for (const reason of coreEnums.YIELD_REASONS) {
      const accepted = commit({ status: 'queued', detail: { kind: 'yield', reason, notBefore } });
      expect(accepted, reason).to.equal(!(NODE_AUTHORED_YIELD_REASONS as readonly string[]).includes(reason));
    }
    expect(NODE_AUTHORED_YIELD_REASONS).to.deep.equal(['payload_store_unavailable']);
  });

  it('CPD0-22 a child grant may name exactly the generated workspace mounts', () => {
    const spawn = (mounts: string[]) =>
      spawnChildRunSchema.safeParse({
        params: { runId: 'run-1' },
        body: { attemptId: 'a-1', fence: 1, invocationId: 'inv-1', goal: 'g', grants: { mounts } },
      });
    const all = spawn([...extEnums.WORKSPACE_MOUNTS]);
    expect(all.success, JSON.stringify(all.error?.issues.slice(0, 2))).to.equal(true);
    expect(spawn(['/etc']).success).to.equal(false);
  });

  it('CPD0-24 model access and run statuses are the generated constants', () => {
    expect(modelAccess.OFFERINGS).to.equal(coreEnums.OFFERINGS);
    expect(modelAccess.ACCOUNT_KINDS).to.equal(coreEnums.ACCOUNT_KINDS);
    expect(modelAccess.ROUTE_KINDS).to.equal(coreEnums.ROUTE_KINDS);
    expect(RUN_STATUSES).to.equal(extEnums.EXECUTION_STATUSES);
  });
});
