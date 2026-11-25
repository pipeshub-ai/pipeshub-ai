/**
 * QAFINAL-FU-PW107: a child's pin keeps where a value its `parent` layer
 * carries came from. PW-107's coordinator held the org's `writes: ask`
 * (`setBy: org`); its deep child one `parent` ceiling, so its approval card
 * named the rule the deployment's. Python's `with_parent_ceilings`
 * (`tests/unit/harness/test_qafinal_pw107_child_rule.py`) holds the same table;
 * the shared pin vectors hold both to the same digests.
 */
import { expect } from 'chai';
import { PARENT_DIMENSIONS } from '../../../src/modules/agent-harness/domain/policy';
import type { EffectiveRunConfig } from '../../../src/modules/agent-harness/contracts/generated/core/policy/effective-run-config';
import { inheritedDimensions } from '../../../src/modules/control-plane/application/layer-sources';

type Entry = EffectiveRunConfig['dimensions'][string];

const WRITE_ASK = { classes: { execute: 'ask', read: 'allow', workspace: 'allow', write: 'ask' }, tools: {} };
const PARENT: Entry = { value: WRITE_ASK, setBy: 'org', ceilings: [{ layer: 'org', value: { writes: 'ask' } }] };
const CHILD: Entry = { value: WRITE_ASK, setBy: 'parent', ceilings: [{ layer: 'parent', value: WRITE_ASK }] };

const childOf = (parent: Entry, child: Entry, path = 'approvals'): Entry =>
  inheritedDimensions(
    { schemaVersion: 1, dimensions: { [path]: child } },
    { schemaVersion: 1, dimensions: { [path]: parent } },
    PARENT_DIMENSIONS,
  ).dimensions[path] as Entry;

const layers = (entry: Entry): string[] => entry.ceilings.map((c) => c.layer);

describe('QAFINAL-FU-PW107: a child pin keeps its parent value’s provenance', () => {
  it('PW107-N1: the org’s ceiling goes before the parent’s; the value and setBy stay the child’s', () => {
    const child = childOf(PARENT, CHILD);
    expect(child.ceilings).to.deep.equal([PARENT.ceilings[0], CHILD.ceilings[0]]);
    expect(child.setBy).to.equal('parent');
    expect(child.value).to.deep.equal(WRITE_ASK);
  });

  it('PW107-N2: a grandchild holds the root’s provenance once', () => {
    expect(layers(childOf(childOf(PARENT, CHILD), structuredClone(CHILD)))).to.deep.equal(['org', 'parent']);
  });

  it('PW107-N3: only the parent’s own layers go before the parent ceiling; an entry the parent layer did not set is as it was', () => {
    const released: Entry = { value: 'ask', setBy: 'release', ceilings: [{ layer: 'release', value: 'ask' }] };
    const fromParent: Entry = {
      value: 'ask',
      setBy: 'parent',
      ceilings: [{ layer: 'release', value: 'ask' }, { layer: 'parent', value: 'ask' }],
    };
    const path = 'engineProfile.approvals.write';
    expect(layers(childOf(released, fromParent, path))).to.deep.equal(['release', 'parent']);
    const named: Entry = { ...released, setBy: 'org', ceilings: [...released.ceilings, { layer: 'org', value: 'ask' }] };
    expect(layers(childOf(named, fromParent, path))).to.deep.equal(['release', 'org', 'parent']);
    const ownRelease: Entry = { value: 'ask', setBy: 'release', ceilings: [{ layer: 'release', value: 'ask' }] };
    expect(childOf(named, ownRelease, path)).to.deep.equal(ownRelease);
  });

  it('PW107-N4: a parent with more ceilings than an entry may hold leaves the child’s as it was', () => {
    const crowded: Entry = { value: 'ask', setBy: 'agent', ceilings: Array.from({ length: 10 }, () => ({ layer: 'org' as const, value: 'ask' })) };
    const child: Entry = { value: 'ask', setBy: 'parent', ceilings: [{ layer: 'parent', value: 'ask' }] };
    expect(childOf(crowded, child, 'engineProfile.approvals.write')).to.deep.equal(child);
  });
});
