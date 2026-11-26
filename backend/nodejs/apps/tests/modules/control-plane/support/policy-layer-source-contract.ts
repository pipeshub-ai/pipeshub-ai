/**
 * PLS: what every `PolicyLayerSource` does (03-interfaces §4.2); CPD-5 runs it
 * on the org, agent and user sources, CPD-9 on the team source.
 */
import { expect } from 'chai';
import type { ScopeKind } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { PolicyLayerSource } from '../../../../src/modules/control-plane/ports/policy-layer-source';
import { ORG_A } from './cp-objects';
import { aLayer, aSnapshot, aSubject } from './resolution-fixtures';

/** The scope each stored-layer kind reads for `aSubject()` (org-a, agent a7, actor u3, owner team t9). */
const OWN_SCOPE: Partial<Record<ScopeKind, string>> = {
  org: `org:${ORG_A}`,
  team: 'team:t9',
  agent: 'agent:a7',
  user: 'user:u3',
};

/** The same kinds for another subject (org other-org, agent a8, actor u4, owner team t1). */
const OTHER_SCOPE: Partial<Record<ScopeKind, string>> = {
  org: 'org:other-org',
  team: 'team:t1',
  agent: 'agent:a8',
  user: 'user:u4',
};

export function describePolicyLayerSourceContract(
  name: string,
  make: (kind: ScopeKind) => PolicyLayerSource,
  kinds: readonly ScopeKind[],
): void {
  describe(`PolicyLayerSource contract (PLS): ${name}`, () => {
    const layers = Object.fromEntries(
      Object.entries(OWN_SCOPE).map(([kind, scope]) => [scope, aLayer(kind as ScopeKind, { budgets: { maxCostUsd: 3 } })]),
    );
    const others: Record<string, ReturnType<typeof aLayer>> = Object.fromEntries(
      Object.entries(OTHER_SCOPE).map(([kind, scope]) => [scope, aLayer(kind as ScopeKind, { budgets: { maxCostUsd: 1 } })]),
    );
    const snapshot = aSnapshot({ layers: { ...layers, ...others }, agentOwnerTeam: 't9', actorTeams: ['t1'] });

    for (const kind of kinds) {
      it(`PLS-1: the ${kind} source reads the subject's own ${kind} layer`, () => {
        const source = make(kind);
        expect(source.kind).to.equal(kind);
        const layer = source.layer(aSubject(), snapshot);
        expect(layer?.scope.kind).to.equal(kind);
        expect(layer).to.deep.equal(layers[OWN_SCOPE[kind]!]);
      });

      it(`PLS-1: the ${kind} source never reads another subject's ${kind} layer`, () => {
        const other = aSubject({ orgId: 'other-org', actorId: 'u4', agentKey: 'a8' });
        const theirs = aSnapshot({ layers: { ...layers, ...others }, agentOwnerTeam: 't1' });
        expect(make(kind).layer(other, theirs)).to.deep.equal(others[OTHER_SCOPE[kind]!]);
      });

      it(`PLS-2: the ${kind} source answers null when the subject has no such layer`, () => {
        const source = make(kind);
        expect(source.layer(aSubject({ actorId: 'u9' }), aSnapshot({ agentOwnerTeam: 't9' }))).to.equal(null);
        if (kind === 'agent') expect(source.layer(aSubject({ agentKey: null }), snapshot)).to.equal(null);
        if (kind === 'team') expect(source.layer(aSubject(), aSnapshot({ layers, agentOwnerTeam: null }))).to.equal(null);
      });

      it(`PLS-3: the ${kind} source reads only the snapshot, synchronously and repeatably`, () => {
        const source = make(kind);
        const first = source.layer(aSubject(), snapshot);
        expect(first).not.to.be.instanceOf(Promise);
        expect(source.layer(aSubject(), snapshot)).to.deep.equal(first);
        expect(snapshot.layers.size).to.equal(Object.keys(layers).length + Object.keys(others).length);
      });
    }
  });
}
