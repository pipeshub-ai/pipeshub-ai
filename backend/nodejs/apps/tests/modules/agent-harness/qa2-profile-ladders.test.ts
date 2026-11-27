/**
 * QA2-7, QA2-8: the engine-profile resolver held to the stricter-only ladders
 * and the requested-effort table Python shares
 * (`contracts/harness/vectors/stricter-only.json`): NO-7's property, as vectors.
 */
import { expect } from 'chai';
import {
  REASONING_EFFORTS,
  REQUESTED_EFFORTS,
} from '../../../src/modules/agent-harness/contracts/generated/core/enums';
import {
  requestNarrowing,
  resolveEngineProfile,
  type ProfileLayers,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import { POLICY_REGISTRY } from '../../../src/modules/agent-harness/domain/policy';
import { put, STRICTER_ONLY, type Ladder } from './support/stricter-only-vectors';

const SECTION = 'engineProfile.';
const PROFILE_LAYERS = ['org', 'agent', 'request'] as const;
const LADDERS = STRICTER_ONLY.ladders.filter((ladder) => ladder.path.startsWith(SECTION) && ladder.hostOnly !== true);
const RELEASES = [STRICTER_ONLY.release];

const narrowing = (ladder: Ladder, rung: 'narrow' | 'widen'): Record<string, unknown> =>
  put({}, POLICY_REGISTRY.get(ladder.path).layerPath.slice(SECTION.length), ladder[rung]);

describe('QA-2 stricter only: the engine-profile resolver on the shared ladders', () => {
  for (const ladder of LADDERS) {
    const path = ladder.path.slice(SECTION.length);
    for (const layer of PROFILE_LAYERS) {
      it(`QA2-7 ${path}: a narrowing in the ${layer} layer is kept and changes nothing else`, () => {
        const decision = resolveEngineProfile({ [layer]: narrowing(ladder, 'narrow') }, RELEASES);
        const kept = ladder.kept === undefined ? ladder.narrow : ladder.kept;
        expect(decision.refusal).to.equal(null);
        expect(decision.profile).to.deep.equal(put(structuredClone(STRICTER_ONLY.release), path, kept));
      });
    }
    for (const [upper, lower] of [
      ['org', 'agent'],
      ['org', 'request'],
      ['agent', 'request'],
    ] as const) {
      it(`QA2-7 ${path}: a widening in the ${lower} layer under the ${upper} layer is refused naming it`, () => {
        const layers: ProfileLayers = { [upper]: narrowing(ladder, 'narrow'), [lower]: narrowing(ladder, 'widen') };
        const decision = resolveEngineProfile(layers, RELEASES);
        expect(decision.profile).to.equal(null);
        expect(decision.refusal).to.include({
          code: 'ENGINE_PROFILE_WIDENING',
          dimension: (ladder.at ?? ladder.path).slice(SECTION.length),
          layer: lower,
        });
      });
    }
  }

  it('QA2-8 every requested effort narrows to a profile effort, never above the ask', () => {
    const order = POLICY_REGISTRY.get(`${SECTION}reasoningEffort`).order!;
    const bottom = REASONING_EFFORTS[0];
    expect(REASONING_EFFORTS.every((effort) => order.leq(bottom, effort) === null)).to.equal(true);
    const rows = new Map(STRICTER_ONLY.efforts.map((row) => [row.requested, row.narrowing]));
    expect([...rows.keys()]).to.have.members([null, ...REQUESTED_EFFORTS]);
    expect(requestNarrowing(undefined)).to.equal(undefined);
    expect(rows.get(null)).to.equal(null);
    for (const requested of REQUESTED_EFFORTS) {
      const asked = (REASONING_EFFORTS as readonly string[]).includes(requested) ? requested : bottom;
      expect(rows.get(requested), requested).to.equal(asked);
      expect(requestNarrowing(requested), requested).to.deep.equal({ reasoningEffort: asked });
    }
    const ranks = REQUESTED_EFFORTS.map((requested) =>
      (REASONING_EFFORTS as readonly string[]).indexOf(rows.get(requested)!),
    );
    expect(ranks).to.deep.equal([...ranks].sort((a, b) => a - b));
  });

  for (const row of STRICTER_ONLY.efforts) {
    for (const at of row.byCeiling) {
      it(`QA2-8 effort ${String(row.requested)} under ceiling ${String(at.ceiling)}: pinned at or below it, or refused`, () => {
        const decision = resolveEngineProfile(
          {
            ...(at.ceiling !== null ? { org: { reasoningEffort: at.ceiling } } : {}),
            request: requestNarrowing(row.requested ?? undefined),
          },
          [{ ...STRICTER_ONLY.release, reasoningEffort: null }],
        );
        if (at.refused === true) {
          expect(decision.refusal).to.include({
            code: 'ENGINE_PROFILE_WIDENING',
            dimension: 'reasoningEffort',
            layer: 'request',
          });
          return;
        }
        const efforts = REASONING_EFFORTS as readonly (string | null)[];
        expect(decision.profile?.reasoningEffort).to.equal(at.pinned);
        if (at.ceiling !== null) {
          expect(efforts.indexOf(decision.profile!.reasoningEffort)).to.be.within(0, efforts.indexOf(at.ceiling));
        }
      });
    }
  }
});
