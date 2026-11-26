/**
 * FLOWASK: the conversation grants a parent held ride its child request and Node pins them as the child's
 * `x.pipeshub.inheritedGrants`, because the child journals on its own stream, which holds none of them. The child
 * then needs a worker that reads them (`inherited_grants`). A malformed grant is refused at the route, and a
 * grandchild gets its own parent's grants, never the pin its parent inherited.
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
import { NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
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
const GRANTS = [{ toolId: 'web@duckduckgo/fetch_url', key: `sha256:${'a'.repeat(64)}` }];
const pipeshub = (spec: RunSpec) => (spec.extensions as Record<string, Record<string, unknown>>)['x.pipeshub'];
const withGrants = (parent: RunSpec, grants: ReturnType<typeof childGrants>['grants'], inherited?: typeof GRANTS) =>
  childRunSpec(
    pinnedOf(parent), grants!, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    inherited,
  );

describe("a child holds the conversation grants its parent held (FLOWASK)", () => {
  const grants = childGrants(parentSpec, {}, 1).grants!;

  it('pins the grants the parent sent and needs a worker that reads them', () => {
    const pinned = withGrants(parentSpec, grants, GRANTS);
    expect(pipeshub(pinned.spec)?.['inheritedGrants']).to.deep.equal(GRANTS);
    expect(childFeatures(pinned)).to.include('inherited_grants');
    expect(KNOWN_WORKER_FEATURES).to.include('inherited_grants');
    expect(NODE_CAPABILITIES.features).to.include('child_grants');
  });

  it('none sent, or none held, pins nothing and needs nothing more', () => {
    for (const pinned of [withGrants(parentSpec, grants), withGrants(parentSpec, grants, [])]) {
      expect(pipeshub(pinned.spec)).to.not.have.property('inheritedGrants');
      expect(childFeatures(pinned)).to.not.include('inherited_grants');
    }
  });

  it("a grandchild gets its own parent's grants, never the pin its parent inherited", () => {
    const child = withGrants(parentSpec, grants, GRANTS).spec;
    const grandchildGrants = childGrants(child, {}, 2).grants!;
    expect(pipeshub(withGrants(child, grandchildGrants).spec)).to.not.have.property('inheritedGrants');
    const own = [...GRANTS, { toolId: 'web@duckduckgo/web_search', key: `sha256:${'b'.repeat(64)}` }];
    expect(pipeshub(withGrants(child, grandchildGrants, own).spec)?.['inheritedGrants']).to.deep.equal(own);
  });

  it('the admission body takes well-formed grants only', () => {
    const base = { attemptId: 'a', fence: 1, invocationId: 'i', goal: 'g' };
    const parse = (inheritedGrants: unknown) =>
      spawnChildRunSchema.shape.body.safeParse({ ...base, inheritedGrants }).success;
    expect(parse(GRANTS)).to.equal(true);
    expect(parse([])).to.equal(true);
    expect(parse([{ toolId: 'web@duckduckgo/fetch_url' }])).to.equal(false);
    expect(parse([{ toolId: 'web@duckduckgo/fetch_url', key: 'pipeshub.com' }])).to.equal(false);
    expect(parse(Array.from({ length: 257 }, () => GRANTS[0]))).to.equal(false);
  });
});
