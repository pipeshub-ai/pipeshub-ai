import {
  AttemptClaims,
  IssuedAttemptToken,
  WorkerCredentialSet,
} from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import { HarnessRun } from '../domain/execution';
import { HarnessCredentialRole } from '../domain/credential-roles';
import { AttemptTokenAuthority } from './attempt-token-authority';
import { WorkerCredentialAuthority } from './worker-credential-authority';

/** Every machine role but the worker's, each its own credential set (SEC-C-10). */
export type RoleCredentialAuthorities = Readonly<
  Record<Exclude<HarnessCredentialRole, 'worker'>, WorkerCredentialAuthority>
>;

export interface RunReader {
  get(orgId: string, runId: string): Promise<HarnessRun | null>;
}

export interface AttemptReference {
  attemptId: string;
  fence: number;
}

/**
 * Who may act on the journal (REPORT 6.1.8): the worker credential admits a
 * claim; the attempt token Node returns admits everything after it. Both are
 * authorization only; the store's fenced predicates stay authoritative.
 */
export class HarnessAuthority {
  constructor(
    private readonly workers: WorkerCredentialAuthority,
    private readonly tokens: AttemptTokenAuthority,
    private readonly runs: RunReader,
    private readonly others: RoleCredentialAuthorities | null = null,
  ) {}

  async provision(rotateId: string | null): Promise<void> {
    await this.provisionWorkerCredential(rotateId);
    await this.tokens.provision(rotateId);
  }

  /**
   * Every role's credential (the sandbox broker and egress proxy verify them even where durable runs are off).
   * An operator rotation id rotates every role at once.
   */
  async provisionWorkerCredential(rotateId: string | null): Promise<void> {
    await this.workers.provision(rotateId);
    for (const role of this.roleAuthorities()) await role.provision(rotateId);
  }

  async rotateWorkerCredential(): Promise<void> {
    await this.workers.rotate();
    for (const role of this.roleAuthorities()) await role.rotate();
  }

  /** The credential a holder of `role` presents; null until provisioned. */
  credentialFor(role: HarnessCredentialRole): Promise<string | null> {
    return this.authorityOf(role)?.current() ?? Promise.resolve(null);
  }

  /** The role's current set, for sealing into a component's document (ADR-KEY-02); null until provisioned. */
  credentialSetFor(
    role: HarnessCredentialRole,
  ): Promise<WorkerCredentialSet | null> {
    return this.authorityOf(role)?.currentSet() ?? Promise.resolve(null);
  }

  /**
   * Admits a credential of one of `roles`, and answers which. A credential of any other role is refused exactly as a
   * wrong one is: the caller learns nothing about which roles exist.
   */
  async verifyRole(
    roles: readonly HarnessCredentialRole[],
    credential: string | undefined,
  ): Promise<HarnessCredentialRole> {
    let refusal: unknown = null;
    for (const role of roles) {
      const authority = this.authorityOf(role);
      if (authority === null) continue;
      try {
        await authority.verify(credential);
        return role;
      } catch (error) {
        refusal = error;
      }
    }
    throw refusal instanceof HarnessUnauthorizedError
      ? refusal
      : new HarnessUnauthorizedError(
          'credential',
          'Agent harness worker credential refused',
        );
  }

  private authorityOf(
    role: HarnessCredentialRole,
  ): WorkerCredentialAuthority | null {
    if (role === 'worker') return this.workers;
    return this.others?.[role] ?? null;
  }

  private roleAuthorities(): WorkerCredentialAuthority[] {
    return this.others === null ? [] : Object.values(this.others);
  }

  rotateKeys(): Promise<void> {
    return this.tokens.rotate();
  }

  /** Node authenticates to an inline claimer with the same credential it verifies. */
  workerCredential(): Promise<string | null> {
    return this.workers.current();
  }

  verifyWorker(credential: string | undefined): Promise<void> {
    return this.workers.verify(credential);
  }

  /** For a run the store just claimed or renewed for its current attempt. */
  issueFor(
    run: HarnessRun,
    leaseDurationMs: number,
  ): Promise<IssuedAttemptToken> {
    if (run.attemptId === null || run.workerId === null) {
      throw new Error('An attempt token needs a claimed run');
    }
    return this.tokens.issue(
      {
        orgId: run.orgId,
        runId: run.runId,
        attemptId: run.attemptId,
        fence: run.fence,
        workerId: run.workerId,
      },
      leaseDurationMs,
    );
  }

  async verifyAttemptToken(
    token: string | undefined,
    runId: string,
  ): Promise<AttemptClaims> {
    const claims = await this.tokens.verify(token);
    if (claims.runId !== runId) {
      throw new HarnessUnauthorizedError(
        'claims',
        'Attempt token is for another run',
      );
    }
    return claims;
  }

  /**
   * The request must name the token's attempt, and no later claim may have
   * superseded it (A-3). A terminal run of the same attempt passes, so a
   * retried commit still replays its receipt. Answers the run as it read it
   * (null: not found), so the request it admits does not read it again.
   */
  async authorizeAttempt(
    claims: AttemptClaims,
    request: AttemptReference,
  ): Promise<HarnessRun | null> {
    if (
      claims.attemptId !== request.attemptId ||
      claims.fence !== request.fence
    ) {
      throw new HarnessUnauthorizedError(
        'claims',
        'Attempt token is for another attempt',
      );
    }
    const run = await this.runs.get(claims.orgId, claims.runId);
    if (
      run !== null &&
      (run.fence > claims.fence ||
        (run.attemptId !== null && run.attemptId !== claims.attemptId))
    ) {
      throw new HarnessUnauthorizedError(
        'superseded',
        'Attempt token was superseded by a later claim',
      );
    }
    return run;
  }
}
