/**
 * Legacy facts and a world for CPD-7's capability migration: a legacy source
 * that serves an org's facts a page of agents at a time (and refuses another
 * org's, as the Python driver does), with the stores, ledger and audited
 * transaction a test reads back.
 */
import type { LegacyCapabilities } from '../../../../src/modules/control-plane/contracts/generated';
import type { CapabilityMigrationDeps } from '../../../../src/modules/control-plane/application/capability-migration';
import { releaseItemsOf } from '../../../../src/modules/control-plane/domain/release-items';
import type {
  CapabilityLedger,
  LegacyCapabilitySource,
  LegacyPageOptions,
} from '../../../../src/modules/control-plane/ports/capability-ledger';
import type { GrantStore } from '../../../../src/modules/control-plane/ports/grant-store';
import type { InstallationStore } from '../../../../src/modules/control-plane/ports/installation-store';
import type { ControlPlaneTransaction } from '../../../../src/modules/control-plane';
import { canonicalDigest } from '../../../../src/libs/utils/canonical-json';
import { digest, ORG_A, ORG_B } from './cp-objects';
import { RecordingLog } from './in-memory';

type Facts = Omit<LegacyCapabilities, 'schemaVersion' | 'orgId' | 'nextAgent'>;

export const JIRA = '6f1c2a7e-0d7b-4c36-9d55-1f0a2b3c4d5e';
export const LINEAR = '4b0d9a1c-5e6f-4a7b-8c9d-0e1f2a3b4c5d';
export const CUSTOM_MCP = '9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d';

export function legacyFacts(overrides: Partial<Facts> = {}): Facts {
  return {
    toolsetInstances: [
      { instanceId: JIRA, instanceName: 'Jira', toolsetType: 'jira', authType: 'OAUTH', createdBy: 'u1', updatedAt: 10 },
      { instanceId: 'calc-1', instanceName: 'Calculator', toolsetType: 'calculator', authType: 'NONE', createdBy: 'u1', updatedAt: 11 },
      { instanceId: 'odd-1', instanceName: 'Odd', toolsetType: 'no-such-toolset', authType: 'NONE', createdBy: 'u1', updatedAt: 12 },
    ],
    mcpInstances: [
      {
        instanceId: LINEAR,
        name: 'Linear',
        typeId: 'linear',
        authMode: 'oauth',
        createdBy: 'u1',
        updatedAt: 20,
        manifest: {
          tools: { list_issues: { digest: digest('a'), effect: 'read' }, create_issue: { digest: digest('b'), effect: 'write' } },
          approvedBy: 'u-admin',
          approvedAt: 21,
        },
      },
      { instanceId: CUSTOM_MCP, name: 'Mine', typeId: null, authMode: 'none', createdBy: 'u2', updatedAt: 22, manifest: null },
    ],
    agents: [
      {
        agentKey: 'a7',
        createdBy: 'u1',
        toolsets: [
          { nodeKey: 'n1', instanceId: JIRA, toolsetType: 'jira', tools: ['search_issues'] },
          { nodeKey: 'n2', instanceId: null, toolsetType: 'jira', tools: null },
        ],
        mcpServers: [{ instanceId: LINEAR, tools: null }],
        skills: [
          { name: 'data-analysis', version: '1.0.1', builtin: true, createdBy: null, bundleDigest: digest('c') },
          { name: 'my-notes', version: '0.1.0', builtin: false, createdBy: 'u1', bundleDigest: digest('d') },
        ],
      },
      { agentKey: 'a8', createdBy: 'u2', toolsets: [], mcpServers: [{ instanceId: CUSTOM_MCP, tools: ['ping'] }], skills: [] },
    ],
    ...overrides,
  };
}

/** Serves each org's facts one agent per page; `fail` makes the next read for an org throw. */
export class LegacyFacts implements LegacyCapabilitySource {
  readonly orgs = new Map<string, Facts>();
  readonly failing = new Set<string>();
  reads = 0;

  async page(orgId: string, options: LegacyPageOptions): Promise<LegacyCapabilities> {
    this.reads += 1;
    if (this.failing.has(orgId)) throw Object.assign(new Error('legacy store down'), { reason: 'legacy_source_unavailable' });
    const facts = this.orgs.get(orgId) ?? { toolsetInstances: [], mcpInstances: [], agents: [] };
    const first = options.after === null;
    const agents =
      options.agents === false
        ? []
        : options.agentKey !== undefined
          ? facts.agents.filter((agent) => agent.agentKey === options.agentKey)
          : facts.agents.filter((agent) => options.after === null || agent.agentKey > options.after).slice(0, 1);
    const last = agents.length === 0 || options.agentKey !== undefined || agents[0]!.agentKey === facts.agents.at(-1)?.agentKey;
    return {
      schemaVersion: 1,
      orgId,
      toolsetInstances: first ? facts.toolsetInstances : [],
      mcpInstances: first ? facts.mcpInstances : [],
      agents,
      nextAgent: last ? null : agents[0]!.agentKey,
    };
  }
}

export interface CapabilityWorld {
  readonly installations: InstallationStore;
  readonly grants: GrantStore;
  readonly ledger: CapabilityLedger;
  readonly transaction: ControlPlaneTransaction;
  configEntries(orgId: string): Promise<number>;
  readonly credentials?: CapabilityMigrationDeps['credentials'];
}

export function migrationDeps(world: CapabilityWorld, source: LegacyCapabilitySource, log = new RecordingLog()): CapabilityMigrationDeps {
  return {
    transaction: world.transaction,
    installations: world.installations,
    grants: world.grants,
    ledger: world.ledger,
    source,
    release: releaseItemsOf,
    digest: canonicalDigest,
    log,
    credentials: world.credentials ?? null,
    orgs: async () => [ORG_A, ORG_B],
  };
}

export { ORG_A };
