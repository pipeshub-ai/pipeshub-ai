import type { StoredEngineSignIn } from '../domain/engine-signin';
import { HarnessNotFoundError } from '../domain/errors';
import type {
  EngineSignInRelay,
  EngineSignInStore,
  SignInRelayAnswer,
  SignInRelayRequest,
  SignInRevocation,
} from '../ports/engine-signins';

/** SIGNIN's store in memory (tests, and deployments without Mongo users). */
export class InMemoryEngineSignIns implements EngineSignInStore {
  readonly users = new Map<
    string,
    { stored: StoredEngineSignIn; serviceAccount: boolean }
  >();
  readonly orgs = new Map<string, boolean>();

  addUser(orgId: string, userId: string, serviceAccount = false): void {
    this.users.set(`${orgId}/${userId}`, {
      stored: { enabled: false },
      serviceAccount,
    });
  }

  read(
    orgId: string,
    userId: string,
  ): Promise<{ stored: StoredEngineSignIn; serviceAccount: boolean } | null> {
    const found = this.users.get(`${orgId}/${userId}`);
    return Promise.resolve(
      found === undefined
        ? null
        : { stored: { ...found.stored }, serviceAccount: found.serviceAccount },
    );
  }

  signedInSince(orgId: string, since: number): Promise<string[]> {
    const found: string[] = [];
    for (const [key, { stored }] of this.users) {
      const [org, user] = key.split('/') as [string, string];
      if (org === orgId && stored.enabled && (stored.signedInAt ?? -1) >= since)
        found.push(user);
    }
    return Promise.resolve(found.sort());
  }

  write(
    orgId: string,
    userId: string,
    next: StoredEngineSignIn,
  ): Promise<void> {
    const found = this.users.get(`${orgId}/${userId}`);
    if (found === undefined)
      return Promise.reject(new HarnessNotFoundError('User not found'));
    found.stored = { ...next };
    return Promise.resolve();
  }

  orgAllows(orgId: string): Promise<boolean> {
    return Promise.resolve(this.orgs.get(orgId) === true);
  }

  setOrgAllows(orgId: string, allowed: boolean): Promise<void> {
    this.orgs.set(orgId, allowed);
    return Promise.resolve();
  }

  eraseUser(orgId: string, userId: string): Promise<void> {
    const found = this.users.get(`${orgId}/${userId}`);
    if (found !== undefined) found.stored = { enabled: false };
    return Promise.resolve();
  }

  eraseOrg(orgId: string): Promise<void> {
    for (const [key, found] of this.users) {
      if (key.startsWith(`${orgId}/`)) found.stored = { enabled: false };
    }
    this.orgs.delete(orgId);
    return Promise.resolve();
  }
}

/** A relay whose worker side a test plays: `answer` decides each request; every revocation is recorded. */
export class InMemorySignInRelay implements EngineSignInRelay {
  readonly asked: Array<{
    attemptId: string;
    caller: { orgId: string; userId: string };
    request: SignInRelayRequest;
  }> = [];
  readonly revoked: SignInRevocation[] = [];

  constructor(
    public answer: (
      attemptId: string,
      caller: { orgId: string; userId: string },
      request: SignInRelayRequest,
    ) => SignInRelayAnswer | null = () => null,
  ) {}

  ask(
    attemptId: string,
    caller: { orgId: string; userId: string },
    request: SignInRelayRequest,
  ): Promise<SignInRelayAnswer | null> {
    this.asked.push({ attemptId, caller, request });
    return Promise.resolve(this.answer(attemptId, caller, request));
  }

  revoke(revocation: SignInRevocation): Promise<void> {
    this.revoked.push(revocation);
    return Promise.resolve();
  }
}
