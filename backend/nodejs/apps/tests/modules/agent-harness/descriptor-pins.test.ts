/**
 * CPS PN-3, PN-4, PN-8, PN-9: a run's tool and MCP pins bind to what the
 * authority bound at admission (R2 F4): a toolset tool's descriptor digest, an
 * MCP server's approved manifest entries. Only a run admitted requiring
 * `descriptor_pins` pins them (version 2), so only a worker that verifies them
 * claims it; every other pin is the legacy identity digest, read as before.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { childFeatures } from '../../../src/modules/agent-harness/domain/child-runs';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  DESCRIPTOR_PINS_FEATURE,
  pinRunSpec,
  resolveRunSpec,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';

interface ManifestVector {
  name: string;
  approved: Record<string, string>;
  granted: string[];
  digest: string | null;
}

interface MoveVector {
  name: string;
  approved: Record<string, string>;
  reapproved: Record<string, string>;
  granted: string[];
  pinned: string;
  current: string | null;
}

const VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/descriptor-pins.json'), 'utf-8'),
) as { mcpManifest: ManifestVector[]; mcpManifestMoves: MoveVector[] };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const D = (n: number): string => `sha256:${String(n).repeat(64).slice(0, 64)}`;

const run = (features: string[] = [DESCRIPTOR_PINS_FEATURE]): HarnessRun =>
  ({
    orgId: 'o',
    runId: 'r',
    sessionId: 's',
    actorId: 'u',
    createdAt: 0,
    absoluteDeadlineAt: 60_000,
    harnessMajor: 2,
    requires: { foldVersion: 8, features },
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
  }) as unknown as HarnessRun;

const bound = (overrides: Partial<AgentRunDefinition> = {}): AgentRunDefinition =>
  agentDefinition({
    toolsets: [
      {
        name: 'jira',
        instanceId: 'inst-42',
        tools: [
          { name: 'search_issues', fullName: 'jira.search_issues', descriptorDigest: D(1) },
          { name: 'create_issue', fullName: 'jira.create_issue' },
        ],
      },
    ],
    mcpServers: [
      {
        instanceId: 'mcp-9',
        name: 'github',
        typeId: 'github',
        tools: [{ name: 'list_prs', fullName: 'mcp_github_list_prs', descriptorDigest: D(2) }],
      },
    ],
    ...overrides,
  });

const pin = (definition: AgentRunDefinition, target: HarnessRun = run()): RunSpec =>
  resolveRunSpec({ run: target, agentDefinition: definition, via: 'session' }, DURABLE);

const mcpOf = (spec: RunSpec) =>
  (spec.extensions?.['x.pipeshub'] as { mcp: Array<Record<string, unknown>> }).mcp;

describe('CPS pins bind to the descriptors and approvals admission bound', () => {
  it('PN-3: a bound toolset tool pins its descriptor digest as version 2; an unbound one its identity', () => {
    const tools = Object.fromEntries(pin(bound()).grants.tools.map((grant) => [grant.toolId, grant]));
    expect(tools['jira@inst-42/search_issues']).to.deep.equal({
      toolId: 'jira@inst-42/search_issues',
      descriptorDigest: D(1),
      digestVersion: 2,
    });
    expect(tools['jira@inst-42/create_issue']).to.deep.equal({
      toolId: 'jira@inst-42/create_issue',
      descriptorDigest: canonicalDigest({ toolId: 'jira@inst-42/create_issue', fullName: 'jira.create_issue' }),
    });
    expect(tools['mcp@mcp-9/list_prs']).to.include({ descriptorDigest: D(2), digestVersion: 2 });
  });

  it('PN-3: an MCP server pins the digest of the approved entries of the tools it grants', () => {
    expect(mcpOf(pin(bound()))).to.deep.equal([
      { instanceId: 'mcp-9', manifestDigest: canonicalDigest({ list_prs: D(2) }), digestVersion: 2 },
    ]);
  });

  for (const vector of VECTORS.mcpManifest) {
    it(`PN-4: the manifest digest equals the shared vector: ${vector.name}`, () => {
      const definition = bound({
        mcpServers: [
          {
            instanceId: 'mcp-9',
            name: 'github',
            typeId: 'github',
            tools: vector.granted.map((name) => ({
              name,
              fullName: `mcp_github_${name}`,
              ...(vector.approved[name] !== undefined ? { descriptorDigest: vector.approved[name] } : {}),
            })),
          },
        ],
      });
      const [server] = mcpOf(pin(definition));
      if (vector.digest === null) {
        expect(server).to.not.have.property('digestVersion');
      } else {
        expect(server).to.deep.equal({ instanceId: 'mcp-9', manifestDigest: vector.digest, digestVersion: 2 });
      }
    });
  }

  for (const vector of VECTORS.mcpManifestMoves) {
    it(`QA-3 MCP-26d: a run admitted after a re-approval pins what the worker then verifies: ${vector.name}`, () => {
      const pinnedOver = (approved: Record<string, string>) =>
        mcpOf(
          pin(
            bound({
              mcpServers: [
                {
                  instanceId: 'mcp-9',
                  name: 'github',
                  typeId: 'github',
                  tools: vector.granted.map((name) => ({
                    name,
                    fullName: `mcp_github_${name}`,
                    ...(approved[name] !== undefined ? { descriptorDigest: approved[name] } : {}),
                  })),
                },
              ],
            }),
          ),
        )[0]!;
      const before = pinnedOver(vector.approved);
      const after = pinnedOver(vector.reapproved);
      expect(before).to.include({ manifestDigest: vector.pinned, digestVersion: 2 });
      if (vector.current === null) {
        expect(after).to.not.have.property('digestVersion');
      } else {
        expect(after).to.include({ manifestDigest: vector.current, digestVersion: 2 });
      }
      const moved = vector.granted.some((name) => vector.approved[name] !== vector.reapproved[name]);
      expect(before.manifestDigest !== after.manifestDigest).to.equal(moved);
    });
  }

  it('PN-8: a run not admitted to pin them keeps every pin as before, whatever the definition carries', () => {
    const before = pin(bound(), run([]));
    expect(before.grants.tools.every((grant) => grant.digestVersion === undefined)).to.equal(true);
    expect(mcpOf(before)[0]).to.not.have.property('digestVersion');
    expect(pin(agentDefinition(), run([])).grants).to.deep.equal(pin(agentDefinition()).grants);
  });

  it('PN-8: a pinned spec without versions still reads and resolves', () => {
    const pinned = pinRunSpec({ run: run([]), agentDefinition: agentDefinition(), via: 'session' }, DURABLE);
    expect(pinned.spec.grants.tools.length).to.be.greaterThan(0);
  });

  it('PN-9: a run whose agent binds descriptors requires descriptor_pins; one that binds none does not', async () => {
    const admitted = async (definition: AgentRunDefinition) => {
      const outcome = await new RunAdmissionService({
        store: new InMemoryExecutionStore(),
        rollout: {
          decide: async () => ({ ...HARNESS_ENABLED, harnessMajor: 2 as const }),
          decideForOrg: async () => ({ ...HARNESS_ENABLED, harnessMajor: 2 as const, harnessOn: true, resumeInFlightRuns: true }),
        },
        agentAccess: grantingAccess(() => definition),
      }).submit({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: 'k',
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      });
      if (outcome.route !== 'durable') return expect.fail('expected a durable run');
      return outcome.admission.run.requires?.features ?? [];
    };
    expect(await admitted(bound())).to.include(DESCRIPTOR_PINS_FEATURE);
    expect(await admitted(agentDefinition())).to.not.include(DESCRIPTOR_PINS_FEATURE);
  });

  it('PN-9: a child of a run pinning them requires the feature too', () => {
    const spec = pin(bound());
    const pinned = { spec, specDigest: canonicalDigest(spec), capabilities: {}, capabilitiesDigest: DURABLE.digest };
    expect(childFeatures(pinned as never)).to.include(DESCRIPTOR_PINS_FEATURE);
    const legacy = pin(bound(), run([]));
    expect(
      childFeatures({ ...pinned, spec: legacy, specDigest: canonicalDigest(legacy) } as never),
    ).to.not.include(DESCRIPTOR_PINS_FEATURE);
  });
});
