/**
 * QA-1 NTF-10 (IX-NTF-PIN-4): `pin ⊓ live ⊑ pin` over Node's reading of the approval layers.
 *
 * The live layer is one more layer folded onto a run's pinned ones (02-lld §3.1). Whatever it holds, the
 * fold decides every effect class and tool at least as strictly as the pin, and its flow rules cover every
 * rule the pin had (as strict, from as low a threshold). Python holds the same property
 * (`tests/unit/harness/pinning/test_qa1_narrow_only_property.py`).
 */
import { expect } from 'chai';
import { foldedApprovals } from './support/approvals-fold';
import fc from 'fast-check';
import { propertyRuns } from '../../support/harness/property-profile';
import {
  EFFECT_CLASSES,
  type ApprovalLevel,
  type EffectiveApprovals,
  type FlowRule,
  type NormalizedRules,
} from '../../../src/modules/agent-harness/domain/approvals';

const RANK: Readonly<Record<ApprovalLevel, number>> = { allow: 0, ask: 1, deny: 2 };
const FLOW_RANK: Readonly<Record<FlowRule['level'], number>> = { ask: 1, deny: 2 };
const THRESHOLD_RANK = { public: 0, private: 1 } as const;
const TOOLS = ['jira@inst-42/create_issue', 'jira@inst-42/search_issues', 'web@default/web_search'];

const level = fc.constantFrom<ApprovalLevel>('allow', 'ask', 'deny');
const layer: fc.Arbitrary<NormalizedRules> = fc.record(
  {
    classes: fc.record(
      { read: level, workspace: level, write: level, execute: level },
      { requiredKeys: [] },
    ),
    tools: fc.dictionary(fc.constantFrom(...TOOLS), level),
    flow: fc.record({
      level: fc.constantFrom('off', 'ask', 'deny'),
      above: fc.constantFrom('public', 'private'),
    }),
  },
  { requiredKeys: ['classes', 'tools'] },
) as fc.Arbitrary<NormalizedRules>;

const decide = (approvals: EffectiveApprovals, kind: (typeof EFFECT_CLASSES)[number], tool: string) =>
  Math.max(RANK[approvals.classes[kind]], RANK[approvals.tools[tool] ?? 'allow']);

const covered = (rule: FlowRule, by: readonly FlowRule[]) =>
  by.some(
    (other) =>
      FLOW_RANK[other.level] >= FLOW_RANK[rule.level] &&
      THRESHOLD_RANK[other.above] <= THRESHOLD_RANK[rule.above],
  );

describe('QA-1 NTF-10: the live layer only narrows a pinned run', () => {
  it('folding any live layer onto the pin never loosens an effect class or a tool', () => {
    fc.assert(
      fc.property(fc.array(layer, { maxLength: 4 }), layer, (pin, live) => {
        const pinned = foldedApprovals(pin);
        const folded = foldedApprovals([...pin, live]);
        for (const kind of EFFECT_CLASSES) {
          for (const tool of TOOLS) {
            expect(decide(folded, kind, tool)).to.be.at.least(decide(pinned, kind, tool));
          }
        }
      }),
      propertyRuns(300),
    );
  });

  it("folding any live layer onto the pin keeps every flow rule the pin had", () => {
    fc.assert(
      fc.property(fc.array(layer, { maxLength: 4 }), layer, (pin, live) => {
        const pinned = foldedApprovals(pin).flow ?? [];
        const folded = foldedApprovals([...pin, live]).flow ?? [];
        for (const rule of pinned) expect(covered(rule, folded), JSON.stringify(rule)).to.equal(true);
      }),
      propertyRuns(300),
    );
  });
});
