/**
 * Extension points of an organization's lifecycle: how it is deleted, and how
 * sign-in treats an organization that is not live. Supplied through
 * `orgLifecycleHook` and `scopedTokenGuard` in src/config.ts and installed once
 * at boot; this build supplies `null` and a guard that claims no token, so an
 * organization is deleted at once and sign-in reads live organizations only.
 */
import type { OrgDto } from '../repositories/org/types';

/** What the hooks may need from the app's configuration. */
export interface OrgLifecycleConfig {
  frontendUrl: string;
  scopedJwtSecret: string;
}

/** A delete whose typed name already matched the organization's. */
export interface OrgDeleteRequest {
  org: OrgDto;
  orgId: string;
  byUserId: string;
  config: OrgLifecycleConfig;
}

/** An answer that ends a sign-in after its credential was proven. */
export interface SignInRefusal {
  status: number;
  body: Record<string, unknown>;
}

export interface OrgLifecycleHook {
  /** Deletes the organization in place of the immediate delete; answers the response body. */
  deleteOrg(request: OrgDeleteRequest): Promise<Record<string, unknown>>;
  /** With no live organization: one whose sign-in methods the sign-in page still offers, or null. */
  signInPageOrg(): Promise<OrgDto | null>;
  /** The account's own organization when it is not live, for the sign-in code mail, or null. */
  notLiveOrgOf(orgId: string): Promise<OrgDto | null>;
  /** Once every credential is proven: an answer that refuses the sign-in, or null to complete it. */
  signInRefusal(
    userId: string,
    config: OrgLifecycleConfig,
  ): Promise<SignInRefusal | null>;
}

/** How the auth middleware refuses a token that is good for one narrow purpose only. */
export interface ScopedTokenRefusal {
  /** The token is checked genuine (signed with the user-action key, holding this scope) before it is refused. */
  scope: string;
  message: string;
  reasonCode: string;
}

/**
 * Whether a token is a sign-in limited to its own routes, which every other
 * route refuses with 403; read from the token unverified, only to choose how
 * it is verified. `false`: an ordinary token.
 */
export type ScopedTokenGuard = (token: string) => false | ScopedTokenRefusal;

export interface OrgLifecycle {
  orgLifecycleHook: OrgLifecycleHook | null;
  scopedTokenGuard: ScopedTokenGuard;
}

export const defaultScopedTokenGuard: ScopedTokenGuard = () => false;

export const noOrgLifecycleHook: OrgLifecycleHook | null = null;

export const DEFAULT_ORG_LIFECYCLE: OrgLifecycle = Object.freeze({
  orgLifecycleHook: null,
  scopedTokenGuard: defaultScopedTokenGuard,
});

let installed: OrgLifecycle = DEFAULT_ORG_LIFECYCLE;

/** Called once at boot with config.ts's values; tests install their own and put the defaults back. */
export function installOrgLifecycle(lifecycle: Partial<OrgLifecycle>): void {
  installed = Object.freeze({ ...DEFAULT_ORG_LIFECYCLE, ...lifecycle });
}

export function resetOrgLifecycle(): void {
  installed = DEFAULT_ORG_LIFECYCLE;
}

export function orgLifecycle(): OrgLifecycle {
  return installed;
}
