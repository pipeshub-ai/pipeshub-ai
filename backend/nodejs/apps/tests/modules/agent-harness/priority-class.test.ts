/**
 * G-45 / GA-24: the run-kind to priority-class table is the shared vector's (the worker's rate-budget mapping is
 * held to the same file), the claim floor is the deployment's to set, and a worker learns it may preempt from the
 * claim's Node capabilities.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  afterApproval,
  ClaimFloor,
  DEFAULT_CLAIM_FLOOR_EVERY,
  PRIORITY_CLASSES,
  priorityClassForRunKind,
  priorityClassOf,
  priorityRank,
  type PriorityClass,
} from '../../../src/modules/agent-harness/domain/priority-class';
import { CLAIM_FLOOR_EVERY_ENV, claimFloorEvery } from '../../../src/modules/agent-harness/config';
import {
  NODE_CAPABILITIES,
  PRIORITY_PREEMPTION_FEATURE,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { CONTRACTS_DIR } from '../../support/harness/contracts';

interface PriorityVector {
  order: PriorityClass[];
  runKinds: Record<string, PriorityClass>;
  unknownRunKind: PriorityClass;
  afterApproval: Record<PriorityClass, PriorityClass>;
}

const VECTOR = JSON.parse(
  readFileSync(join(CONTRACTS_DIR, 'vectors', 'priority-classes.json'), 'utf8'),
) as PriorityVector;

describe('G-45: priority classes', () => {
  it('GA-24: the classes are served in the vector’s order', () => {
    expect([...PRIORITY_CLASSES]).to.deep.equal(VECTOR.order);
    expect(VECTOR.order.map(priorityRank)).to.deep.equal([0, 1, 2, 3]);
  });

  it('GA-24: each admitted run kind gets the vector’s class; an unknown kind is interactive', () => {
    for (const [runKind, expected] of Object.entries(VECTOR.runKinds)) {
      expect(priorityClassForRunKind(runKind), runKind).to.equal(expected);
    }
    expect(priorityClassForRunKind('a_kind_from_a_later_release')).to.equal(VECTOR.unknownRunKind);
  });

  it('GA-24: a person’s answer raises a lower class to approval_resume and never lowers one', () => {
    for (const [from, to] of Object.entries(VECTOR.afterApproval)) {
      expect(afterApproval(from as PriorityClass), from).to.equal(to);
    }
  });

  it('a run recorded before classes reads as interactive', () => {
    expect(priorityClassOf({})).to.equal('interactive');
  });

  it('GA-24 floor: every n-th claim is a floor claim; 0 is strict priority', () => {
    const floor = new ClaimFloor(3);
    expect([1, 2, 3, 4, 5, 6].map(() => floor.next())).to.deep.equal([false, false, true, false, false, true]);
    const strict = new ClaimFloor(0);
    expect([1, 2, 3].map(() => strict.next())).to.deep.equal([false, false, false]);
  });

  it('AGENT_HARNESS_CLAIM_FLOOR_EVERY: a whole number sets the floor; anything else keeps the default', () => {
    expect(claimFloorEvery({})).to.equal(DEFAULT_CLAIM_FLOOR_EVERY);
    expect(claimFloorEvery({ [CLAIM_FLOOR_EVERY_ENV]: '10' })).to.equal(10);
    expect(claimFloorEvery({ [CLAIM_FLOOR_EVERY_ENV]: '0' })).to.equal(0);
    expect(claimFloorEvery({ [CLAIM_FLOOR_EVERY_ENV]: ' ' })).to.equal(DEFAULT_CLAIM_FLOOR_EVERY);
    expect(claimFloorEvery({ [CLAIM_FLOOR_EVERY_ENV]: '-1' })).to.equal(DEFAULT_CLAIM_FLOOR_EVERY);
    expect(claimFloorEvery({ [CLAIM_FLOOR_EVERY_ENV]: 'five' })).to.equal(DEFAULT_CLAIM_FLOOR_EVERY);
  });

  it('GA-24: this Node names priority_preemption, so a worker may ask above a class and yield preempted', () => {
    expect(NODE_CAPABILITIES.features).to.include(PRIORITY_PREEMPTION_FEATURE);
  });
});
