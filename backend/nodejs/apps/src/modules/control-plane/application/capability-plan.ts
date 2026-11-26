import type { LegacyCapabilities } from '../contracts/generated';
import type { CatalogItem } from '../domain/catalog-item';
import type { Grant, GrantEntry, Installation } from '../domain/objects';

/**
 * What today's stores say an org's installations and agent grants are
 * (02-lld §11.2), as control-plane objects. Pure: the same facts give the same
 * objects, so the migration and the dual-write sync share it and a rerun
 * changes nothing (MG-6). Revisions are the store's; every planned object
 * carries revision 1 and is compared without it.
 */

export type LegacyToolsetInstance =
  LegacyCapabilities['toolsetInstances'][number];
export type LegacyMcpInstance = LegacyCapabilities['mcpInstances'][number];
export type LegacyAgent = LegacyCapabilities['agents'][number];
export type LegacySkill = LegacyAgent['skills'][number];

export type { Hold, HoldReason } from '../domain/capability-holds';
import type { Hold } from '../domain/capability-holds';

export interface CapabilityPlan {
  readonly installations: readonly Installation[];
  readonly grants: readonly Grant[];
  readonly holds: readonly Hold[];
}

/** The release catalog's items of a kind, by lower-cased name. */
export type ReleaseItems = (kind: string) => readonly CatalogItem[];

const SYSTEM = 'system';
const userActor = (id: string | null): string =>
  id === null ? SYSTEM : `user:${id}`;
const slug = (value: string): string =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '');

/** The installation id of an agent-assigned skill: one per org and skill name. */
export const skillInstallationId = (name: string): string =>
  `skill_${name.replace(/[^A-Za-z0-9_-]+/g, '-')}`;

const latestByName = (
  items: readonly CatalogItem[],
  name: string,
): CatalogItem | undefined =>
  items
    .filter((item) => item.name.toLowerCase() === name.toLowerCase())
    .sort((a, b) => b.createdAt - a.createdAt || (a.ref < b.ref ? 1 : -1))[0];

const toolDigests = (item: CatalogItem): Record<string, string> =>
  Object.fromEntries(
    (item.descriptors.kind === 'tool' ? item.descriptors.items : []).map(
      (tool) => [tool.name, tool.digest],
    ),
  );

type Stamps = Pick<
  Installation,
  | 'schemaVersion'
  | 'orgId'
  | 'pending'
  | 'installedVia'
  | 'revision'
  | 'updatedAt'
  | 'updatedBy'
>;

function base(orgId: string, updatedAt: number, updatedBy: string): Stamps {
  return {
    schemaVersion: 1 as const,
    orgId,
    pending: null,
    installedVia: null,
    revision: 1,
    updatedAt,
    updatedBy,
  };
}

function toolsetInstallation(
  orgId: string,
  instance: LegacyToolsetInstance,
  release: ReleaseItems,
): Installation | Hold {
  const item = latestByName(release('toolset'), instance.toolsetType);
  if (item === undefined) {
    return { reason: 'unknown_toolset_type', object: instance.instanceId };
  }
  // A personal instance is its owner's alone: no other member's run sees it.
  const ownerId = instance.ownerId ?? null;
  return {
    ...base(orgId, instance.updatedAt, userActor(instance.createdBy)),
    id: instance.instanceId,
    item: item.ref,
    kind: 'toolset',
    owner: ownerId === null ? 'org' : `user:${ownerId}`,
    visibility: ownerId === null ? 'org' : 'owner',
    config: { instanceName: instance.instanceName },
    approved: {
      descriptorSetDigest: item.descriptorSetDigest,
      tools: toolDigests(item),
      approvalRef: null,
      at: instance.updatedAt,
      by: SYSTEM,
    },
    status: 'enabled',
    credentialMode:
      instance.authType.toUpperCase() === 'NONE' ? 'none' : 'per_user',
  };
}

function mcpInstallation(
  orgId: string,
  instance: LegacyMcpInstance,
  release: ReleaseItems,
  digestOf: (value: unknown) => string,
): Installation {
  const template =
    instance.typeId === null
      ? undefined
      : latestByName(release('mcp'), instance.typeId);
  const manifest = instance.manifest;
  const tools =
    manifest === null
      ? null
      : Object.fromEntries(
          Object.entries(manifest.tools).map(([name, tool]) => [
            name,
            tool.digest,
          ]),
        );
  return {
    ...base(orgId, instance.updatedAt, userActor(instance.createdBy)),
    id: instance.instanceId,
    item: template?.ref ?? `mcp:org/${slug(instance.instanceId)}@1`,
    kind: 'mcp',
    owner: 'org',
    visibility: 'org',
    config: { name: instance.name },
    approved:
      manifest === null || tools === null
        ? null
        : {
            descriptorSetDigest: digestOf(tools),
            tools,
            approvalRef: null,
            at: manifest.approvedAt,
            by: userActor(manifest.approvedBy),
          },
    status: 'enabled',
    credentialMode:
      instance.authMode.toLowerCase() === 'none' ? 'none' : 'per_user',
  };
}

/**
 * A builtin is the release's and the org's; a custom skill is its author's,
 * visible to them (CPS), shared through the agents they put it on (ESR-10).
 */
function skillInstallation(
  orgId: string,
  skill: LegacySkill,
  release: ReleaseItems,
  digestOf: (value: unknown) => string,
): Installation {
  const builtin = skill.builtin
    ? latestByName(release('skill'), skill.name)
    : undefined;
  const author = skill.builtin ? null : skill.createdBy;
  return {
    ...base(orgId, 0, SYSTEM),
    id: skillInstallationId(skill.name),
    item:
      builtin?.ref ??
      `skill:${skill.builtin ? 'pipeshub' : 'user'}/${slug(skill.name)}@${skill.version}`,
    kind: 'skill',
    owner: author === null ? 'org' : `user:${author}`,
    visibility: author === null ? 'org' : 'owner',
    config: { name: skill.name },
    // One descriptor, the bundle by its digest, as a release skill item has: the pin then carries the
    // digest the runtime loads by (SKL-G6).
    approved: {
      descriptorSetDigest: digestOf({ [skill.name]: skill.bundleDigest }),
      tools: { [skill.name]: skill.bundleDigest },
      approvalRef: null,
      at: 0,
      by: SYSTEM,
    },
    status: 'enabled',
    credentialMode: 'none',
  };
}

const byInstallation = (a: GrantEntry, b: GrantEntry): number =>
  a.installation < b.installation
    ? -1
    : a.installation > b.installation
      ? 1
      : 0;

function agentGrant(orgId: string, agent: LegacyAgent, holds: Hold[]): Grant {
  const entries = new Map<string, GrantEntry>();
  const add = (installation: string, tools: readonly string[] | null): void => {
    const seen = entries.get(installation);
    const merged =
      seen === undefined
        ? tools
        : seen.tools === null || tools === null
          ? null
          : [...new Set([...seen.tools, ...tools])];
    entries.set(installation, {
      installation,
      tools: merged === null ? null : [...merged].sort(),
      required: false,
    });
  };
  for (const toolset of agent.toolsets) {
    if (toolset.instanceId === null) {
      holds.push({
        reason: 'type_keyed_toolset',
        object: `agent:${agent.agentKey}/${toolset.nodeKey}`,
      });
      continue;
    }
    add(toolset.instanceId, toolset.tools);
  }
  for (const server of agent.mcpServers) add(server.instanceId, server.tools);
  for (const skill of agent.skills) add(skillInstallationId(skill.name), null);
  return {
    schemaVersion: 1,
    orgId,
    subject: `agent:${agent.agentKey}`,
    entries: [...entries.values()].sort(byInstallation),
    revision: 1,
  };
}

export interface PlanInput {
  readonly orgId: string;
  readonly toolsetInstances: readonly LegacyToolsetInstance[];
  readonly mcpInstances: readonly LegacyMcpInstance[];
  readonly agents: readonly LegacyAgent[];
  readonly release: ReleaseItems;
  /** The canonical digest (libs `canonicalDigest`). */
  readonly digest: (value: unknown) => string;
}

export function planCapabilities(input: PlanInput): CapabilityPlan {
  const holds: Hold[] = [];
  const installations = new Map<string, Installation>();
  for (const instance of input.toolsetInstances) {
    const planned = toolsetInstallation(input.orgId, instance, input.release);
    if ('reason' in planned) holds.push(planned);
    else installations.set(planned.id, planned);
  }
  for (const instance of input.mcpInstances) {
    const planned = mcpInstallation(
      input.orgId,
      instance,
      input.release,
      input.digest,
    );
    installations.set(planned.id, planned);
  }
  for (const agent of input.agents) {
    for (const skill of agent.skills) {
      const planned = skillInstallation(
        input.orgId,
        skill,
        input.release,
        input.digest,
      );
      if (!installations.has(planned.id))
        installations.set(planned.id, planned);
    }
  }
  const grants = input.agents.map((agent) =>
    agentGrant(input.orgId, agent, holds),
  );
  const byId = <T>(rows: Iterable<T>, id: (row: T) => string): T[] =>
    [...rows].sort((a, b) => (id(a) < id(b) ? -1 : id(a) > id(b) ? 1 : 0));
  return {
    installations: byId(installations.values(), (inst) => inst.id),
    grants: byId(grants, (grant) => grant.subject),
    holds,
  };
}

/** Whether a stored object already says what the plan says (revision and audit stamps aside). */
export function samePlanned<T extends { revision: number }>(
  stored: T,
  planned: T,
  digest: (value: unknown) => string,
): boolean {
  const strip = (value: T): unknown => {
    const { revision: _revision, ...rest } = value as Record<
      string,
      unknown
    > & { revision: number };
    return rest;
  };
  return digest(strip(stored)) === digest(strip(planned));
}
