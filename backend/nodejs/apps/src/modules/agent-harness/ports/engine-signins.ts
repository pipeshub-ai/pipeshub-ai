import type { SignOutBy, StoredEngineSignIn } from '../domain/engine-signin';

/** The person's sign-in record and the org's switch (SIGNIN): the user and org documents. Throws when unreadable. */
export interface EngineSignInStore {
  /** null: no such person in the org. `serviceAccount`: such accounts never use a plan. */
  read(
    orgId: string,
    userId: string,
  ): Promise<{ stored: StoredEngineSignIn; serviceAccount: boolean } | null>;
  write(orgId: string, userId: string, next: StoredEngineSignIn): Promise<void>;
  /** SIGNIN-5: the org's people whose switch is on and who signed in at or after `since` (epoch ms). */
  signedInSince(orgId: string, since: number): Promise<string[]>;
  /** The org's switch; absent: off. */
  orgAllows(orgId: string): Promise<boolean>;
  setOrgAllows(orgId: string, allowed: boolean): Promise<void>;
  /** Removes the person's record (erasure: before the user is marked deleted). */
  eraseUser(orgId: string, userId: string): Promise<void>;
  /** Removes every record of the org and its switch (erasure). */
  eraseOrg(orgId: string): Promise<void>;
}

/** Whether an org lets its people use their own Claude accounts (SIGNIN). Throws when it cannot be read. */
export interface OrgSignInSwitch {
  allows(orgId: string): Promise<boolean>;
}

/** The default: every org does; each person still turns their own account on. */
export const everyOrgAllowsSignIns: OrgSignInSwitch = Object.freeze({
  allows: () => Promise.resolve(true),
});

/** C-14: user and org deletion reach SIGNIN (records removed, live sign-ins ended). */
export const ENGINE_SIGNIN_ERASER = 'AgentHarnessEngineSignInEraser';

export interface EngineSignInEraser {
  eraseUser(orgId: string, userId: string): Promise<void>;
  eraseOrg(orgId: string): Promise<void>;
}

/** A request the owner's browser sends to the worker holding their sign-in. */
export type SignInRelayRequest =
  | { op: 'describe' }
  | { op: 'cancel' }
  | { op: 'code'; code: string; workPlansOnly: boolean };

/** The worker's answer, as it sent it; `ok: false` names why. */
export type SignInRelayAnswer = Record<string, unknown> & { ok: boolean };

/** Who and what a revocation reaches: every sign-in of the user, or of the session, or of the whole org. */
export interface SignInRevocation {
  orgId: string;
  userId?: string;
  sessionId?: string;
  reason: SignOutBy;
}

/**
 * The transient channel to the worker holding a sign-in (Redis pub/sub): no
 * message is stored, so the link, the code and the reply are never kept.
 */
export interface EngineSignInRelay {
  /** The answer, or null when no worker holds the attempt (it ended, or never was). */
  ask(
    attemptId: string,
    caller: { orgId: string; userId: string },
    request: SignInRelayRequest,
    timeoutMs: number,
  ): Promise<SignInRelayAnswer | null>;
  /** Every worker holding a matching sign-in ends it; best effort, the next turn's admission checks again. */
  revoke(revocation: SignInRevocation): Promise<void>;
}
