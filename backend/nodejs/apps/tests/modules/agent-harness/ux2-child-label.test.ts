/**
 * UX2 VC-2: a parent's label rides its child request and Node pins it as the
 * child's `x.pipeshub.inheritedLabel`; the child then needs a worker that reads
 * it (`inherited_labels`). A malformed label is refused at the route. A
 * grandchild gets its own parent's label, never the grandparent's pin.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  childFeatures,
  childGrants,
  childRunSpec,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { KNOWN_WORKER_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { spawnChildRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const table = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/child-grants.json'), 'utf8'),
) as { parentSpec: RunSpec };

const parentSpec: RunSpec = { ...table.parentSpec, executor: { kind: 'loop' } };
const pinnedOf = (spec: RunSpec): PinnedRunSpec => ({
  spec,
  specDigest: `sha256:${'0'.repeat(64)}`,
  capabilities: {} as PinnedRunSpec['capabilities'],
  capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
});
const LABEL = { complete: true, sources: [{ kind: 'indexed' as const, recordId: 'rec-1' }] };
const pipeshub = (spec: RunSpec) => (spec.extensions as Record<string, Record<string, unknown>>)['x.pipeshub'];

describe("a child inherits its parent's label (UX2 VC-2)", () => {
  const grants = childGrants(parentSpec, {}, 1).grants!;

  it('pins the label the parent sent and needs a worker that reads it', () => {
    const pinned = childRunSpec(pinnedOf(parentSpec), grants, undefined, undefined, undefined, undefined, LABEL);
    expect(pipeshub(pinned.spec)?.['inheritedLabel']).to.deep.equal(LABEL);
    expect(childFeatures(pinned)).to.include('inherited_labels');
    expect(KNOWN_WORKER_FEATURES).to.include('inherited_labels');
  });

  it('without one (an older worker) nothing is pinned and nothing more is needed', () => {
    const pinned = childRunSpec(pinnedOf(parentSpec), grants);
    expect(pipeshub(pinned.spec)).to.not.have.property('inheritedLabel');
    expect(childFeatures(pinned)).to.not.include('inherited_labels');
  });

  it("a grandchild gets its own parent's label, never the pin its parent inherited", () => {
    const child = childRunSpec(pinnedOf(parentSpec), grants, undefined, undefined, undefined, undefined, LABEL).spec;
    const grandchildGrants = childGrants(child, {}, 2).grants!;
    const own = { complete: false, sources: [] };
    const grandchild = childRunSpec(pinnedOf(child), grandchildGrants, undefined, undefined, undefined, undefined, own);
    expect(pipeshub(grandchild.spec)?.['inheritedLabel']).to.deep.equal(own);
    const unlabelled = childRunSpec(pinnedOf(child), grandchildGrants);
    expect(pipeshub(unlabelled.spec)).to.not.have.property('inheritedLabel');
  });

  it('the admission body takes a well-formed label only', () => {
    const base = { attemptId: 'a', fence: 1, invocationId: 'i', goal: 'g' };
    const parse = (inheritedLabel: unknown) => spawnChildRunSchema.shape.body.safeParse({ ...base, inheritedLabel }).success;
    expect(parse(LABEL)).to.equal(true);
    expect(parse({ complete: false, sources: [] })).to.equal(true);
    expect(parse({ sources: [] })).to.equal(false);
    expect(parse({ complete: true, sources: [{ kind: 'web', recordId: 'r' }] })).to.equal(false);
    expect(spawnChildRunSchema.shape.body.safeParse(base).success).to.equal(true);
  });
});
