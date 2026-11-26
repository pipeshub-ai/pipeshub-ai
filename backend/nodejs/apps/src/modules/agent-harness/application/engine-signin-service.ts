import {
  actorRefOfUser,
  ControlPlaneTransaction,
  userScope,
} from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  WORK_PLANS,
  signInKeepFromEnv,
  type EngineSignInView,
  type SignInKeep,
  type SignInAttemptView,
  type SignInCodeResult,
  type SignOutBy,
  type StoredEngineSignIn,
} from '../domain/engine-signin';
import {
  HarnessModelAccessRefusedError,
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../domain/errors';
import {
  everyOrgAllowsSignIns,
  type EngineSignInEraser,
  type EngineSignInRelay,
  type EngineSignInStore,
  type OrgSignInSwitch,
  type SignInRelayAnswer,
} from '../ports/engine-signins';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { ModelAccessService } from './model-access-service';

/** How long Node waits for the worker holding a sign-in. A code waits for Anthropic's exchange and the status. */
export const DESCRIBE_TIMEOUT_MS = 5_000;
export const CODE_TIMEOUT_MS = 90_000;

export interface EngineSignInServiceOptions {
  store: EngineSignInStore;
  relay?: EngineSignInRelay;
  /**
   * The org's consumer-plan choice (A1): a code carries whether the org admits only work plans. And (SIGNGAP)
   * whether a live worker carries a sign-in at all: without one the switches are shown unavailable and turning one
   * on is refused (ENGINE_SIGNIN_UNAVAILABLE).
   */
  access: Pick<ModelAccessService, 'policy' | 'signInsCarried'>;
  /** Whether the org lets its people use their own Claude accounts; absent: it does. */
  orgSwitch?: OrgSignInSwitch;
  /** Every change is one audited control-plane write (CPD-1). */
  transaction: ControlPlaneTransaction;
  logger?: HarnessLogger;
  now?: () => number;
  /** SIGNIN-5: the deployment's idle and maximum lifetime of a kept sign-in; unset, read from the environment. */
  keep?: SignInKeep;
}

/** What the person and the admin read when this server cannot carry a sign-in (SIGNGAP). */
const UNAVAILABLE_MESSAGE =
  'Personal Claude accounts need the sign-in add-on on this server; see the install guide';

type Caller = { orgId: string; userId: string };

const iso = (ms: number | undefined): string | undefined =>
  ms === undefined ? undefined : new Date(ms).toISOString();

/**
 * A person's own Claude account in Claude Code (SIGNIN): their switch, the
 * sign-in their browser completes through the worker that
 * holds it, and ending it. Stores only Claude Code's report (email, plan),
 * never a token or a code; logs and audits ids only.
 */
export class EngineSignInService implements EngineSignInEraser {
  private readonly now: () => number;
  private readonly keep: SignInKeep;
  private readonly orgSwitch: OrgSignInSwitch;

  constructor(private readonly options: EngineSignInServiceOptions) {
    this.now = options.now ?? Date.now;
    this.keep = options.keep ?? signInKeepFromEnv();
    this.orgSwitch = options.orgSwitch ?? everyOrgAllowsSignIns;
  }

  async view(orgId: string, userId: string): Promise<EngineSignInView> {
    const [found, allowed, consumerPlans, carried] = await Promise.all([
      this.read(orgId, userId),
      this.orgAllows(orgId),
      this.consumerPlans(orgId),
      this.options.access.signInsCarried(),
    ]);
    const { stored, serviceAccount } = found;
    const reason = !carried
      ? ('engine_signin_unavailable' as const)
      : !allowed
        ? ('engine_signin_off' as const)
        : serviceAccount
          ? ('account_requires_interactive' as const)
          : undefined;
    const claude: EngineSignInView['claude'] = {
      state:
        reason !== undefined
          ? 'not_allowed'
          : stored.enabled && stored.email !== undefined
            ? 'connected'
            : stored.disconnectedAt !== undefined && !stored.enabled
              ? 'disconnected'
              : 'available',
      enabled: reason === undefined && stored.enabled,
      consumerPlans,
      keep: { ...this.keep },
    };
    if (reason !== undefined) claude.reason = reason;
    if (stored.email !== undefined) claude.email = stored.email;
    if (stored.plan !== undefined) {
      claude.plan = stored.plan;
      claude.workPlan = WORK_PLANS.has(stored.plan);
    }
    const signedInAt = iso(stored.signedInAt);
    if (signedInAt !== undefined) claude.signedInAt = signedInAt;
    const disconnectedAt = iso(stored.disconnectedAt);
    if (disconnectedAt !== undefined) claude.disconnectedAt = disconnectedAt;
    return { claude, chatgpt: { state: 'waiting_vendor' } };
  }

  /** Whether the person's turn may pin their plan: their switch on, the org's on, a person (not a service account). */
  async usable(
    orgId: string,
    userId: string,
  ): Promise<{ enabled: boolean; allowed: boolean }> {
    const [found, allowed] = await Promise.all([
      this.options.store.read(orgId, userId),
      this.orgSwitch.allows(orgId),
    ]);
    return {
      enabled: found !== null && found.stored.enabled,
      allowed: allowed && found !== null && !found.serviceAccount,
    };
  }

  /** The person's switch: use their Claude account for their own tasks. Off ends every live sign-in. */
  async setEnabled(
    orgId: string,
    userId: string,
    enabled: boolean,
  ): Promise<EngineSignInView> {
    const { stored, serviceAccount } = await this.read(orgId, userId);
    if (enabled) {
      await this.assertCarried();
      if (!(await this.orgAllows(orgId))) {
        throw new HarnessModelAccessRefusedError(
          'engine_signin_off',
          'Your organization has not allowed personal Claude accounts',
        );
      }
      if (serviceAccount) {
        throw new HarnessModelAccessRefusedError(
          'account_requires_interactive',
          'A service account cannot use a personal Claude account',
        );
      }
    }
    if (!enabled && stored.enabled) {
      return this.disconnect(orgId, userId, userId, 'user');
    }
    const next: StoredEngineSignIn = { ...stored, enabled };
    await this.audited(
      orgId,
      userId,
      userScope(userId),
      `user/${userId}`,
      ['enabled'],
      {
        write: () => this.options.store.write(orgId, userId, next),
        before: stored.enabled,
        after: enabled,
      },
    );
    this.log('engine_account.enabled', { orgId, userId, enabled });
    return this.view(orgId, userId);
  }

  /**
   * Ends the person's sign-in everywhere: every worker holding one signs it
   * out at Anthropic and wipes the sandbox; the record keeps only when.
   */
  async disconnect(
    orgId: string,
    userId: string,
    actorId: string,
    by: SignOutBy,
  ): Promise<EngineSignInView> {
    const { stored } = await this.read(orgId, userId);
    const next: StoredEngineSignIn = {
      enabled: false,
      disconnectedAt: this.now(),
    };
    await this.audited(
      orgId,
      actorId,
      userScope(userId),
      `user/${userId}`,
      ['enabled', 'lastSignIn'],
      {
        write: () => this.options.store.write(orgId, userId, next),
        before: {
          enabled: stored.enabled,
          signedIn: stored.signedInAt ?? null,
        },
        after: { enabled: false, signedIn: null },
      },
    );
    await this.revoke({ orgId, userId, reason: by });
    this.log('engine_account.signed_out', { orgId, userId, by });
    return this.view(orgId, userId);
  }

  /**
   * An org rule a kept sign-in was checked against changed (the org's switch,
   * consumer plans): every sandbox kept between turns ends now, and the next
   * sign-in is checked against the new rule. `event` names the change in the
   * log, as its audit readers know it; null when the caller logs its own.
   */
  async orgRulesChanged(
    orgId: string,
    event: string | null = 'engine_account.org_rules_changed',
  ): Promise<void> {
    await this.revoke({ orgId, reason: 'policy' });
    if (event !== null) this.log(event, { orgId });
  }

  /** Turning a sign-in on where no worker can carry it is refused; turning one off never is. */
  private async assertCarried(): Promise<void> {
    if (!(await this.options.access.signInsCarried())) {
      throw new HarnessModelAccessRefusedError(
        'engine_signin_unavailable',
        UNAVAILABLE_MESSAGE,
      );
    }
  }

  /** The owner's open sign-in: Anthropic's link while it waits. Not found for anyone else. */
  async describe(
    caller: Caller,
    attemptId: string,
  ): Promise<SignInAttemptView> {
    const answer = await this.ask(
      caller,
      attemptId,
      { op: 'describe' },
      DESCRIBE_TIMEOUT_MS,
    );
    const view: SignInAttemptView = {
      attemptId,
      runId: typeof answer['runId'] === 'string' ? answer['runId'] : '',
      sessionId:
        typeof answer['sessionId'] === 'string' ? answer['sessionId'] : '',
      state:
        (answer['state'] as SignInAttemptView['state'] | undefined) ??
        'waiting',
      expiresAt: new Date(Number(answer['expiresAt'] ?? 0)).toISOString(),
    };
    if (typeof answer['link'] === 'string') view.link = answer['link'];
    return view;
  }

  /**
   * The pasted code goes straight to Claude Code's prompt in the owner's
   * sandbox; it is never stored or logged. On success the account's email and
   * plan, as Claude Code reports them, are kept for the owner's page.
   */
  async submitCode(
    caller: Caller,
    attemptId: string,
    code: string,
  ): Promise<SignInCodeResult> {
    if (!(await this.orgAllows(caller.orgId))) {
      throw new HarnessModelAccessRefusedError(
        'engine_signin_off',
        'Your organization has not allowed personal Claude accounts',
      );
    }
    const workPlansOnly = !(await this.consumerPlans(caller.orgId));
    const answer = await this.ask(
      caller,
      attemptId,
      { op: 'code', code, workPlansOnly },
      CODE_TIMEOUT_MS,
    );
    const result: SignInCodeResult = {
      state: answer['state'] === 'signed_in' ? 'signed_in' : 'failed',
      retry: answer['retry'] === true,
    };
    if (typeof answer['reasonCode'] === 'string')
      result.reasonCode = answer['reasonCode'];
    const email = answer['email'];
    const plan = answer['plan'];
    if (
      result.state === 'signed_in' &&
      typeof email === 'string' &&
      typeof plan === 'string'
    ) {
      await this.signedIn(caller, email, plan);
      result.email = email;
      result.plan = plan;
    }
    this.log(
      result.state === 'signed_in'
        ? 'engine_account.signed_in'
        : 'engine_account.signin_failed',
      {
        orgId: caller.orgId,
        userId: caller.userId,
        attemptId,
        ...(result.reasonCode !== undefined
          ? { reasonCode: result.reasonCode }
          : {}),
        ...(plan !== undefined && typeof plan === 'string' ? { plan } : {}),
      },
    );
    return result;
  }

  async cancel(caller: Caller, attemptId: string): Promise<void> {
    await this.ask(caller, attemptId, { op: 'cancel' }, DESCRIBE_TIMEOUT_MS);
    this.log('engine_account.signin_cancelled', {
      orgId: caller.orgId,
      userId: caller.userId,
      attemptId,
    });
  }

  /** A conversation gained a participant: a sign-in in it ends (ARCH §4.9). */
  async sessionShared(orgId: string, sessionId: string): Promise<void> {
    await this.revoke({ orgId, sessionId, reason: 'shared' });
  }

  /** The owner's "End now" on `/sandbox` (SLASH3 SB1): the conversation's kept sign-in sandbox ends now. */
  async endSession(orgId: string, sessionId: string): Promise<void> {
    await this.revoke({ orgId, sessionId, reason: 'user' });
  }

  async eraseUser(orgId: string, userId: string): Promise<void> {
    await engineSignInEraser(this.options.store, this.options.relay).eraseUser(
      orgId,
      userId,
    );
  }

  async eraseOrg(orgId: string): Promise<void> {
    await engineSignInEraser(this.options.store, this.options.relay).eraseOrg(
      orgId,
    );
  }

  private async signedIn(
    caller: Caller,
    email: string,
    plan: string,
  ): Promise<void> {
    const { stored } = await this.read(caller.orgId, caller.userId);
    const next: StoredEngineSignIn = {
      enabled: stored.enabled,
      email,
      plan,
      signedInAt: this.now(),
    };
    await this.audited(
      caller.orgId,
      caller.userId,
      userScope(caller.userId),
      `user/${caller.userId}`,
      ['lastSignIn'],
      {
        write: () =>
          this.options.store.write(caller.orgId, caller.userId, next),
        // The digest covers the plan kind and the time only: the audit never holds the email.
        before: { plan: stored.plan ?? null, at: stored.signedInAt ?? null },
        after: { plan, at: next.signedInAt ?? null },
      },
    );
  }

  private async ask(
    caller: Caller,
    attemptId: string,
    request: Parameters<EngineSignInRelay['ask']>[2],
    timeoutMs: number,
  ): Promise<SignInRelayAnswer> {
    const relay = this.options.relay;
    if (relay === undefined) {
      throw new HarnessUnavailableError(
        'Engine sign-in is not available on this deployment',
      );
    }
    const answer = await relay.ask(attemptId, caller, request, timeoutMs);
    // Anyone but the owner, an answer that names no owner, and an attempt no worker holds, are the same: not found.
    // The worker's answer is parsed JSON, so `ok` is checked as it arrived.
    const ok: unknown = answer?.ok;
    if (answer === null || ok !== true || answer['ownerId'] !== caller.userId) {
      throw new HarnessNotFoundError('No such sign-in');
    }
    return answer;
  }

  private async revoke(
    revocation: Parameters<EngineSignInRelay['revoke']>[0],
  ): Promise<void> {
    try {
      await this.options.relay?.revoke(revocation);
    } catch (error) {
      // The next turn's admission refuses the plan anyway; a sandbox in use ends at its turn's end.
      this.options.logger?.warn(
        'engine_account.revoke_failed',
        harnessLogMeta('engine_account.revoke_failed', {
          orgId: revocation.orgId,
          reason: revocation.reason,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  private async read(
    orgId: string,
    userId: string,
  ): Promise<{ stored: StoredEngineSignIn; serviceAccount: boolean }> {
    let found;
    try {
      found = await this.options.store.read(orgId, userId);
    } catch {
      throw new HarnessUnavailableError(
        'The engine sign-in settings could not be read',
      );
    }
    if (found === null) throw new HarnessNotFoundError('User not found');
    return found;
  }

  private async orgAllows(orgId: string): Promise<boolean> {
    try {
      return await this.orgSwitch.allows(orgId);
    } catch {
      throw new HarnessUnavailableError(
        'The organization settings could not be read',
      );
    }
  }

  private async consumerPlans(orgId: string): Promise<boolean> {
    return (await this.options.access.policy(orgId)).consumerPlans;
  }

  private async audited(
    orgId: string,
    actorId: string,
    scope: string,
    id: string,
    changed: string[],
    change: { write: () => Promise<void>; before: unknown; after: unknown },
  ): Promise<void> {
    await this.options.transaction.run(
      { orgId, actor: actorRefOfUser(actorId) },
      (tx) => {
        tx.external(change.write);
        return Promise.resolve({
          result: null,
          change: {
            scope,
            object: { type: 'engine-signin', id },
            revisionFrom: null,
            revisionTo: null,
            changeDigest: canonicalDigest({
              before: change.before,
              after: change.after,
            }),
            changed,
          },
        });
      },
    );
  }

  private log(
    event: string,
    fields: Record<string, string | boolean | undefined>,
  ): void {
    this.options.logger?.info(event, harnessLogMeta(event, fields));
  }
}

/**
 * What user and org deletion call (C-14): the record goes, and every worker
 * holding a sign-in of the person (or the org) signs it out. A relay that
 * cannot be reached does not block the deletion: the next turn's admission
 * finds no record, and a sandbox in use ends with its turn.
 */
export function engineSignInEraser(
  store: Pick<EngineSignInStore, 'eraseUser' | 'eraseOrg'>,
  relay?: Pick<EngineSignInRelay, 'revoke'>,
): EngineSignInEraser {
  const revoke = async (
    revocation: Parameters<EngineSignInRelay['revoke']>[0],
  ): Promise<void> => {
    try {
      await relay?.revoke(revocation);
    } catch {
      // See above: best effort.
    }
  };
  return {
    async eraseUser(orgId: string, userId: string): Promise<void> {
      await store.eraseUser(orgId, userId);
      await revoke({ orgId, userId, reason: 'erasure' });
    },
    async eraseOrg(orgId: string): Promise<void> {
      await store.eraseOrg(orgId);
      await revoke({ orgId, reason: 'erasure' });
    },
  };
}
