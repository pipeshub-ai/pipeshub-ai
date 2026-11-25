/**
 * WP-111 ENGINEOFF: whether a coding engine may start a session for one person in one org now. An admin turning an
 * engine off (the org ceiling's `engines`, E4a), a group the person is not in (G-41) and an operator's stop of the
 * engine's version or of egress (ADR-OPS-01) are controls, not UI hints: every place an engine is chosen asks here,
 * at a turn's admission, the default chat's engine choice, a code_task child's spec pin and an engine run's claim.
 * Whether this server can run the engine at all (image, sandbox, runtime, live worker) is admission's own check.
 */
import {
  acpOptInOf,
  engineAdmits,
  engineRef,
  type AcpOptIn,
  isAcpKind,
  parseNarrowing,
  RELEASE_ENGINE_PROFILES,
  resolveEngineProfile,
  type PinnedEngineProfile,
} from '../domain/engine-profiles';
import { HarnessUnavailableError } from '../domain/errors';
import type { EngineProfileCeilings } from '../ports/engine-profiles';
import type { AccessGroupsPolicy } from '../../control-plane';
import type { GroupAccess } from '../ports/group-access';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { OperatorStops } from '../ports/operator-stops';

/** A run that would have started an engine session on an engine that is off fails with this (`run.failed`). */
export const ENGINE_OFF_ERROR = 'engine_off';

/** Why an engine may not start a session: off for the org, not for this person's groups, or stopped by an operator. */
export type EngineIneligibility =
  | 'engine_off'
  | 'engine_group_required'
  | 'engine_version_stopped'
  | 'egress_stopped';

export interface EngineEligibilityOptions {
  /** Absent: the org can set no ceiling, so every native engine is on. */
  ceilings?: EngineProfileCeilings | null;
  groupAccess?: Pick<GroupAccess, 'admits'> | null;
  operator?: Pick<OperatorStops, 'stoppedEngines' | 'egressStopped'> | null;
  releases?: readonly PinnedEngineProfile[];
  logger?: HarnessLogger;
}

/** What a caller already read this admission, so the check reads it once. */
export interface KnownPolicy {
  ceiling?: unknown;
  groups?: AccessGroupsPolicy | null;
}

/**
 * Whether the ceiling lets `engine` run: an engine unless the ceiling's `engines` leaves it out, where an ACP engine
 * needs the ceiling to name it when `acpOptIn` is `org` (`optedIn`). A ceiling that is not a narrowing turns every
 * engine off, as admission refuses it.
 */
export function ceilingAllows(
  ceiling: unknown,
  engine: { kind: string; version?: string },
  acpOptIn: AcpOptIn = 'release',
): boolean {
  const unlisted = acpOptIn === 'release' || !isAcpKind(engine.kind);
  if (ceiling === null || ceiling === undefined) return unlisted;
  const parsed = parseNarrowing(ceiling);
  if (parsed.narrowing === null) return false;
  const listed = parsed.narrowing.engines;
  if (listed === undefined) return unlisted;
  return engine.version === undefined
    ? listed.some(
        (selector) =>
          selector === engine.kind || selector.startsWith(`${engine.kind}@`),
      )
    : engineAdmits(listed, { kind: engine.kind, version: engine.version });
}

/**
 * Whether an agent's narrowing names only engines the org turned off: the release runs one of them, the ceiling
 * none. Distinguishes the admin's switch from a profile that asks for more than the release has.
 */
export function offByOrg(
  ceiling: unknown,
  agent: unknown,
  releases: readonly PinnedEngineProfile[] = RELEASE_ENGINE_PROFILES,
  acpOptIn: AcpOptIn = 'release',
): boolean {
  const withoutOrg = resolveEngineProfile({ agent }, releases, acpOptIn);
  if (withoutOrg.refusal !== null) return false;
  const withOrg = resolveEngineProfile(
    { org: ceiling, agent },
    releases,
    acpOptIn,
  );
  return withOrg.refusal !== null && withOrg.refusal.dimension === 'engine';
}

export class EngineEligibility {
  constructor(private readonly options: EngineEligibilityOptions = {}) {}

  private get releases(): readonly PinnedEngineProfile[] {
    return this.options.releases ?? RELEASE_ENGINE_PROFILES;
  }

  /** The org's ceiling as stored; throws `HarnessUnavailableError` when it cannot be read (nothing starts on a guess). */
  async ceiling(orgId: string): Promise<unknown> {
    if (this.options.ceilings === undefined || this.options.ceilings === null)
      return null;
    try {
      return await this.options.ceilings.read(orgId);
    } catch {
      throw new HarnessUnavailableError(
        "The organization's coding engine settings could not be read",
      );
    }
  }

  /**
   * Null when `engine` may start a session for `actorId`, else why not, in that order: the org's switch, the
   * person's groups, an operator's stop (checked only for a version). A group policy that cannot be read throws.
   * Operator controls that cannot be read admit, as admission's do: the sweeper stops a covered run anyway.
   */
  async check(
    orgId: string,
    actorId: string,
    engine: { kind: string; version?: string },
    known: KnownPolicy = {},
  ): Promise<EngineIneligibility | null> {
    const ceiling =
      'ceiling' in known ? known.ceiling : await this.ceiling(orgId);
    if (!ceilingAllows(ceiling, engine, acpOptInOf(this.options.ceilings)))
      return 'engine_off';
    const groups = this.options.groupAccess;
    if (
      groups !== undefined &&
      groups !== null &&
      !(await groups.admits(
        orgId,
        actorId,
        { kind: 'engine', engine: engine.kind },
        ...(known.groups !== undefined ? [known.groups] : []),
      ))
    ) {
      return 'engine_group_required';
    }
    return engine.version === undefined
      ? null
      : this.operatorStop(orgId, {
          kind: engine.kind,
          version: engine.version,
        });
  }

  /** The engine kinds that are on for this person, in release order (the first is the release default). */
  async enabledKinds(orgId: string, actorId: string): Promise<string[]> {
    const ceiling = await this.ceiling(orgId);
    const kinds = [
      ...new Set(this.releases.map((release) => release.engine.kind)),
    ];
    const on: string[] = [];
    for (const kind of kinds) {
      if ((await this.check(orgId, actorId, { kind }, { ceiling })) === null)
        on.push(kind);
    }
    return on;
  }

  private async operatorStop(
    orgId: string,
    engine: { kind: string; version: string },
  ): Promise<'engine_version_stopped' | 'egress_stopped' | null> {
    const operator = this.options.operator;
    if (operator === undefined || operator === null) return null;
    try {
      const [stopped, egress] = await Promise.all([
        operator.stoppedEngines(orgId),
        operator.egressStopped(orgId),
      ]);
      if (egress) return 'egress_stopped';
      return stopped.has(engineRef(engine)) ? 'engine_version_stopped' : null;
    } catch (error) {
      this.options.logger?.warn(
        'operator.controls_unknown',
        harnessLogMeta('operator.controls_unknown', {
          orgId,
          engine: engineRef(engine),
          choice: 'admit',
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return null;
    }
  }
}
