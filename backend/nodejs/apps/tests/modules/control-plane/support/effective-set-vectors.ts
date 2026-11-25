/**
 * The effective-set vectors (`contracts/control-plane/vectors/effective-set.json`):
 * the inputs are written here, the expected outputs are the domain resolver's,
 * checked by hand when a case is added and frozen in the file. Python reads the
 * same file (`tests/unit/control_plane/test_effective_set_vectors.py`).
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Binding, EffectiveSet, Grant, Installation } from '../../../../src/modules/control-plane/domain/objects';
import { kindAllowed, TOOLS_EFFECTS, TOOLS_GRANT, type EffectiveRunConfig } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { PinSubject, Snapshot, ToolEffect } from '../../../../src/modules/control-plane/domain/resolution';
import { aBinding, aGrant, anEntry, anInstallation, digest, instId, ORG_A } from './cp-objects';
import { aSnapshot, aSubject } from './resolution-fixtures';

export const VECTORS_PATH = resolve(__dirname, '../../../../../../../contracts/control-plane/vectors/effective-set.json');

export interface VectorInput {
  readonly id: string;
  readonly description: string;
  readonly subject: { readonly actorId: string; readonly agentKey: string | null };
  /** Dimension path → value, each set by the org layer. */
  readonly dimensions: Readonly<Record<string, unknown>>;
  readonly installations: readonly Installation[];
  readonly grants: readonly Grant[];
  readonly bindings: readonly Binding[];
  readonly actorTeams: readonly string[];
  readonly agentOwner: string | null;
  readonly toolEffects: Readonly<Record<string, Readonly<Record<string, ToolEffect | null>>>>;
}

export interface Vector extends VectorInput {
  readonly expected: { readonly set: EffectiveSet } | { readonly refusal: string; readonly installation: string | null };
}

export const vectorSubject = (input: VectorInput): PinSubject =>
  aSubject({ actorId: input.subject.actorId, agentKey: input.subject.agentKey, agentOwner: input.agentOwner });

export const vectorConfig = (input: VectorInput): EffectiveRunConfig => ({
  schemaVersion: 1,
  dimensions: Object.fromEntries(
    Object.entries(input.dimensions).map(([path, raw]) => {
      const value = raw as EffectiveRunConfig['dimensions'][string]['value'];
      return [path, { value, setBy: 'org' as const, ceilings: [{ layer: 'org' as const, value }] }];
    }),
  ),
});

export const vectorSnapshot = (input: VectorInput): Snapshot =>
  aSnapshot({
    installations: input.installations,
    grants: input.grants,
    bindings: input.bindings,
    actorTeams: input.actorTeams,
    toolEffects: input.toolEffects,
    agentKey: input.subject.agentKey,
    actorId: input.subject.actorId,
  });

const base = {
  subject: { actorId: 'u3', agentKey: 'a7' },
  dimensions: {},
  bindings: [],
  actorTeams: [],
  agentOwner: null,
  toolEffects: {},
} as const;

const HEX = '0123456789abcdef';
/** A distinct, well-formed digest per tool name. */
const toolDigest = (name: string): string => digest(HEX[Array.from(name).reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 16]!);
const tools = (...names: string[]) => Object.fromEntries(names.map((name) => [name, toolDigest(name)]));
const approved = (...names: string[]) => ({
  descriptorSetDigest: digest('9'),
  tools: tools(...names),
  approvalRef: null,
  at: 1,
  by: 'user:u-admin',
});

export const VECTOR_INPUTS: readonly VectorInput[] = [
  {
    ...base,
    id: 'ES-1',
    description: 'an org-visible MCP installation granted with a tool subset: only those tools, sorted, with their approved digests',
    installations: [anInstallation({ approved: approved('list_issues', 'create_issue', 'close_issue') })],
    grants: [aGrant({ entries: [anEntry(instId(1), ['list_issues', 'close_issue'])] })],
  },
  {
    ...base,
    id: 'ES-2',
    description: 'every unavailable reason in the documented order, one per granted installation',
    installations: [
      anInstallation({ id: instId(2), status: 'disabled' }),
      anInstallation({ id: instId(3), visibility: 'owner', owner: 'user:u9' }),
      anInstallation({ id: instId(4), approved: null, status: 'pending_approval' }),
      anInstallation({ id: instId(5) }),
      anInstallation({ id: instId(6), credentialMode: 'per_user' }),
      anInstallation({ id: instId(7), kind: 'toolset', item: 'toolset:pipeshub/jira@1' }),
    ],
    dimensions: { [kindAllowed('toolset')]: false, [TOOLS_GRANT]: { [instId(2)]: null, [instId(3)]: null, [instId(4)]: null, [instId(6)]: null, [instId(7)]: null } },
    grants: [aGrant({ entries: [1, 2, 3, 4, 5, 6, 7].map((n) => anEntry(instId(n))) })],
  },
  {
    ...base,
    id: 'ES-3',
    description: 'tools.effects: only tools whose effect is inside the ceiling; an unclassified tool is dropped; an item left with none is not_granted',
    installations: [
      anInstallation({ approved: approved('list_issues', 'create_issue', 'mystery') }),
      anInstallation({ id: instId(2), approved: approved('delete_all') }),
    ],
    dimensions: { [TOOLS_EFFECTS]: ['read', 'workspace'] },
    toolEffects: {
      [instId(1)]: { list_issues: 'read', create_issue: 'write', mystery: null },
      [instId(2)]: { delete_all: 'execute' },
    },
    grants: [aGrant({ entries: [anEntry(instId(1)), anEntry(instId(2))] })],
  },
  {
    ...base,
    id: 'ES-4',
    description: 'a required item without a credential refuses the run naming it',
    installations: [anInstallation({ credentialMode: 'per_user' }), anInstallation({ id: instId(2) })],
    grants: [aGrant({ entries: [anEntry(instId(1), null, true), anEntry(instId(2))] })],
  },
  {
    ...base,
    id: 'ES-5',
    description: 'the same item not required: unavailable{credential_missing}, the rest ready',
    installations: [anInstallation({ credentialMode: 'per_user' }), anInstallation({ id: instId(2) })],
    grants: [aGrant({ entries: [anEntry(instId(1)), anEntry(instId(2))] })],
  },
  {
    ...base,
    id: 'ES-6',
    description: "sharing: in a run of the agent, what the agent's owner put on it is visible to whoever runs it",
    agentOwner: 'u1',
    installations: [anInstallation({ visibility: 'owner', owner: 'user:u1', kind: 'skill', item: 'skill:pipeshub/notes@1' })],
    grants: [aGrant({ entries: [anEntry(instId(1))] })],
  },
  {
    ...base,
    id: 'ES-7',
    description: 'sharing does not reach an item of someone other than the agent owner',
    agentOwner: 'u1',
    installations: [anInstallation({ visibility: 'owner', owner: 'user:u9' })],
    grants: [aGrant({ entries: [anEntry(instId(1))] })],
  },
  {
    ...base,
    id: 'ES-8',
    description: "an assistant run (no agent): the org, user and team grants merged; another user's item is not visible; a team item is visible to a member",
    subject: { actorId: 'u3', agentKey: null },
    actorTeams: ['t1'],
    agentOwner: null,
    installations: [
      anInstallation({ visibility: 'owner', owner: 'user:u9' }),
      anInstallation({ id: instId(2), visibility: 'team', owner: 'team:t1' }),
      anInstallation({ id: instId(3), visibility: 'team', owner: 'team:t2' }),
    ],
    grants: [
      aGrant({ subject: 'org', entries: [anEntry(instId(1))] }),
      aGrant({ subject: 'team:t1', entries: [anEntry(instId(2), ['list_issues'])] }),
      aGrant({ subject: 'user:u3', entries: [anEntry(instId(2), ['create_issue']), anEntry(instId(3))] }),
    ],
  },
  {
    ...base,
    id: 'ES-9',
    description: 'a revoked and an expired per-user credential; an active one carries its binding reference',
    installations: [
      anInstallation({ credentialMode: 'per_user' }),
      anInstallation({ id: instId(2), credentialMode: 'per_user' }),
      anInstallation({ id: instId(3), credentialMode: 'org' }),
    ],
    bindings: [
      aBinding({ status: 'revoked' }),
      aBinding({ id: 'bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0002', installation: instId(2), status: 'expired' }),
      aBinding({ id: 'bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0003', installation: instId(3), owner: 'org' }),
    ],
    grants: [aGrant({ entries: [1, 2, 3].map((n) => anEntry(instId(n))) })],
  },
  {
    ...base,
    id: 'ES-10',
    description: 'a skill is pinned by its bundle, its one descriptor; tools.grant narrows it like any item',
    installations: [
      anInstallation({
        id: 'skill_notes',
        kind: 'skill',
        item: 'skill:pipeshub/notes@1.0.0',
        approved: { descriptorSetDigest: digest('b'), tools: { notes: digest('c') }, approvalRef: null, at: 1, by: 'system' },
      }),
      anInstallation({
        id: 'skill_pdf',
        kind: 'skill',
        item: 'skill:pipeshub/pdf@1.0.1',
        approved: { descriptorSetDigest: digest('d'), tools: { pdf: digest('e') }, approvalRef: null, at: 1, by: 'system' },
      }),
    ],
    dimensions: { [TOOLS_GRANT]: { skill_notes: null } },
    grants: [aGrant({ entries: [anEntry('skill_notes'), anEntry('skill_pdf')] })],
  },
];

/**
 * The org the shared file names: Python reads the same file, so it keeps its own fixture org and the Node fixtures'
 * org (24 hex, the id every store takes) is swapped in on read and out on write.
 */
const FILE_ORG = 'org-a';
const swapOrg = (text: string, from: string, to: string): string =>
  text.split(`"${from}"`).join(`"${to}"`).split(`/${from}/`).join(`/${to}/`);

export function readVectors(): Vector[] {
  return (JSON.parse(swapOrg(readFileSync(VECTORS_PATH, 'utf8'), FILE_ORG, ORG_A)) as { vectors: Vector[] }).vectors;
}

export function writeVectors(vectors: readonly Vector[]): void {
  const document = {
    description:
      'EffectiveSetResolver vectors (03-interfaces §4.3, 02-lld §5.2): inputs and the expected effective set or refusal. Node computes each; Python validates and recomputes each digest.',
    vectors,
  };
  writeFileSync(VECTORS_PATH, swapOrg(`${JSON.stringify(document, null, 2)}\n`, ORG_A, FILE_ORG));
}
