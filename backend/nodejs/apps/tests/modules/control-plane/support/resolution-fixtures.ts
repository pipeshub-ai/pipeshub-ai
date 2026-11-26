/** Subjects, snapshots and layers for the resolution contracts. */
import type { ConfiguredLlm } from '../../../../src/modules/control-plane/domain/llm';
import type { Binding, Grant, Installation } from '../../../../src/modules/control-plane/domain/objects';
import type { PolicyLayer, ScopeKind } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { PinSubject, Snapshot, ToolEffect } from '../../../../src/modules/control-plane/domain/resolution';
import { NO_FAMILIES, type ConfiguredFamilies } from '../../../../src/modules/control-plane/domain/routing';
import { ORG_A } from './cp-objects';

/**
 * A policy layer. The contracts also use sections CPD-3's schema does not have
 * yet (`tools.grant`, `catalog.kinds`, `budgets`, from CPD-7 and CP7), so the
 * fixture is typed by assertion; the store and pinner treat sections opaquely.
 */
export const aLayer = (kind: ScopeKind, sections: Record<string, unknown>, id?: string): PolicyLayer =>
  ({ schemaVersion: 1, scope: id === undefined ? { kind } : { kind, id }, ...sections }) as unknown as PolicyLayer;

export function aSubject(overrides: Partial<PinSubject> = {}): PinSubject {
  return {
    orgId: ORG_A,
    actorId: 'u3',
    agentKey: 'a7',
    projectId: null,
    chatMode: 'quick',
    surface: 'web',
    request: aLayer('request', {}),
    ...overrides,
  };
}

export interface SnapshotParts {
  readonly installations?: readonly Installation[];
  readonly grants?: readonly Grant[];
  readonly layers?: Readonly<Record<string, PolicyLayer>>;
  readonly bindings?: readonly Binding[];
  readonly actorTeams?: readonly string[];
  readonly agentOwnerTeam?: string | null;
  readonly toolEffects?: Readonly<Record<string, Readonly<Record<string, ToolEffect | null>>>>;
  readonly headRevision?: number;
  readonly deploymentRevision?: number;
  readonly models?: readonly ConfiguredLlm[];
  readonly families?: ConfiguredFamilies;
  readonly dependsOn?: readonly string[];
  readonly agentKey?: string | null;
  readonly actorId?: string;
}

/** A snapshot, frozen deeply so a reader that mutates it fails loudly. */
export function aSnapshot(parts: SnapshotParts = {}): Snapshot {
  const snapshot: Snapshot = {
    orgId: ORG_A,
    headRevision: parts.headRevision ?? 1,
    deploymentRevision: parts.deploymentRevision ?? 0,
    models: parts.models ?? [],
    families: parts.families ?? NO_FAMILIES,
    agentKey: parts.agentKey === undefined ? 'a7' : parts.agentKey,
    actorId: parts.actorId ?? 'u3',
    installations: new Map((parts.installations ?? []).map((inst) => [inst.id, inst])),
    grants: new Map((parts.grants ?? []).map((grant) => [grant.subject, grant])),
    layers: new Map(Object.entries(parts.layers ?? {})),
    bindings: parts.bindings ?? [],
    actorTeams: parts.actorTeams ?? [],
    agentOwnerTeam: parts.agentOwnerTeam ?? null,
    toolEffects: new Map(
      Object.entries(parts.toolEffects ?? {}).map(([inst, tools]) => [inst, new Map(Object.entries(tools))]),
    ),
    dependsOn: new Set(parts.dependsOn ?? []),
  };
  return deepFreeze(snapshot);
}

function deepFreeze<T>(value: T): T {
  if (value === null || typeof value !== 'object' || Object.isFrozen(value)) return value;
  if (value instanceof Map) {
    for (const inner of value.values()) deepFreeze(inner);
    const frozen = value as Map<unknown, unknown>;
    frozen.set = () => {
      throw new TypeError('a snapshot is read-only');
    };
    frozen.delete = () => {
      throw new TypeError('a snapshot is read-only');
    };
  } else if (value instanceof Set) {
    (value as Set<unknown>).add = () => {
      throw new TypeError('a snapshot is read-only');
    };
  } else {
    for (const inner of Object.values(value)) deepFreeze(inner);
  }
  return Object.freeze(value);
}
