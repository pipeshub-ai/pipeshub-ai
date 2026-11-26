/**
 * CPD-10 §3: the pieces of a legacy turn's header for the suites that sign
 * one. The keyring holds the shared vector's keys
 * (`contracts/control-plane/vectors/resolved-turn.json`), the control plane
 * is the production pinner, snapshot loader and layer adapters over
 * in-memory documents.
 */
import { createPrivateKey, createPublicKey } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryConfigDocumentStore } from '../../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { orgPolicyLayers } from './org-policy-layers';
import { policySnapshotLoader } from '../../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { gatewayKeyring } from '../../../../src/modules/agent-harness/application/gateway-token-authority';
import { LegacyTurnHeader } from '../../../../src/modules/agent-harness/application/legacy-turn-header';
import { LegacyTurnResolver } from '../../../../src/modules/agent-harness/application/legacy-turn-resolver';
import { ResolvedTurnAuthority } from '../../../../src/modules/agent-harness/application/resolved-turn-authority';
import type { SigningKeyring } from '../../../../src/modules/agent-harness/application/signing-keyring';
import type { FlowRule } from '../../../../src/modules/agent-harness/domain/approvals';
import type {
  AttemptKeyring,
  AttemptSigningKey,
  PublishedAttemptKeys,
} from '../../../../src/modules/agent-harness/domain/authority';
import { releaseLayers } from '../../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../../src/modules/agent-harness/domain/policy';
import { holdsUnreadableLayer } from '../../../../src/modules/agent-harness/domain/resolved-turn';
import type { HarnessRolloutSettings } from '../../../../src/modules/agent-harness/domain/rollout';
import {
  CanonicalEffectiveSetResolver,
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type AgentDirectory,
  type CapabilityStores,
  type ResolvedTurn,
  type RunConfigPinner,
  type Snapshot,
} from '../../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../../control-plane/support/head-revisions-contract';

interface VectorKey {
  kid: string;
  jwk: Record<string, string>;
  publicKeyPem: string;
  retiredAtMs?: number;
}

export interface ResolvedTurnVector {
  issuer: string;
  audience: string;
  ttlSeconds: number;
  maxLifetimeSeconds: number;
  retentionMs: number;
  nowSeconds: number;
  keys: { active: VectorKey; retired: VectorKey };
  valid: { payload: ResolvedTurn; token: string };
  retired: { payload: ResolvedTurn; token: string };
  refused: Array<{ id: string; reason: string; token: string }>;
  binding: {
    request: VectorRequest;
    refused: Array<{ id: string; reason: string; request: VectorRequest }>;
  };
}

export interface VectorRequest {
  orgId: string;
  userId: string;
  agentKey: string | null;
  conversationId: string | null;
}

const CONTRACTS = join(__dirname, '../../../../../../../contracts');

export const RESOLVED_TURN_VECTOR = JSON.parse(
  readFileSync(join(CONTRACTS, 'control-plane/vectors/resolved-turn.json'), 'utf8'),
) as ResolvedTurnVector;

export const VECTOR_NOW_MS = RESOLVED_TURN_VECTOR.nowSeconds * 1000;

export interface ManualClock {
  now(): number;
  advance(ms: number): void;
}

export const manualClock = (start = VECTOR_NOW_MS): ManualClock => {
  let now = start;
  return {
    now: () => now,
    advance: (ms) => {
      now += ms;
    },
  };
};

/** A vector key as the keyring document holds one. */
export function signingKeyOf(key: VectorKey, createdAt: number): AttemptSigningKey {
  const privateKey = createPrivateKey({ key: { ...key.jwk }, format: 'jwk' });
  return {
    kid: key.kid,
    privateKey: privateKey.export({ type: 'pkcs8', format: 'pem' }),
    publicKey: createPublicKey(privateKey).export({ type: 'spki', format: 'pem' }),
    createdAt,
    retiredAt: key.retiredAtMs ?? null,
  };
}

export interface VectorKeyring {
  keys: SigningKeyring;
  signing: InMemoryConfigDocumentStore<AttemptKeyring>;
  published: InMemoryConfigDocumentStore<PublishedAttemptKeys>;
  clock: ManualClock;
}

/** The gateway keyring holding the vector's active key and the one it retired, at the vector's time. */
export async function vectorKeyring(clock: ManualClock = manualClock()): Promise<VectorKeyring> {
  const signing = new InMemoryConfigDocumentStore<AttemptKeyring>();
  const published = new InMemoryConfigDocumentStore<PublishedAttemptKeys>();
  const { active, retired } = RESOLVED_TURN_VECTOR.keys;
  await signing.replace(null, {
    activeKid: active.kid,
    keys: [signingKeyOf(retired, VECTOR_NOW_MS - 120_000), signingKeyOf(active, VECTOR_NOW_MS - 60_000)],
    rotateId: null,
  });
  const keys = gatewayKeyring({ signingKeys: signing, publishedKeys: published, clock });
  await keys.provision(null);
  return { keys, signing, published, clock };
}

const ROLLOUT_ON: HarnessRolloutSettings = {
  infrastructureReady: true,
  killSwitch: false,
  orgEnabled: true,
  modelFamilies: null,
};

export interface LegacyWorldOptions {
  /** The approvals document as the configuration store holds it. */
  approvals?: unknown;
  /** The deployment's flow rule, as Node read it at boot. */
  flow?: FlowRule | null;
  settings?: Partial<HarnessRolloutSettings>;
  /** The other Labs stages of the org. */
  stages?: Record<string, boolean>;
  capabilities?: CapabilityStores;
  /** A snapshot served as it is, instead of the loader's. */
  snapshot?: Snapshot;
  directory?: Pick<AgentDirectory, 'authorizeRun'> | null;
  clock?: ManualClock;
}

export interface LegacyWorld {
  heads: InMemoryHeadRevisions;
  pinner: RunConfigPinner;
  resolver: LegacyTurnResolver;
  authority: ResolvedTurnAuthority;
  header: LegacyTurnHeader;
  ring: VectorKeyring;
  /** How often the approvals document was read for its readability. */
  readabilityReads: () => number;
}

/** A process's legacy-turn header under `CONTROL_PLANE_PIN=on`, wired as `bindControlPlanePin` wires it. */
export async function legacyWorld(options: LegacyWorldOptions = {}): Promise<LegacyWorld> {
  const heads = new InMemoryHeadRevisions();
  const approvals = new InMemoryConfigDocumentStore<unknown>();
  if (options.approvals !== undefined && options.approvals !== null) {
    await approvals.replace(null, options.approvals);
  }
  const snapshot = options.snapshot;
  const snapshots = new ResolvedSnapshotCache({
    load:
      snapshot !== undefined
        ? async () => snapshot
        : policySnapshotLoader({
            heads,
            layers: orgPolicyLayers({ approvals, flow: options.flow ?? null }),
            rollout: { read: async () => ({ ...ROLLOUT_ON, ...options.settings }) },
            ...(options.stages !== undefined ? { stages: { read: async () => options.stages ?? {} } } : {}),
            ...(options.capabilities !== undefined ? { capabilities: options.capabilities } : {}),
          }),
    heads,
  });
  const pinner = new PolicyRunConfigPinner({
    snapshots,
    engine: new PolicyResolver(),
    effectiveSet: new CanonicalEffectiveSetResolver(),
    releases: releaseLayers(),
    prefix: prefixSources(),
    run: runSources(PARENT_DIMENSIONS),
    parent: PARENT_DIMENSIONS,
  });
  let reads = 0;
  const resolver = new LegacyTurnResolver({
    pinner,
    unreadableApprovals: async (orgId, agentKey) => {
      reads += 1;
      return holdsUnreadableLayer((await approvals.read()).value, orgId, agentKey);
    },
  });
  const ring = await vectorKeyring(options.clock);
  const authority = new ResolvedTurnAuthority({ keys: ring.keys, clock: ring.clock });
  const directory = options.directory ?? null;
  const header = new LegacyTurnHeader({ resolver, authority: () => authority, directory: () => directory });
  return { heads, pinner, resolver, authority, header, ring, readabilityReads: () => reads };
}
