import {
  actorRefOfUser,
  ControlPlaneTransaction,
  orgScope,
  userScope,
} from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  engineKindOf,
  NO_ENGINE,
  ORG_DEFAULT_CHOICE,
  resolveCodingEngine,
  type CodingEngineSource,
} from '../domain/coding-engines';
import {
  acpOptInOf,
  engineRef,
  isAcpKind,
  PinnedEngineProfile,
  RELEASE_ENGINE_PROFILES,
  resolveEngineProfile,
} from '../domain/engine-profiles';
import { RELEASE_ENGINE_NAMES } from '../contracts/generated/core/policy-data';
import {
  enginesWithoutGvisor,
  GVISOR_REQUIRED_MESSAGE,
  offersFloor,
  RELEASE_ISOLATION_FLOOR,
} from '../domain/engine-isolation';
import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessGroupAccessRefusedError,
  HarnessUnavailableError,
} from '../domain/errors';
import type { CodingEngineView } from '../contracts/generated/ext/pipeshub/api/coding-engine-view';
import type {
  CodingEnginePreferences,
  StoredCodingEngines,
} from '../ports/coding-engines';
import type { EngineAvailability } from '../ports/engine-availability';
import type { EngineProfileCeilings } from '../ports/engine-profiles';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { ModelAccessService } from './model-access-service';
import type { GroupAccess } from '../ports/group-access';
import {
  EngineEligibility,
  type EngineIneligibility,
} from './engine-eligibility';
import {
  engineExecutorAbsent,
  type WorkerFeatureAvailability,
} from './worker-feature-availability';

/** What a picker and an admission say when engines have no sandbox on this deployment (Helm today, recs2). */
export const ENGINE_SANDBOX_MISSING_MESSAGE =
  'Coding engines need the sandbox add-on, not installed on this deployment';

/**
 * What a picker and a refusal say when an engine's image is there but its profile is signed by no key this
 * deployment trusts (ADR-SEC-01 D7): `ENGINE_UNAVAILABLE` with this reason, never "not installed".
 */
export const ENGINE_PROFILE_UNTRUSTED = 'engine_profile_untrusted';
export const ENGINE_PROFILE_UNTRUSTED_MESSAGE =
  "This engine's profile isn't signed for this deployment";
/** Not a setting anyone can change in the app: the server has no image for it, which its operator builds. */
export const ENGINE_NOT_INSTALLED_MESSAGE =
  "This coding engine isn't installed on this server. It isn't a setting: whoever runs the server installs it with deployment/sandbox-images/engine-build.sh and the engine's name";

/** What the coding-engine pickers show (the `CodingEngineView` contract). */
export type { CodingEngineView };
/** One engine the org's ceiling allows, and why it cannot be used now (a catalogue code), if it cannot. */
export type CodingEngineOption = CodingEngineView['engines'][number];

/** What admission reads and saves of the coding-engine choices (ENGPICK, CMDUI2, DEEPROUTE, C14.01). */
export type AdmissionCodingEngine = Pick<
  CodingEngineService,
  'forTurn' | 'sessionChoice' | 'assertSessionChoice' | 'saveSessionChoice'
> &
  Partial<
    Pick<CodingEngineService, 'autoRoute' | 'sessionPin' | 'saveSessionPin'>
  >;

/** The engine a default-chat Agent-mode turn hands its code tasks to; `withheld` names why a chosen one is not. */
export type TurnCodingEngine =
  | {
      engine: string;
      source: CodingEngineSource;
      /** WP-111: the chosen engine is off for the org or the person's groups, so the turn uses `engine` instead. */
      movedFrom?: { engine: string; reason: EngineIneligibility };
    }
  | { engine: null; source: CodingEngineSource; withheld?: string };

export interface CodingEngineServiceOptions {
  ceilings: EngineProfileCeilings;
  /** Also (SIGNGAP) whether a live worker carries a person's own vendor sign-in, shown as `vendorSignIn`. */
  access: Pick<ModelAccessService, 'engineRefusals' | 'signInsCarried'>;
  preferences: CodingEnginePreferences;
  /** Every saved choice is one audited control-plane write (CPD-1, C-32). */
  transaction: ControlPlaneTransaction;
  availability?: EngineAvailability;
  releases?: readonly PinnedEngineProfile[];
  /** The dev/test override is on (`../domain/engine-isolation`); unset: read from the environment. */
  withoutGvisor?: boolean;
  logger?: HarnessLogger;
  /** G-41: an engine the org lets only some groups use is greyed for anyone outside them. Absent: none is. */
  groupAccess?: GroupAccess;
  /**
   * The workers' sightings: when live workers prove none runs engines (`executor_engine`; no broker, or
   * `HARNESS_ENGINE_RUNS` off), every engine is greyed `ENGINE_SANDBOX_MISSING`, as admission withholds it. Before any
   * worker is seen nothing is greyed (ADR-CAP-01 D2). Absent: not consulted.
   */
  engineWorkers?: Pick<WorkerFeatureAvailability, 'live'>;
}

type Refusal = Error | null;

/**
 * The default chat's coding engine (ENGPICK) and the engine checks the
 * pickers and saves share. Every reason is an existing admission check: the
 * org's engine ceiling (E4a), the broker's images (H1), its runtimes against
 * the engine's isolation floor (gVisor on production) and the vendor rules
 * over the org's configured models (A1). Nothing here is a rule of its own.
 */
export class CodingEngineService {
  private readonly releases: readonly PinnedEngineProfile[];

  private readonly withoutGvisor: boolean;

  /** WP-111: whether a chosen engine is on for this person, as admission and a child's spec pin ask it. */
  private readonly eligibility: EngineEligibility;

  constructor(private readonly options: CodingEngineServiceOptions) {
    this.releases = options.releases ?? RELEASE_ENGINE_PROFILES;
    this.withoutGvisor = options.withoutGvisor ?? enginesWithoutGvisor();
    this.eligibility = new EngineEligibility({
      ceilings: options.ceilings,
      groupAccess: options.groupAccess ?? null,
      ...(options.releases !== undefined ? { releases: options.releases } : {}),
      ...(options.logger !== undefined ? { logger: options.logger } : {}),
    });
  }

  /**
   * The engines the org's ceiling allows, in release order, each with why it cannot be used now. An ACP engine
   * (`acp-<agent>`) is listed only where it is opted in (`acpOptIn`) and the broker says it is installed (its
   * profile and image are there), or that its profile is there but untrusted, greyed with that reason so an admin
   * sees what to fix: not installed, it is no choice to grey out but absent, also while the broker cannot be asked.
   */
  async engines(orgId: string): Promise<CodingEngineOption[]> {
    const allowed = this.allowed(await this.ceiling(orgId));
    const [refusals, missing, untrusted] = await Promise.all([
      this.refusals(orgId, allowed),
      this.knownMissing(),
      this.untrustedProfiles(),
    ]);
    const listed = allowed.filter(
      (p) =>
        !isAcpKind(p.engine.kind) ||
        untrusted.has(engineRef(p.engine)) ||
        (missing !== null && !missing.has(engineRef(p.engine))),
    );
    return listed.map((profile) => {
      const kind = profile.engine.kind;
      const refusal = refusals.get(kind) ?? null;
      const name = RELEASE_ENGINE_NAMES[kind];
      return {
        kind,
        ...(name !== undefined ? { name } : {}),
        ...(refusal === null
          ? { available: true }
          : { available: false, reason: reasonOf(refusal) }),
      };
    });
  }

  async view(orgId: string, userId: string): Promise<CodingEngineView> {
    const [engines, stored, vendorSignIn, belowFloor, syscallFilter] =
      await Promise.all([
        this.engines(orgId).then((listed) =>
          this.forPerson(orgId, userId, listed),
        ),
        this.stored(orgId, userId),
        this.options.access.signInsCarried(),
        this.runsBelowGvisor(),
        this.gvisorSyscallFilter(),
      ]);
    return {
      engines,
      orgDefault: stored.org,
      ...(belowFloor ? { withoutGvisor: true } : {}),
      ...(syscallFilter !== null ? { gvisorSyscallFilter: syscallFilter } : {}),
      vendorSignIn,
      choice: stored.user ?? ORG_DEFAULT_CHOICE,
      autoRoute: stored.orgAutoRoute !== false,
    };
  }

  /** G-41: a person outside the groups the org lets use `kind` cannot choose it (`ENGINE_GROUP_REQUIRED`). */
  private async assertGroupAllows(
    orgId: string,
    userId: string,
    kind: string,
  ): Promise<void> {
    const access = this.options.groupAccess;
    if (access === undefined) return;
    if (
      !(await access.admits(orgId, userId, { kind: 'engine', engine: kind }))
    ) {
      throw new HarnessGroupAccessRefusedError('ENGINE_GROUP_REQUIRED');
    }
  }

  /**
   * The org's engines as one person may use them (G-41): an engine their groups do not hold is greyed with
   * `ENGINE_GROUP_REQUIRED`. A group policy that cannot be read greys nothing here: admission checks it again.
   */
  private async forPerson(
    orgId: string,
    userId: string,
    engines: CodingEngineOption[],
  ): Promise<CodingEngineOption[]> {
    const access = this.options.groupAccess;
    if (access === undefined) return engines;
    try {
      const policy = await access.policy(orgId);
      if (policy?.engines === undefined) return engines;
      return await Promise.all(
        engines.map(async (engine) =>
          engine.available &&
          !(await access.admits(
            orgId,
            userId,
            { kind: 'engine', engine: engine.kind },
            policy,
          ))
            ? {
                ...engine,
                available: false,
                reason: { code: 'ENGINE_GROUP_REQUIRED' },
              }
            : engine,
        ),
      );
    } catch {
      return engines;
    }
  }

  /**
   * DEEPROUTE: whether the org lets a turn that may hand off to an engine do so on its own (on by default). Read at
   * admission; never throws: a setting that cannot be read is the default.
   */
  async autoRoute(orgId: string, userId: string): Promise<boolean> {
    try {
      return (
        (await this.options.preferences.read(orgId, userId)).orgAutoRoute !==
        false
      );
    } catch {
      return true;
    }
  }

  /** The org's "Hand long work to the coding engine" switch (an admin's), as one audited write. */
  async setOrgAutoRoute(
    orgId: string,
    userId: string,
    on: boolean,
  ): Promise<CodingEngineView> {
    await this.audited(orgId, userId, orgScope(orgId), 'org/autoRoute', {
      read: (current) => current.orgAutoRoute !== false,
      write: () => this.options.preferences.writeOrgAutoRoute(orgId, on),
      after: on,
    });
    this.options.logger?.info(
      'coding_engine.org_auto_route_set',
      harnessLogMeta('coding_engine.org_auto_route_set', {
        orgId,
        actorId: userId,
        autoRoute: on,
      }),
    );
    return this.view(orgId, userId);
  }

  /** The person's choice; an engine must be usable now. */
  async setChoice(
    orgId: string,
    userId: string,
    choice: string,
  ): Promise<CodingEngineView> {
    if (choice !== ORG_DEFAULT_CHOICE && choice !== NO_ENGINE) {
      await this.assertUsable(orgId, choice);
      await this.assertGroupAllows(orgId, userId, choice);
    }
    const stored = choice === ORG_DEFAULT_CHOICE ? null : choice;
    await this.audited(orgId, userId, userScope(userId), `user/${userId}`, {
      read: (current) => current.user,
      write: () => this.options.preferences.writeUser(orgId, userId, stored),
      after: stored,
    });
    return this.view(orgId, userId);
  }

  /**
   * This chat's engine (SLASH3 `/engine`, SD5): an engine kind, `none`, or null (back to the person's). Checked
   * by `assertSessionChoice`, then saved by `saveSessionChoice`.
   */
  async setSessionChoice(
    orgId: string,
    userId: string,
    sessionId: string,
    choice: string | null,
    agentEngines?: readonly string[],
  ): Promise<{ engine: string | null }> {
    await this.assertSessionChoice(orgId, choice, agentEngines);
    if (choice !== null && choice !== NO_ENGINE) {
      await this.assertGroupAllows(orgId, userId, choice);
    }
    await this.saveSessionChoice(orgId, userId, sessionId, choice);
    return { engine: choice };
  }

  /**
   * Refuses a chat's choice its turns could not honour: an engine must be usable now, as a person's save is;
   * in an agent's chat it must be one the agent's profile lists. `/engine` and a new chat's first message
   * (CMDUI2) are checked here alike.
   */
  async assertSessionChoice(
    orgId: string,
    choice: string | null,
    agentEngines?: readonly string[],
  ): Promise<void> {
    // An agent's turn runs one of the engines its profile lists, never "none": a choice it would ignore is
    // refused here rather than saved and shown as taken.
    if (
      choice !== null &&
      agentEngines !== undefined &&
      !agentEngines.some((engine) => engineKindOf(engine) === choice)
    ) {
      throw new HarnessConflictError(
        'ENGINE_UNAVAILABLE',
        "This agent's profile does not list that engine",
      );
    }
    if (choice !== null && choice !== NO_ENGINE) {
      await this.assertUsable(orgId, choice);
    }
  }

  /** A checked chat choice, saved as one audited control-plane write (`coding-engine`, `session/<id>`). */
  async saveSessionChoice(
    orgId: string,
    userId: string,
    sessionId: string,
    choice: string | null,
  ): Promise<void> {
    await this.options.transaction.run(
      { orgId, actor: actorRefOfUser(userId) },
      async (tx) => {
        const before = await this.options.preferences.readSession(
          orgId,
          sessionId,
        );
        // Picking an engine again is how a person moves the chat off the version it started on (C14.01).
        tx.external(async () => {
          await this.options.preferences.writeSession(orgId, sessionId, choice);
          await this.options.preferences.writeSessionPin(
            orgId,
            sessionId,
            null,
          );
        });
        return {
          result: null,
          change: {
            scope: userScope(userId),
            object: { type: 'coding-engine', id: `session/${sessionId}` },
            revisionFrom: null,
            revisionTo: null,
            changeDigest: canonicalDigest({ before, after: choice }),
            changed: ['codingEngine'],
          },
        };
      },
    );
  }

  /** This chat's own choice, as `/engine` shows it; null: none set. */
  sessionChoice(orgId: string, sessionId: string): Promise<string | null> {
    return this.options.preferences.readSession(orgId, sessionId);
  }

  /** The engine version (`kind@version`) this chat's engine turns run on (C14.01); null: none yet. */
  sessionPin(orgId: string, sessionId: string): Promise<string | null> {
    return this.options.preferences.readSessionPin(orgId, sessionId);
  }

  /**
   * Records the version an admission ran the chat's engine on. Not a person's setting, so not an audited
   * control-plane write: the run's own pinned executor is the record of what ran.
   */
  saveSessionPin(
    orgId: string,
    sessionId: string,
    engine: string,
  ): Promise<void> {
    return this.options.preferences.writeSessionPin(orgId, sessionId, engine);
  }

  /** The org's default (an admin's); an engine must be usable now. */
  async setOrgDefault(
    orgId: string,
    userId: string,
    engine: string | null,
  ): Promise<CodingEngineView> {
    if (engine !== null) await this.assertUsable(orgId, engine);
    await this.audited(orgId, userId, orgScope(orgId), 'org', {
      read: (current) => current.org,
      write: () => this.options.preferences.writeOrg(orgId, engine),
      after: engine,
    });
    this.options.logger?.info(
      'coding_engine.org_default_set',
      harnessLogMeta('coding_engine.org_default_set', {
        orgId,
        actorId: userId,
        engine: engine ?? NO_ENGINE,
      }),
    );
    return this.view(orgId, userId);
  }

  /**
   * Refuses an engine that cannot be used now, with the same typed refusal
   * its admission would give: the ceiling's `ENGINE_PROFILE_WIDENING`,
   * `ENGINE_UNAVAILABLE`, or the vendor rule's code.
   */
  async assertUsable(orgId: string, kind: string): Promise<void> {
    const decision = resolveEngineProfile(
      { org: await this.ceiling(orgId), agent: { engines: [kind] } },
      this.releases,
      acpOptInOf(this.options.ceilings),
    );
    if (decision.refusal !== null) {
      throw new HarnessEngineProfileRefusedError(decision.refusal);
    }
    const refusal =
      (await this.refusals(orgId, [decision.profile])).get(
        decision.profile.engine.kind,
      ) ?? null;
    if (refusal !== null) throw refusal;
  }

  /**
   * The engine a default-chat Agent-mode turn may hand off to, as the person
   * and the org chose it, checked against the org's switches and model
   * access. A chosen engine an admin turned off (or the person's groups do
   * not hold) moves to one that is on: the org's default first, then release
   * order (WP-111), so the turn says which it used. The image and a live
   * engine worker are admission's own engine step. Never throws: anything
   * that cannot be decided gives no engine (today's chat).
   */
  async forTurn(
    orgId: string,
    userId: string,
    sessionId?: string,
    /** A checked choice for the chat this turn creates (CMDUI2): it has no session to read yet. */
    newChatChoice?: string,
  ): Promise<TurnCodingEngine> {
    let chosen: ReturnType<typeof resolveCodingEngine>;
    let orgDefault: string | null;
    try {
      const stored = await this.stored(orgId, userId);
      orgDefault = stored.org;
      chosen = resolveCodingEngine({
        ...stored,
        session:
          sessionId !== undefined
            ? await this.options.preferences.readSession(orgId, sessionId)
            : (newChatChoice ?? null),
      });
    } catch {
      return { engine: null, source: 'none', withheld: 'unavailable' };
    }
    if (chosen.engine === null) return { engine: null, source: chosen.source };
    try {
      const off = await this.eligibility.check(orgId, userId, {
        kind: chosen.engine,
      });
      if (off === null) {
        const refusal = await this.accessRefusal(orgId, chosen.engine);
        return refusal === null
          ? { engine: chosen.engine, source: chosen.source }
          : { engine: null, source: chosen.source, withheld: refusal };
      }
      const fallback = await this.fallback(
        orgId,
        userId,
        chosen.engine,
        orgDefault,
      );
      return fallback === null
        ? { engine: null, source: chosen.source, withheld: off }
        : {
            engine: fallback,
            source: chosen.source,
            movedFrom: { engine: chosen.engine, reason: off },
          };
    } catch (error) {
      return {
        engine: null,
        source: chosen.source,
        withheld: codeOf(error).toLowerCase(),
      };
    }
  }

  /** The engine a turn moves to when its chosen one is off: the org's default if on, else the first on in release order. */
  private async fallback(
    orgId: string,
    userId: string,
    off: string,
    orgDefault: string | null,
  ): Promise<string | null> {
    const enabled = await this.eligibility.enabledKinds(orgId, userId);
    const order = [
      ...(orgDefault !== null && enabled.includes(orgDefault)
        ? [orgDefault]
        : []),
      ...enabled,
    ].filter((kind, at, all) => kind !== off && all.indexOf(kind) === at);
    if (order.length === 0) return null;
    const refusals = await this.options.access.engineRefusals(orgId, order);
    return order.find((kind) => (refusals.get(kind) ?? null) === null) ?? null;
  }

  /** The model-access refusal (A1) of one engine as a withholding reason; null: allowed. */
  private async accessRefusal(
    orgId: string,
    kind: string,
  ): Promise<string | null> {
    const refusal =
      (await this.options.access.engineRefusals(orgId, [kind])).get(kind) ??
      null;
    return refusal === null ? null : codeOf(refusal).toLowerCase();
  }

  /** One audited control-plane write (CPD-1) of a stored choice: the document itself cannot join the transaction. */
  private async audited(
    orgId: string,
    userId: string,
    scope: string,
    id: string,
    change: {
      read: (current: StoredCodingEngines) => string | boolean | null;
      write: () => Promise<void>;
      after: string | boolean | null;
    },
  ): Promise<void> {
    await this.options.transaction.run(
      { orgId, actor: actorRefOfUser(userId) },
      async (tx) => {
        const before = change.read(await this.stored(orgId, userId));
        tx.external(change.write);
        return {
          result: null,
          change: {
            scope,
            object: { type: 'coding-engine', id },
            revisionFrom: null,
            revisionTo: null,
            changeDigest: canonicalDigest({ before, after: change.after }),
            changed: ['codingEngine'],
          },
        };
      },
    );
  }

  private async stored(
    orgId: string,
    userId: string,
  ): Promise<StoredCodingEngines> {
    try {
      return await this.options.preferences.read(orgId, userId);
    } catch {
      throw new HarnessUnavailableError(
        'The coding engine settings could not be read',
      );
    }
  }

  private async ceiling(orgId: string): Promise<unknown> {
    try {
      return await this.options.ceilings.read(orgId);
    } catch {
      throw new HarnessUnavailableError(
        "The organization's engine profile could not be read",
      );
    }
  }

  /** The release engines the ceiling admits, one per kind. */
  private allowed(ceiling: unknown): PinnedEngineProfile[] {
    const kinds = [...new Set(this.releases.map((p) => p.engine.kind))];
    return kinds.flatMap((kind) => {
      const decision = resolveEngineProfile(
        { org: ceiling, agent: { engines: [kind] } },
        this.releases,
        acpOptInOf(this.options.ceilings),
      );
      return decision.refusal === null ? [decision.profile] : [];
    });
  }

  /**
   * Why each engine cannot run here: no engine sandbox on this deployment first (Helm today), then no image, then
   * no sandbox runtime at its isolation floor, then model access.
   */
  private async refusals(
    orgId: string,
    profiles: readonly PinnedEngineProfile[],
  ): Promise<Map<string, Refusal>> {
    const kinds = profiles.map((p) => p.engine.kind);
    const [sandboxMissing, missing, untrusted, modes, access] =
      await Promise.all([
        this.sandboxMissing(),
        this.missingImages(),
        this.untrustedProfiles(),
        this.offeredModes(),
        kinds.length === 0
          ? new Map<string, Refusal>()
          : this.options.access.engineRefusals(orgId, kinds),
      ]);
    const refusal = (profile: PinnedEngineProfile): Refusal => {
      if (sandboxMissing) {
        return new HarnessConflictError(
          'ENGINE_SANDBOX_MISSING',
          ENGINE_SANDBOX_MISSING_MESSAGE,
        );
      }
      if (untrusted.has(engineRef(profile.engine))) {
        return new HarnessConflictError(
          'ENGINE_UNAVAILABLE',
          ENGINE_PROFILE_UNTRUSTED_MESSAGE,
          ENGINE_PROFILE_UNTRUSTED,
        );
      }
      if (missing.has(engineRef(profile.engine))) {
        return new HarnessConflictError(
          'ENGINE_UNAVAILABLE',
          ENGINE_NOT_INSTALLED_MESSAGE,
        );
      }
      if (modes !== null && !offersFloor(modes, profile.isolationMin)) {
        return new HarnessConflictError(
          'ENGINE_ISOLATION_UNAVAILABLE',
          GVISOR_REQUIRED_MESSAGE,
        );
      }
      return access.get(profile.engine.kind) ?? null;
    };
    return new Map(
      profiles.map((profile) => [profile.engine.kind, refusal(profile)]),
    );
  }

  /**
   * Whether engines run below the gVisor floor here: the dev override is on and the broker offers no runtime at the
   * release floor. A run takes the strongest runtime offered, so with gVisor offered the override lowers nothing
   * (R31-GVISO: on a host with runsc, `withoutGvisor` once read true while every engine ran on gVisor). A broker
   * that cannot be asked keeps the warning: nothing shows the engines are not on runc.
   */
  private async runsBelowGvisor(): Promise<boolean> {
    if (!this.withoutGvisor) return false;
    const modes = await this.offeredModes();
    return modes === null || !offersFloor(modes, RELEASE_ISOLATION_FLOOR);
  }

  /** The broker's runtimes; null when it cannot be asked or does not say (admission and the worker decide again). */
  private async offeredModes(): Promise<ReadonlySet<string> | null> {
    try {
      return (await this.options.availability?.modes?.()) ?? null;
    } catch {
      return null;
    }
  }

  /** Whether the broker's gVisor filters its guests' syscalls; null when it offers no gVisor, does not say or cannot be asked. */
  private async gvisorSyscallFilter(): Promise<boolean | null> {
    try {
      return (await this.options.availability?.gvisorSyscallFilter?.()) ?? null;
    } catch {
      return null;
    }
  }

  /** The broker's unrunnable engines; null when it cannot be asked or is not configured. */
  private async knownMissing(): Promise<ReadonlySet<string> | null> {
    if (this.options.availability === undefined) return null;
    try {
      return await this.options.availability.unavailable();
    } catch {
      return null;
    }
  }

  /** The broker's unrunnable engines; when it cannot be asked, none (admission decides again). */
  private async missingImages(): Promise<ReadonlySet<string>> {
    try {
      return (await this.options.availability?.unavailable()) ?? new Set();
    } catch {
      return new Set();
    }
  }

  /** The unavailable engines whose profile no trusted key signed; when the broker cannot be asked or does not say, none. */
  private async untrustedProfiles(): Promise<ReadonlySet<string>> {
    try {
      return (await this.options.availability?.untrusted?.()) ?? new Set();
    } catch {
      return new Set();
    }
  }

  /**
   * Whether engines have no sandbox here: the broker says so (Helm today), or live workers prove none runs engines
   * (a deployment without the broker answers no health, so its silence alone proves nothing). Unknown is no.
   */
  private async sandboxMissing(): Promise<boolean> {
    const reported = await (
      this.options.availability?.sandboxMissing?.() ?? Promise.resolve(false)
    ).catch(() => false);
    return reported || (await engineExecutorAbsent(this.options.engineWorkers));
  }
}

/** A refusal as a picker shows it: its catalogue code, and its finer reason when it has one. */
function reasonOf(refusal: Exclude<Refusal, null>): {
  code: string;
  reasonCode?: string;
} {
  const reasonCode =
    refusal instanceof HarnessConflictError ? refusal.reasonCode : undefined;
  return {
    code: codeOf(refusal),
    ...(reasonCode !== undefined ? { reasonCode } : {}),
  };
}

/** The catalogue code of a refusal, as the wire names it without its prefix. */
export function codeOf(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === 'string' ? code.toUpperCase() : 'UNAVAILABLE';
}
