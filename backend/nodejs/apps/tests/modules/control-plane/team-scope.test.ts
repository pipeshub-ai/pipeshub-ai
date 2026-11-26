/**
 * CPD-9 TM-1..4 (team scope): the team layer source on CP-SKEL's PLS contract
 * and on `contracts/control-plane/vectors/team-scope.json` (precedence: the
 * agent's owner team, never the actor's teams), and the visibility and team
 * grant vectors on the reference effective-set resolver.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import { TeamPolicyLayerSource } from '../../../src/modules/control-plane/application/team-layer-source';
import type { EffectiveSet, SubjectRef } from '../../../src/modules/control-plane/domain/objects';
import { aGrant, anEntry, anInstallation, digest, instId } from './support/cp-objects';
import { InMemoryEffectiveSetResolver } from './support/in-memory-resolution';
import { describePolicyLayerSourceContract } from './support/policy-layer-source-contract';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';

interface TeamScopeVectors {
  precedence: Array<{
    id: string;
    behaviour: string;
    agentKey: string | null;
    agentOwnerTeam: string | null;
    actorTeams: string[];
    teamLayers: string[];
    expect: string | null;
  }>;
  visibility: Array<{
    id: string;
    behaviour: string;
    actorTeams: string[];
    installations: Array<{ n: number; owner: string; visibility: 'org' | 'team' | 'owner' }>;
    grants: Record<string, number[]>;
    ready: number[];
    unavailable: Record<string, string>;
  }>;
}

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/team-scope.json'), 'utf8'),
) as TeamScopeVectors;

describePolicyLayerSourceContract('TeamPolicyLayerSource', () => new TeamPolicyLayerSource(), ['team']);

describe('CPD-9 team precedence (team-scope.json)', () => {
  for (const vector of VECTORS.precedence) {
    it(`${vector.id}: ${vector.behaviour}`, () => {
      const layers = Object.fromEntries(
        vector.teamLayers.map((team) => [`team:${team}`, aLayer('team', { catalog: { approval: { 'install:mcp': 'orgAdmin' } } }, team)]),
      );
      const snapshot = aSnapshot({
        agentKey: vector.agentKey,
        agentOwnerTeam: vector.agentOwnerTeam,
        actorTeams: vector.actorTeams,
        layers,
      });
      const layer = new TeamPolicyLayerSource().layer(aSubject({ agentKey: vector.agentKey }), snapshot);
      expect(layer?.scope.id ?? null).to.equal(vector.expect);
    });
  }
});

describe('CPD-9 team visibility and team grants (team-scope.json)', () => {
  for (const vector of VECTORS.visibility) {
    it(`${vector.id}: ${vector.behaviour}`, () => {
      const installations = vector.installations.map((inst) =>
        anInstallation({
          id: instId(inst.n),
          owner: inst.owner as never,
          visibility: inst.visibility,
          credentialMode: 'none',
          approved: { descriptorSetDigest: digest('1'), tools: { a: digest('a') }, approvalRef: null, at: 1, by: 'user:u1' },
        }),
      );
      const grants = Object.entries(vector.grants).map(([subject, ns]) =>
        aGrant({ subject: subject as SubjectRef, entries: ns.map((n) => anEntry(instId(n))) }),
      );
      const snapshot = aSnapshot({ agentKey: null, actorTeams: vector.actorTeams, installations, grants });
      const result = new InMemoryEffectiveSetResolver().resolve(
        aSubject({ agentKey: null }),
        { schemaVersion: 1, dimensions: {} },
        snapshot,
      ) as EffectiveSet;
      const ready = result.items.filter((item) => item.state === 'ready').map((item) => item.installation).sort();
      expect(ready).to.deep.equal(vector.ready.map((n) => instId(n)).sort());
      const unavailable = Object.fromEntries(
        result.items.filter((item) => item.state !== 'ready').map((item) => [item.installation, item.reason]),
      );
      expect(unavailable).to.deep.equal(
        Object.fromEntries(Object.entries(vector.unavailable).map(([n, reason]) => [instId(Number(n)), reason])),
      );
    });
  }
});
