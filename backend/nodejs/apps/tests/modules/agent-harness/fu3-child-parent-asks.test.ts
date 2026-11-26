/**
 * ANSWER-CONTRACT-FU-3 (ADR-RUN-03 D2): a job asks the person only when its parent can. Node pins it on the child at
 * admission (`x.pipeshub.parentAsks`): true for a job of a person's run, and of a job of one; false under a
 * schedule's or a webhook's firing (`runKind: scheduled`) and a person's `/run`, where nobody waits on a question.
 * The worker offers the job `ask_user` only when it is true; the field needs a worker that reads it (`child_asks`).
 * The service side (pinned while a live worker reads it, withheld otherwise) is in `support/child-run-contract.ts`.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  childFeatures,
  childGrants,
  childRunSpec,
  parentCanAsk,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { KNOWN_WORKER_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { without } from './support/without';

const table = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/child-grants.json'), 'utf8'),
) as { parentSpec: RunSpec };

const rootOf = (runKind: RunSpec['runKind']): RunSpec => ({ ...table.parentSpec, runKind, executor: { kind: 'loop' } });
const pinnedOf = (spec: RunSpec): PinnedRunSpec => ({
  spec,
  specDigest: `sha256:${'0'.repeat(64)}`,
  capabilities: {} as PinnedRunSpec['capabilities'],
  capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
});
const pipeshub = (spec: RunSpec) => (spec.extensions as Record<string, Record<string, unknown>>)['x.pipeshub'];
const childOf = (parent: RunSpec, depth: number, parentAsks?: boolean) =>
  childRunSpec(
    pinnedOf(parent), childGrants(parent, {}, depth).grants!, undefined, undefined, undefined, undefined, undefined,
    undefined, undefined, undefined, undefined, undefined, parentAsks,
  );

describe('a job asks the person only when its parent can (ANSWER-CONTRACT-FU-3)', () => {
  it("a person's run can ask; a schedule's or a webhook's firing and a person's /run cannot", () => {
    expect(parentCanAsk(rootOf('interactive'))).to.equal(true);
    for (const kind of ['scheduled', 'command', 'workflow_code', 'authoring'] as const) {
      expect(parentCanAsk(rootOf(kind)), kind).to.equal(false);
    }
  });

  it('the child pins what its parent can do and needs a worker that reads it', () => {
    for (const [kind, asks] of [['interactive', true], ['scheduled', false]] as const) {
      const parent = rootOf(kind);
      const pinned = childOf(parent, 1, parentCanAsk(parent));
      expect(pinned.spec.runKind).to.equal('child');
      expect(pipeshub(pinned.spec)?.['parentAsks'], kind).to.equal(asks);
      expect(childFeatures(pinned)).to.include('child_asks');
    }
    expect(KNOWN_WORKER_FEATURES).to.include('child_asks');
  });

  it("a job's job asks only if its own parent could, never on its parent's pin alone", () => {
    const asking = childOf(rootOf('interactive'), 1, true).spec;
    const scheduled = childOf(rootOf('scheduled'), 1, false).spec;
    expect(parentCanAsk(asking)).to.equal(true);
    expect(parentCanAsk(scheduled)).to.equal(false);
    expect(pipeshub(childOf(asking, 2, parentCanAsk(asking)).spec)?.['parentAsks']).to.equal(true);
    expect(pipeshub(childOf(scheduled, 2, parentCanAsk(scheduled)).spec)?.['parentAsks']).to.equal(false);
    // Not pinned (no live worker reads it): the grandchild carries nothing, whatever its parent was pinned with.
    expect(pipeshub(childOf(asking, 2).spec)).to.not.have.property('parentAsks');
    // A child pinned without it (before FU-3) cannot ask, so neither can its own job.
    const unpinned = without(pipeshub(asking)!, 'parentAsks');
    expect(parentCanAsk({ ...asking, extensions: { ...asking.extensions, 'x.pipeshub': unpinned } })).to.equal(false);
  });

  it('not pinned: no field and no new requirement', () => {
    const pinned = childOf(rootOf('interactive'), 1);
    expect(pipeshub(pinned.spec)).to.not.have.property('parentAsks');
    expect(childFeatures(pinned)).to.not.include('child_asks');
  });
});
