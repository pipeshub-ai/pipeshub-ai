import { SandboxFolderEraser, SandboxReaper } from '../../ports/sandbox-reaper';
import { SandboxOrgEraser } from '../../ports/principal-erasure';

/** How long one reap may take before the sweeper moves on; the next sweep of the run asks again. */
export const REAP_TIMEOUT_MS = 5_000;
/** Removing a session's folder runs a helper container: longer than a reap. */
export const ERASE_TIMEOUT_MS = 60_000;

/**
 * `SandboxReaper` over the broker's HTTP API: `POST {endpoint}/v1/reap`
 * with the deployment's worker credential, as every broker client
 * authenticates (P5.1). The endpoint and credential are read on every call,
 * so a rotation or a moved broker needs no restart.
 */
export class HttpSandboxReaper
  implements SandboxReaper, SandboxFolderEraser, SandboxOrgEraser
{
  constructor(
    private readonly endpoint: () => Promise<string | null>,
    private readonly credential: () => Promise<string | null>,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly timeoutMs: number = REAP_TIMEOUT_MS,
  ) {}

  async reap(labels: Readonly<Record<string, string>>): Promise<number> {
    const [endpoint, credential] = await Promise.all([
      this.endpoint(),
      this.credential(),
    ]);
    if (endpoint === null || credential === null) {
      throw new Error('sandbox broker not configured');
    }
    const response = await this.fetchImpl(
      `${endpoint.replace(/\/+$/, '')}/v1/reap`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${credential}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ labels, older_than_s: 0 }),
        signal: AbortSignal.timeout(this.timeoutMs),
      },
    );
    if (!response.ok) {
      throw new Error(
        `sandbox broker refused the reap: HTTP ${response.status}`,
      );
    }
    const body = (await response.json()) as { reaped?: unknown };
    if (typeof body.reaped !== 'number') {
      throw new Error('malformed reap response');
    }
    return body.reaped;
  }

  /** No broker configured: this deployment never kept a sandbox folder, so there is none to erase. */
  async eraseSession(orgId: string, sessionId: string): Promise<number> {
    const [endpoint, credential] = await Promise.all([
      this.endpoint(),
      this.credential(),
    ]);
    if (endpoint === null) return 0;
    if (credential === null) {
      throw new Error('worker credential not provisioned');
    }
    const response = await this.fetchImpl(
      `${endpoint.replace(/\/+$/, '')}/v1/erase-session`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${credential}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ orgId, sessionId }),
        signal: AbortSignal.timeout(ERASE_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      throw new Error(
        `sandbox broker refused the erasure: HTTP ${response.status}`,
      );
    }
    const body = (await response.json()) as { erased?: unknown };
    if (typeof body.erased !== 'number') {
      throw new Error('malformed erase response');
    }
    return body.erased;
  }

  /**
   * Every sandbox and persistent folder of a deleted org (`/v1/erase-org`,
   * CLEANUP-G1): kept sign-ins are signed out first by the broker. No broker
   * configured: there is nothing of the org on one.
   */
  async eraseOrg(
    orgId: string,
  ): Promise<{ sandboxes: number; folders: number }> {
    const [endpoint, credential] = await Promise.all([
      this.endpoint(),
      this.credential(),
    ]);
    if (endpoint === null) return { sandboxes: 0, folders: 0 };
    if (credential === null) {
      throw new Error('worker credential not provisioned');
    }
    const response = await this.fetchImpl(
      `${endpoint.replace(/\/+$/, '')}/v1/erase-org`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${credential}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ orgId }),
        signal: AbortSignal.timeout(ERASE_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      throw new Error(
        `sandbox broker refused the org erasure: HTTP ${response.status}`,
      );
    }
    const body = (await response.json()) as {
      sandboxes?: unknown;
      folders?: unknown;
    };
    if (
      typeof body.sandboxes !== 'number' ||
      typeof body.folders !== 'number'
    ) {
      throw new Error('malformed erase-org response');
    }
    return { sandboxes: body.sandboxes, folders: body.folders };
  }
}
