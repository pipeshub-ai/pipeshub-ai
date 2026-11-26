/**
 * ESR: what every `EffectiveSetResolver` does (03-interfaces §4.3, 02-lld §3.4
 * invariant 6 and §5.2); CPD-7 runs it on the domain resolver and adds the
 * shared vectors (`contracts/control-plane/vectors/effective-set.json`).
 */
import { expect } from 'chai';
import fc from 'fast-check';
import type { Problem } from '../../../../src/modules/control-plane/contracts/generated';
import {
  effectiveSetSchema,
  type Binding,
  type EffectiveSet,
  type Installation,
} from '../../../../src/modules/control-plane/domain/objects';
import { kindAllowed, TOOLS_GRANT, type EffectiveRunConfig } from '../../../../src/modules/control-plane/domain/policy-alias';
import { isProblem } from '../../../../src/modules/control-plane/domain/problem';
import type { EffectiveSetResolver } from '../../../../src/modules/control-plane/ports/effective-set-resolver';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, digest, instId } from './cp-objects';
import { aSnapshot, aSubject } from './resolution-fixtures';

const TOOLS = ['a', 'b', 'c'];
const NO_CONFIG: EffectiveRunConfig = { schemaVersion: 1, dimensions: {} };
type Value = EffectiveRunConfig['dimensions'][string]['value'];
const configOf = (dimensions: Record<string, unknown>): EffectiveRunConfig => ({
  schemaVersion: 1,
  dimensions: Object.fromEntries(
    Object.entries(dimensions).map(([path, raw]) => {
      const value = raw as Value;
      return [path, { value, setBy: 'org' as const, ceilings: [{ layer: 'org' as const, value }] }];
    }),
  ),
});

const setOf = (result: EffectiveSet | Problem): EffectiveSet => {
  expect(isProblem(result), JSON.stringify(result)).to.equal(false);
  return result as EffectiveSet;
};

const approvedTools = (names: readonly string[]) => Object.fromEntries(names.map((name) => [name, digest(name)]));

const installationArb = (n: number) =>
  fc.record({
    status: fc.constantFrom<Installation['status']>('enabled', 'enabled', 'disabled', 'upgrade_pending', 'pending_approval', 'removed'),
    visibility: fc.constantFrom<Installation['visibility']>('org', 'team', 'owner'),
    owner: fc.constantFrom<Installation['owner']>('org', 'team:t1', 'team:t2', 'user:u3', 'user:u4'),
    kind: fc.constantFrom('mcp', 'toolset'),
    approved: fc.option(fc.subarray(TOOLS), { nil: null }),
    credentialMode: fc.constantFrom<Installation['credentialMode']>('none', 'per_user', 'org'),
  }).map((f) =>
    anInstallation({
      id: instId(n),
      status: f.status,
      visibility: f.visibility,
      owner: f.owner,
      kind: f.kind,
      item: f.kind === 'mcp' ? 'mcp:pipeshub/server@1' : 'toolset:pipeshub/jira@1',
      approved: f.approved === null ? null : { descriptorSetDigest: digest('1'), tools: approvedTools(f.approved), approvalRef: null, at: 1, by: 'user:u1' },
      credentialMode: f.credentialMode,
    }),
  );

const worldArb = fc
  .record({
    installations: fc.tuple(installationArb(1), installationArb(2), installationArb(3), installationArb(4)),
    granted: fc.subarray([1, 2, 3, 4, 5]),
    entryTools: fc.array(fc.option(fc.subarray(TOOLS), { nil: null }), { minLength: 5, maxLength: 5 }),
    actorTeams: fc.subarray(['t1', 't2']),
    bindingStatus: fc.array(fc.option(fc.constantFrom<Binding['status']>('active', 'expiring', 'revoked', 'expired'), { nil: null }), { minLength: 4, maxLength: 4 }),
    mcpAllowed: fc.option(fc.boolean(), { nil: undefined }),
    ceiling: fc.option(fc.dictionary(fc.constantFrom(instId(1), instId(2), instId(3)), fc.option(fc.subarray(TOOLS), { nil: null })), { nil: undefined }),
  })
  .map((w) => {
    const bindings = w.bindingStatus.flatMap((status, i) =>
      status === null
        ? []
        : [aBinding({ id: bindingId(i + 1), installation: instId(i + 1), owner: w.installations[i]!.credentialMode === 'org' ? 'org' : 'user:u3', status })],
    );
    const dimensions: Record<string, unknown> = {};
    if (w.mcpAllowed !== undefined) dimensions[kindAllowed('mcp')] = w.mcpAllowed;
    if (w.ceiling !== undefined) dimensions[TOOLS_GRANT] = w.ceiling;
    const grant = aGrant({ entries: w.granted.map((n) => anEntry(instId(n), w.entryTools[n - 1] ?? null)) });
    return {
      snapshot: aSnapshot({ installations: w.installations, grants: [grant], bindings, actorTeams: w.actorTeams }),
      config: configOf(dimensions),
      grant,
    };
  });

export function describeEffectiveSetResolverContract(name: string, make: () => EffectiveSetResolver): void {
  describe(`EffectiveSetResolver contract (ESR): ${name}`, () => {
    it('ESR-1: every ready tool is granted, visible, enabled, approved and inside tools.grant', () => {
      fc.assert(
        fc.property(worldArb, ({ snapshot, config, grant }) => {
          const result = make().resolve(aSubject(), config, snapshot);
          if (isProblem(result)) return;
          for (const item of result.items.filter((i) => i.state === 'ready')) {
            const inst = snapshot.installations.get(item.installation)!;
            const entry = grant.entries.find((e) => e.installation === inst.id)!;
            expect(entry, 'granted').to.not.equal(undefined);
            expect(['enabled', 'upgrade_pending']).to.include(inst.status);
            expect(config.dimensions[kindAllowed(inst.kind)]?.value).to.not.equal(false);
            const visible =
              inst.visibility === 'org' ||
              inst.owner === 'user:u3' ||
              (inst.visibility === 'team' && snapshot.actorTeams.some((t) => inst.owner === `team:${t}`));
            expect(visible, 'visible').to.equal(true);
            for (const tool of item.tools) {
              const name = tool.toolId.slice(tool.toolId.lastIndexOf('/') + 1);
              expect(inst.approved?.tools[name], 'approved').to.equal(tool.descriptorDigest);
              if (entry.tools !== null) expect(entry.tools).to.include(name);
              const ceiling = config.dimensions[TOOLS_GRANT]?.value as Record<string, string[] | null> | undefined;
              if (ceiling !== undefined) expect(ceiling[inst.id] === null || (ceiling[inst.id] ?? []).includes(name)).to.equal(true);
            }
          }
        }),
        { numRuns: 300, seed: 20261010 },
      );
    });

    it('ESR-2: the same inputs give the same digest, whatever the order of grant entries and installations', () => {
      fc.assert(
        fc.property(worldArb, ({ snapshot, config, grant }) => {
          const first = make().resolve(aSubject(), config, snapshot);
          const reordered = aSnapshot({
            installations: [...snapshot.installations.values()].reverse(),
            grants: [{ ...grant, entries: [...grant.entries].reverse() }],
            bindings: [...snapshot.bindings].reverse(),
            actorTeams: [...snapshot.actorTeams],
          });
          const second = make().resolve(aSubject(), config, reordered);
          expect(second).to.deep.equal(first);
        }),
        { numRuns: 200, seed: 20261011 },
      );
    });

    it('ESR-3: an unavailable item carries the first failing check, in the documented order', () => {
      const cases: Array<[string, Partial<Installation> | null, EffectiveRunConfig, readonly Binding[], string]> = [
        ['missing', null, NO_CONFIG, [], 'not_installed'],
        ['disabled and not visible', { status: 'disabled', visibility: 'owner', owner: 'user:u4' }, NO_CONFIG, [], 'disabled'],
        ['kind not allowed and not visible', { visibility: 'owner', owner: 'user:u4' }, configOf({ [kindAllowed('mcp')]: false }), [], 'policy_ceiling'],
        ['not visible and no credential', { visibility: 'owner', owner: 'user:u4', credentialMode: 'per_user' }, NO_CONFIG, [], 'not_visible'],
        ['no credential', { credentialMode: 'per_user' }, NO_CONFIG, [], 'credential_missing'],
        ['revoked credential', { credentialMode: 'per_user' }, NO_CONFIG, [aBinding({ status: 'revoked' })], 'credential_revoked'],
        ['another user\'s credential only', { credentialMode: 'per_user' }, NO_CONFIG, [aBinding({ owner: 'user:u4' })], 'credential_missing'],
      ];
      for (const [label, inst, config, bindings, reason] of cases) {
        const snapshot = aSnapshot({
          installations: inst === null ? [] : [anInstallation(inst)],
          grants: [aGrant()],
          bindings,
        });
        const set = setOf(make().resolve(aSubject(), config, snapshot));
        expect(set.items.map((i) => [i.state, i.reason]), label).to.deep.equal([['unavailable', reason]]);
      }
    });

    it('ESR-4: a required item that is unavailable refuses the run, naming it', () => {
      const snapshot = aSnapshot({
        installations: [anInstallation({ status: 'disabled' })],
        grants: [aGrant({ entries: [anEntry(instId(1), null, true)] })],
      });
      const result = make().resolve(aSubject(), NO_CONFIG, snapshot);
      expect(isProblem(result)).to.equal(true);
      expect(result).to.deep.include({ code: 'CP_DISABLED', subject: { installation: instId(1) } });
    });

    it('ESR-5: more tools than the cap is CP_EFFECTIVE_SET_TOO_LARGE', () => {
      const many = Object.fromEntries(Array.from({ length: 2001 }, (_, n) => [`t${n}`, digest('7')]));
      const snapshot = aSnapshot({
        installations: [anInstallation({ approved: { descriptorSetDigest: digest('1'), tools: many, approvalRef: null, at: 1, by: 'user:u1' } })],
        grants: [aGrant()],
      });
      expect(make().resolve(aSubject(), NO_CONFIG, snapshot)).to.deep.include({ code: 'CP_EFFECTIVE_SET_TOO_LARGE' });
    });

    it('ESR-6: the effective set validates against the generated schema', () => {
      fc.assert(
        fc.property(worldArb, ({ snapshot, config }) => {
          const result = make().resolve(aSubject(), config, snapshot);
          if (!isProblem(result)) expect(effectiveSetSchema.safeParse(result).success).to.equal(true);
        }),
        { numRuns: 100, seed: 20261012 },
      );
    });
  });
}
