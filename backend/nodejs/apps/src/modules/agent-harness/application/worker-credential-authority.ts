import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import {
  acceptedWorkerCredentials,
  AUTHORITY_CACHE_TTL_MS,
  AUTHORITY_MIN_RELOAD_INTERVAL_MS,
  AUTHORITY_STALE_MAX_MS,
  AuthorityClock,
  newWorkerCredentialSet,
  rotateWorkerCredentialSet,
  systemAuthorityClock,
  WorkerCredentialSet,
} from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import { ConfigDocumentStore } from '../ports/config-document-store';
import { CachedConfigDocument, updateConfigDocument } from './config-documents';
import { HarnessLogger } from './harness-log';

const WORKER_CREDENTIAL_BYTES = 32;
// A recognizable prefix lets secret scanners flag a leaked credential.
const WORKER_CREDENTIAL_PREFIX = 'phw_';

export const generateWorkerCredential = (): string =>
  `${WORKER_CREDENTIAL_PREFIX}${randomBytes(WORKER_CREDENTIAL_BYTES).toString('base64url')}`;

/** Equal-length digests, so neither content nor length leaks through timing. */
export function constantTimeEquals(left: string, right: string): boolean {
  const digest = (value: string): Buffer =>
    createHash('sha256').update(value, 'utf8').digest();
  return timingSafeEqual(digest(left), digest(right));
}

/**
 * The per-deployment credential a harness worker presents to claim a run.
 * It is not derived from, or stored with, the scoped-JWT secret, so a service
 * that can mint scoped tokens still cannot claim (A-1).
 */
export class WorkerCredentialAuthority {
  private readonly credentials: CachedConfigDocument<WorkerCredentialSet>;

  constructor(
    private readonly store: ConfigDocumentStore<WorkerCredentialSet>,
    private readonly clock: AuthorityClock = systemAuthorityClock,
    private readonly generate: () => string = generateWorkerCredential,
    logger?: HarnessLogger,
  ) {
    this.credentials = new CachedConfigDocument(
      store,
      () => clock.now(),
      AUTHORITY_CACHE_TTL_MS,
      AUTHORITY_MIN_RELOAD_INTERVAL_MS,
      { name: 'worker_credential', maxMs: AUTHORITY_STALE_MAX_MS, logger },
    );
  }

  /** Creates the credential once; applies a new operator rotation id once. */
  async provision(rotateId: string | null): Promise<void> {
    const set = await updateConfigDocument(this.store, (current) => {
      if (current === null) {
        return newWorkerCredentialSet(this.generate(), rotateId);
      }
      if (rotateId !== null && current.rotateId !== rotateId) {
        return rotateWorkerCredentialSet(
          current,
          this.generate(),
          this.clock.now(),
          rotateId,
        );
      }
      return null;
    });
    this.credentials.remember(set);
  }

  async rotate(): Promise<void> {
    const set = await updateConfigDocument(this.store, (current) =>
      current === null
        ? newWorkerCredentialSet(this.generate(), null)
        : rotateWorkerCredentialSet(current, this.generate(), this.clock.now()),
    );
    this.credentials.remember(set);
  }

  /** The credential to present to a claimer (the inline claim hint); null until provisioned. */
  async current(): Promise<string | null> {
    return (await this.credentials.get())?.current ?? null;
  }

  /** The whole set (current, previous and its grace), for sealing into a component's document; null until provisioned. */
  async currentSet(): Promise<WorkerCredentialSet | null> {
    return this.credentials.get();
  }

  /** Never provisions: an unprovisioned deployment refuses every claim. */
  async verify(presented: string | undefined): Promise<void> {
    if (presented === undefined || presented.length === 0) {
      throw new HarnessUnauthorizedError(
        'credential',
        'Agent harness worker credential required',
      );
    }
    if (this.accepts(await this.credentials.get(), presented)) return;
    // Another replica may have rotated since this one cached the set.
    const refreshed = await this.credentials.refresh();
    if (refreshed !== null && this.accepts(refreshed.value, presented)) return;
    throw new HarnessUnauthorizedError(
      'credential',
      'Agent harness worker credential refused',
    );
  }

  private accepts(set: WorkerCredentialSet | null, presented: string): boolean {
    if (set === null) return false;
    return acceptedWorkerCredentials(set, this.clock.now())
      .map((accepted) => constantTimeEquals(accepted, presented))
      .some(Boolean);
  }
}
