import { publicAuthClient } from '@/lib/api/public-auth-client';

/** Response body from GET /api/v1/org/exists — strict `{ exists: boolean }`. */
export interface OrgExistsResponse {
  exists: boolean;
}

let inFlight: Promise<OrgExistsResponse> | null = null;

/** Where unauthenticated users should land: org present → login, first install → sign-up. */
export function authEntryPath(orgExists: boolean): '/login' | '/sign-up' {
  return orgExists ? '/login' : '/sign-up';
}

/**
 * Returns whether at least one organization exists (GET /api/v1/org/exists).
 * Reuses a single in-flight promise per page load (AuthGuard + login/sign-up gates).
 */
export function getOrgExists(): Promise<OrgExistsResponse> {
  if (!inFlight) {
    inFlight = publicAuthClient
      .get<OrgExistsResponse>('/api/v1/org/exists')
      .then((res) => res.data)
      .catch((err) => {
        inFlight = null;
        throw err;
      });
  }
  return inFlight;
}

/** Call after first org is created so the next check refetches { exists: true }. */
export function invalidateOrgExistsCache(): void {
  inFlight = null;
}
