import type { EffectiveRunConfig } from '../domain/policy-alias';
import { isProblem } from '../domain/problem';
import type { EffectiveSet } from '../domain/objects';
import type { PinSubject, Snapshot } from '../domain/resolution';
import {
  shadowMismatches,
  type LegacyPin,
  type ShadowDimension,
} from '../domain/shadow-diff';
import type { EffectiveSetResolver } from '../ports/effective-set-resolver';
import type { ControlPlaneLog } from '../ports/log';
import type { ShadowMismatchCounter } from '../ports/shadow-metrics';
import type { SnapshotSource } from '../ports/snapshot-source';

export type { ShadowMismatchCounter } from '../ports/shadow-metrics';
import type { ControlPlanePin } from './control-plane-pin';

export interface ShadowReview {
  readonly mismatches: ShadowDimension[];
  readonly notVisible: ReadonlySet<string> | null;
  /**
   * For an org recorded as migrated (CPD-10 R3): the installations its
   * effective set holds ready, each with the tool names it holds. The set is
   * then the only authority, so what is not here is not granted. Null for an
   * org that is not migrated, whose definition stays the authority.
   */
  readonly granted: ReadonlyMap<string, ReadonlySet<string>> | null;
}

/** Who the run is for, as admission knows it (never from the client). */
export interface ShadowSubject {
  readonly orgId: string;
  readonly actorId: string;
  readonly agentKey: string | null;
  /** The agent's owner, whose items the agent shares (ESR-10); null when unknown. */
  readonly agentOwner: string | null;
}

export interface EffectiveSetShadowDeps {
  readonly mode: ControlPlanePin;
  /** The pinner's snapshots: the comparison reads what the pin reads, at the same head, and loads nothing of its own. */
  readonly snapshots: Pick<SnapshotSource, 'snapshot'>;
  readonly resolver: EffectiveSetResolver;
  readonly counter: ShadowMismatchCounter;
  readonly log: ControlPlaneLog;
}

/**
 * The comparison is with what the definition grants, so it resolves with no
 * ceilings: what the grants and installations give. The run's ceilings are
 * folded from its request, which is built from the definition this review
 * returns; the pin then resolves the set against them.
 */
const NO_CEILINGS: EffectiveRunConfig = { schemaVersion: 1, dimensions: {} };

/**
 * Whether the capability migration recorded the snapshot's org as migrated:
 * the row the snapshot's loader folds into the org's layer from the ledger,
 * and the one the pin carries as `rollout.CONTROL_PLANE_CAPABILITIES`.
 */
export const capabilitiesMigrated = (
  snapshot: Pick<Snapshot, 'orgId' | 'layers'>,
): boolean =>
  snapshot.layers.get(`org:${snapshot.orgId}`)?.rollout
    ?.CONTROL_PLANE_CAPABILITIES === true;

const toolName = (toolId: string): string =>
  toolId.slice(toolId.lastIndexOf('/') + 1);

const readyTools = (set: EffectiveSet): Map<string, ReadonlySet<string>> =>
  new Map(
    set.items
      .filter((item) => item.state === 'ready')
      .map((item) => [
        item.installation,
        new Set(item.tools.map((tool) => toolName(tool.toolId))),
      ]),
  );

/**
 * The shadow of 05 §1 rule 6 for the effective set: beside today's pin, it
 * resolves the control plane's effective set for the same subject, counts
 * every dimension on which they differ and changes nothing. It never fails
 * the admission it watches: a store failure is logged and counted as
 * `unavailable`.
 */
export class EffectiveSetShadow {
  constructor(private readonly deps: EffectiveSetShadowDeps) {}

  get active(): boolean {
    return this.deps.mode !== 'off';
  }

  /** Whether admission applies the visibility rule (CPD-7's switch; 05 §8 Q1), not only counts it. */
  get enforcing(): boolean {
    return this.deps.mode === 'on';
  }

  async observe(
    subject: ShadowSubject,
    legacy: LegacyPin,
  ): Promise<ShadowDimension[]> {
    return (await this.review(subject, legacy)).mismatches;
  }

  /**
   * The differences, counted, the granted installations the actor may not
   * see (`notVisible`; `null` when the control plane could not answer) and,
   * for a migrated org, what its effective set holds ready (`granted`).
   */
  async review(
    subject: ShadowSubject,
    legacy: LegacyPin,
  ): Promise<ShadowReview> {
    if (!this.active)
      return { mismatches: [], notVisible: new Set(), granted: null };
    let review: ShadowReview;
    try {
      const snapshot = await this.deps.snapshots.snapshot(
        subject.orgId,
        subject.agentKey,
        subject.actorId,
      );
      const set = this.deps.resolver.resolve(
        this.pinSubject(subject),
        NO_CEILINGS,
        snapshot,
      );
      review = isProblem(set)
        ? { mismatches: ['unavailable'], notVisible: null, granted: null }
        : {
            mismatches: shadowMismatches(legacy, set, snapshot.installations),
            notVisible: new Set(
              set.items
                .filter((item) => item.reason === 'not_visible')
                .map((item) => item.installation),
            ),
            granted: capabilitiesMigrated(snapshot) ? readyTools(set) : null,
          };
    } catch (error) {
      this.deps.log.warn('control_plane.shadow_unavailable', {
        orgId: subject.orgId,
        reasonCode: 'control_plane_unavailable',
        errorType: error instanceof Error ? error.name : typeof error,
      });
      review = { mismatches: ['unavailable'], notVisible: null, granted: null };
    }
    for (const dimension of review.mismatches) this.deps.counter.inc(dimension);
    if (review.mismatches.length > 0) {
      this.deps.log.info('control_plane.shadow_mismatch', {
        orgId: subject.orgId,
        agentKey: subject.agentKey,
        dimensions: review.mismatches,
      });
    }
    return review;
  }

  private pinSubject(subject: ShadowSubject): PinSubject {
    return {
      orgId: subject.orgId,
      actorId: subject.actorId,
      agentKey: subject.agentKey,
      agentOwner: subject.agentOwner,
      projectId: null,
      chatMode: 'agent',
      surface: 'web',
      request: {
        schemaVersion: 1,
        scope: { kind: 'request' },
      } as unknown as PinSubject['request'],
    };
  }
}
