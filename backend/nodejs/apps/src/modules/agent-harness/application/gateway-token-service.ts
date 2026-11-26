import { recordedRunFieldsOn } from '../domain/recorded-run-fields';
import { AuthorityClock, systemAuthorityClock } from '../domain/authority';
import { priorityClassOf } from '../domain/priority-class';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessUnauthorizedError,
  HarnessUnavailableError,
} from '../domain/errors';
import { HarnessRun } from '../domain/execution';
import type { GatewayClaims } from '../domain/gateway-token';
import { forOrg } from '../../../libs/context/orgContext';
import {
  gatewayGrantDigests,
  grantedRoutes,
  IssuedGatewayToken,
  RunModelGrant,
} from '../domain/gateway-token';
import { PinnedRunSpec } from '../domain/run-spec';
import {
  runTokenRefusal,
  runTokenRefused,
  RunTokenUse,
} from '../domain/run-token-standing';
import { verifyBindingProof } from '../domain/sandbox-binding';
import { GatewayTokenAuthority } from './gateway-token-authority';
import {
  EgressAllowed,
  EgressVolumeAlert,
  GatewayRefusal,
  GatewayRefusalJournal,
  ModelFallbackReport,
  SecretHiddenReport,
  RefusalOutcome,
} from './gateway-refusal-journal';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** A gateway's call runs as the org its run's verified gateway token names. */
const RUN_TOKEN_JOB = 'agent-harness.run-token';

type PinnedRun = { run: HarnessRun; pinned: PinnedRunSpec | null };

export interface PinnedRunReader {
  pinned(orgId: string, runId: string): Promise<PinnedRun | null>;
}

export interface GatewayTokenServiceOptions {
  tokens: GatewayTokenAuthority;
  runs: PinnedRunReader;
  clock?: AuthorityClock;
  logger?: HarnessLogger;
  refusals?: GatewayRefusalJournal<unknown>;
  /**
   * The keys a genuine sandbox broker signs bindings under (R38IDENT-FU-7, `brokerBindingKeys`), read per request so a
   * rotation applies at once. None: no proxy request is honoured.
   */
  bindingKeys?: () => readonly Buffer[];
}

/**
 * Issues gateway tokens (E2). A run's token is bound from Node's own records
 * (session, fence, the pinned grant), never from what the caller says beyond
 * which attempt it serves, and only while that attempt holds the run's lease:
 * the egress proxy refreshes on demand, so a sandbox outliving its attempt
 * stops getting tokens. A request token binds the caller's verified org and
 * user to a session and a request id the query service chose.
 */
export class GatewayTokenService {
  private readonly tokens: GatewayTokenAuthority;
  private readonly runs: PinnedRunReader;
  private readonly clock: AuthorityClock;
  private readonly logger: HarnessLogger;
  private readonly refusals: GatewayRefusalJournal<unknown> | undefined;
  private readonly bindingKeys: () => readonly Buffer[];

  constructor(options: GatewayTokenServiceOptions) {
    this.tokens = options.tokens;
    this.runs = options.runs;
    this.clock = options.clock ?? systemAuthorityClock;
    this.logger = options.logger ?? silentHarnessLogger;
    this.refusals = options.refusals;
    this.bindingKeys = options.bindingKeys ?? (() => []);
  }

  /**
   * SEC2 O-2: a sandbox gateway reports a refusal of the run's request with the
   * run's own gateway token, while its attempt holds the run or just after it
   * let go (SEC-C-11). Org, session and attempt come from the token and Node's
   * record, never from the body.
   */
  async recordRefusal(
    token: string | undefined,
    runId: string,
    refusal: GatewayRefusal,
  ): Promise<RefusalOutcome> {
    return this.asReporter(token, runId, (run, attemptId) =>
      this.journal().record(run, attemptId, refusal),
    );
  }

  private journal(): GatewayRefusalJournal<unknown> {
    if (this.refusals === undefined) {
      throw new HarnessUnavailableError(
        'Gateway decisions are not journaled here',
      );
    }
    return this.refusals;
  }

  /**
   * The run a gateway's report is about, from its own gateway token: `work` runs as the org the verified token
   * names, with the attempt the token binds, only while that attempt holds the run or just let it go (SEC-C-11).
   */
  private async asReporter<T>(
    token: string | undefined,
    runId: string,
    work: (run: HarnessRun, attemptId: string) => Promise<T>,
  ): Promise<T> {
    this.journal();
    return this.asRunToken(token, runId, 'report', ({ run }, claims) =>
      work(run, claims.attemptId),
    );
  }

  /**
   * Every gateway-token route's one check (SEC-C-11): a run-binding token for this run, whose attempt may still do
   * `use` on it by `runTokenRefusal`'s rule. `work` runs as the org the verified token names.
   */
  private async asRunToken<T>(
    token: string | undefined,
    runId: string,
    use: RunTokenUse,
    work: (found: PinnedRun, claims: GatewayClaims) => Promise<T>,
  ): Promise<T> {
    const claims = await this.tokens.verify(token);
    // Read as a string so the check still holds if another binding kind is ever verified here.
    const binding: string = claims.binding;
    if (binding !== 'run' || claims.runId !== runId) {
      throw new HarnessUnauthorizedError(
        'claims',
        'The gateway token is not for this run',
      );
    }
    return forOrg(claims.orgId, RUN_TOKEN_JOB, async () =>
      work(await this.standing(claims.orgId, runId, claims, use), claims),
    );
  }

  /**
   * OB-03: the egress proxy reports a destination it let the run's sandbox
   * reach, with the run's own gateway token, as it reports a refusal.
   */
  async recordEgressAllowed(
    token: string | undefined,
    runId: string,
    allowed: EgressAllowed,
  ): Promise<RefusalOutcome> {
    return this.asReporter(token, runId, (run, attemptId) =>
      this.journal().recordAllowed(run, attemptId, allowed),
    );
  }

  /**
   * C6.12: the egress proxy reports an unusual volume of the run's sandboxes,
   * with the run's own gateway token, as it reports a refusal.
   */
  async recordEgressVolume(
    token: string | undefined,
    runId: string,
    alert: EgressVolumeAlert,
  ): Promise<RefusalOutcome> {
    return this.asReporter(token, runId, (run, attemptId) =>
      this.journal().recordVolumeAlert(run, attemptId, alert),
    );
  }

  /**
   * G-48 (WP-100): the model gateway reports a call it moved to the run's alternate model, with the run's own
   * gateway token, as it reports a refusal; journaled as the run's `model.fallback`.
   */
  async recordModelFallback(
    token: string | undefined,
    runId: string,
    fallback: ModelFallbackReport,
  ): Promise<RefusalOutcome> {
    return this.asReporter(token, runId, (run, attemptId) =>
      this.journal().recordFallback(run, attemptId, fallback),
    );
  }

  /**
   * WP-103: a gateway reports a credential shape it hid from the run's model call, with the run's own gateway
   * token, as it reports a refusal; journaled as the run's `gateway.secret_hidden`.
   */
  async recordSecretHidden(
    token: string | undefined,
    runId: string,
    hidden: SecretHiddenReport,
  ): Promise<RefusalOutcome> {
    return this.asReporter(token, runId, (run, attemptId) =>
      this.journal().recordSecretHidden(run, attemptId, hidden),
    );
  }

  /** Creates the keyring once; rotates on a new operator id or an aged key. */
  provision(rotateId: string | null): Promise<void> {
    return this.tokens.provision(rotateId);
  }

  /**
   * The egress proxy's request for the attempt one of its sandboxes serves. `bindingProof` is what the broker attached
   * to that sandbox's binding (R38IDENT-FU-7): without one the broker made for exactly this org, run and attempt the
   * proxy's credential names no run, so it is refused before any run is read.
   */
  async forAttempt(
    orgId: string,
    runId: string,
    attemptId: string,
    bindingProof: string,
  ): Promise<IssuedGatewayToken> {
    if (
      !verifyBindingProof(
        bindingProof,
        { orgId, runId, attemptId },
        this.bindingKeys(),
      )
    ) {
      this.logger.warn(
        'gateway_token.binding_refused',
        harnessLogMeta('gateway_token.binding_refused', {
          orgId,
          runId,
          attemptId,
        }),
      );
      throw new HarnessUnauthorizedError(
        'claims',
        'No sandbox the broker started serves this run attempt',
      );
    }
    const { run, pinned } = this.withPin(
      orgId,
      runId,
      attemptId,
      await this.standing(orgId, runId, { attemptId }, 'act'),
    );
    const issued = await this.tokens.issue({
      binding: 'run',
      orgId: run.orgId,
      sessionId: run.sessionId,
      runId: run.runId,
      parentRunId: null,
      attemptId,
      fence: run.fence,
      ...gatewayGrantDigests(pinned.spec),
    });
    this.logger.info(
      'gateway_token.issued',
      harnessLogMeta('gateway_token.issued', {
        orgId,
        runId,
        attemptId,
        fence: run.fence,
        binding: 'run',
        expiresAt: issued.expiresAt,
      }),
    );
    return issued;
  }

  /**
   * E3: the model gateway, presenting a run's gateway token, reads what that
   * run may call. Only a run-binding token for this run, while its attempt
   * still holds the lease; the routes are the pin's, never the caller's.
   */
  async modelGrant(
    token: string | undefined,
    runId: string,
  ): Promise<RunModelGrant> {
    return this.asRunToken(token, runId, 'act', (found, claims) =>
      Promise.resolve(this.grantOf(claims, runId, found)),
    );
  }

  private grantOf(
    claims: GatewayClaims,
    runId: string,
    found: PinnedRun,
  ): RunModelGrant {
    const { run, pinned } = this.withPin(
      claims.orgId,
      runId,
      claims.attemptId,
      found,
    );
    const { routesDigest } = gatewayGrantDigests(pinned.spec);
    if (routesDigest !== claims.routesDigest) {
      this.refused(claims.orgId, runId, claims.attemptId, 'grant_changed');
      throw new HarnessUnauthorizedError(
        'claims',
        'The gateway token was issued for another fence or pin',
      );
    }
    const effort = pinned.spec.model.reasoningEffort;
    this.logger.info(
      'gateway_token.model_grant',
      harnessLogMeta('gateway_token.model_grant', {
        orgId: claims.orgId,
        runId,
        attemptId: claims.attemptId,
        fence: run.fence,
        routes: pinned.spec.model.policy.length,
      }),
    );
    return {
      runId,
      attemptId: claims.attemptId,
      routesDigest,
      ...grantedRoutes(pinned.spec),
      ...(effort !== undefined ? { reasoningEffort: effort } : {}),
      budgets: {
        maxCostUsd: pinned.spec.budgets.maxCostUsd,
        maxTokens: pinned.spec.budgets.maxTokens,
      },
      actorId: run.actorId,
      // The fields an edition records on the run, for its own reading of the grant.
      ...recordedRunFieldsOn(run),
      priorityClass: priorityClassOf(run),
    };
  }

  /**
   * The run, if `holder`'s attempt may do `use` on it now. The worker names the attempt it asks a token for and the
   * token is minted at the run's fence, so a holder without one is checked at the run's.
   */
  private async standing(
    orgId: string,
    runId: string,
    holder: { attemptId: string; fence?: number },
    use: RunTokenUse,
  ): Promise<PinnedRun> {
    const found = await this.runs.pinned(orgId, runId);
    if (found === null) {
      this.refused(orgId, runId, holder.attemptId, 'run_not_found');
      throw new HarnessNotFoundError();
    }
    const refusal = runTokenRefusal(
      found.run,
      { attemptId: holder.attemptId, fence: holder.fence ?? found.run.fence },
      use,
      this.clock.now(),
    );
    if (refusal !== null) {
      this.refused(orgId, runId, holder.attemptId, refusal);
      throw runTokenRefused(refusal);
    }
    return found;
  }

  private withPin(
    orgId: string,
    runId: string,
    attemptId: string,
    { run, pinned }: PinnedRun,
  ): { run: HarnessRun; pinned: PinnedRunSpec } {
    if (pinned === null) {
      this.refused(orgId, runId, attemptId, 'grant_not_pinned');
      throw new HarnessConflictError(
        'GRANT_NOT_PINNED',
        'The run has no pinned grant to bind a gateway token to',
      );
    }
    return { run, pinned };
  }

  private refused(
    orgId: string,
    runId: string,
    attemptId: string,
    reason: string,
  ): void {
    this.logger.warn(
      'gateway_token.refused',
      harnessLogMeta('gateway_token.refused', {
        orgId,
        runId,
        attemptId,
        reason,
      }),
    );
  }
}
