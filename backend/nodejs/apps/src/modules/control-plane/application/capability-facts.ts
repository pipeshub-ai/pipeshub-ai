import type {
  Binding,
  BindingOwnerRef,
  Grant,
  Installation,
  SubjectRef,
} from '../domain/objects';
import { releaseItemsOf } from '../domain/release-items';
import type { Snapshot, ToolEffect } from '../domain/resolution';
import type { BindingStore } from '../ports/binding-store';
import type { CapabilityLedger } from '../ports/capability-ledger';
import type { GrantStore } from '../ports/grant-store';
import type { InstallationStore } from '../ports/installation-store';

export interface CapabilityStores {
  readonly installations: Pick<InstallationStore, 'listForOrg'>;
  readonly grants: Pick<GrantStore, 'getMany'>;
  /** CPD-8's bindings; absent: the snapshot holds none, so a credentialed item reads `credential_missing`. */
  readonly bindings?: Pick<BindingStore, 'listForOwners'>;
  /** The capability migration's ledger; absent: no org reads as migrated. */
  readonly ledger?: Pick<CapabilityLedger, 'migrated'>;
}

export interface CapabilitySubject {
  readonly orgId: string;
  readonly actorId: string;
  readonly agentKey: string | null;
  readonly actorTeams: readonly string[];
}

export type CapabilityFacts = Pick<
  Snapshot,
  'installations' | 'grants' | 'toolEffects' | 'bindings'
> & {
  /** Whether the org is recorded as migrated: its effective set is then the authority for a run's items (CPD-10 R3). */
  readonly migrated: boolean;
};

/**
 * An installation's tools by effect: a release item's as classified; a skill
 * bundle reads (it is content the run loads). An org's own MCP server or
 * toolset is not classified, so it has no entry.
 */
function effectsOf(
  inst: Installation,
): ReadonlyMap<string, ToolEffect | null> | undefined {
  if (inst.kind === 'skill') {
    return new Map(
      Object.keys(inst.approved?.tools ?? {}).map((name) => [name, 'read']),
    );
  }
  const item = releaseItemsOf(inst.kind).find(
    (candidate) => candidate.ref === inst.item,
  );
  if (item?.descriptors.kind !== 'tool') return undefined;
  return new Map(
    item.descriptors.items.map((tool) => [tool.name, tool.effect ?? null]),
  );
}

/**
 * What the effective set is resolved from (02-lld §5.2): the org's
 * installations, the grants of the agent (an agent run) or of the org, the
 * actor and the actor's teams, the bindings a credentialed item can use
 * (the org's, the agent's and the actor's), and whether the org is recorded
 * as migrated (one point read).
 */
export async function loadCapabilityFacts(
  stores: CapabilityStores,
  subject: CapabilitySubject,
): Promise<CapabilityFacts> {
  const subjects: SubjectRef[] =
    subject.agentKey !== null
      ? [`agent:${subject.agentKey}`]
      : [
          'org',
          `user:${subject.actorId}`,
          ...subject.actorTeams.map((team): SubjectRef => `team:${team}`),
        ];
  const owners: BindingOwnerRef[] = [
    'org',
    ...(subject.agentKey !== null
      ? [`agent:${subject.agentKey}` as const]
      : []),
    `user:${subject.actorId}`,
  ];
  const [installations, grants, bindings, migrated] = await Promise.all([
    stores.installations.listForOrg(subject.orgId, {}),
    stores.grants.getMany(subject.orgId, subjects),
    stores.bindings?.listForOwners(subject.orgId, owners) ??
      Promise.resolve([] as Binding[]),
    stores.ledger?.migrated(subject.orgId) ?? Promise.resolve(false),
  ]);
  const toolEffects = new Map<string, ReadonlyMap<string, ToolEffect | null>>();
  for (const inst of installations) {
    const effects = effectsOf(inst);
    if (effects !== undefined) toolEffects.set(inst.id, effects);
  }
  return {
    installations: new Map<string, Installation>(
      installations.map((inst) => [inst.id, inst]),
    ),
    grants: new Map<string, Grant>(grants),
    toolEffects,
    bindings,
    migrated,
  };
}
