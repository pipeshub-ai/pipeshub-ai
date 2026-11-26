import { DeploymentCapabilities } from '../../domain/capabilities';
import { CapabilityProbe } from '../../ports/capability-probe';

/** How long one probe waits for the broker; a slow broker leaves isolation unknown until a retry answers (ADR-CAP-01). */
export const ISOLATION_PROBE_TIMEOUT_MS = 2_000;

/** The broker's runtime modes (`harness_core.sandbox.types.RuntimeMode`), strongest first. */
const ISOLATION_BY_MODE: ReadonlyArray<
  [string, DeploymentCapabilities['sandbox.isolation']]
> = [
  ['gvisor', 'container_gvisor'],
  ['docker', 'container'],
];

/**
 * `sandbox.isolation` (REPORT 11) from the sandbox broker's `GET /v1/health`
 * (P5.1), with the worker credential every broker client presents: the
 * strongest mode it offers. No broker configured proves nothing (`{}`: the
 * least value, `none`); a broker that cannot be asked throws, and the service
 * records isolation as unknown for the round and asks again on its backoff.
 */
export class HttpSandboxIsolationProbe implements CapabilityProbe {
  readonly name = 'sandbox-isolation';
  readonly provides = ['sandbox.isolation'] as const;

  constructor(
    private readonly endpoint: () => Promise<string | null>,
    private readonly credential: () => Promise<string | null>,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async probe(): Promise<Partial<DeploymentCapabilities>> {
    const [endpoint, credential] = await Promise.all([
      this.endpoint(),
      this.credential(),
    ]);
    if (endpoint === null) return {};
    if (credential === null)
      throw new Error('worker credential not provisioned');
    const response = await this.fetchImpl(
      `${endpoint.replace(/\/+$/, '')}/v1/health`,
      {
        method: 'GET',
        headers: { Authorization: `Bearer ${credential}` },
        signal: AbortSignal.timeout(ISOLATION_PROBE_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      throw new Error(`sandbox broker health: HTTP ${response.status}`);
    }
    const body = (await response.json()) as unknown;
    const modes =
      body !== null && typeof body === 'object' && !Array.isArray(body)
        ? (body as Record<string, unknown>)['modes']
        : undefined;
    if (!Array.isArray(modes)) throw new Error('malformed broker health');
    const ok = (body as Record<string, unknown>)['ok'] === true;
    const found = ISOLATION_BY_MODE.find(([mode]) => modes.includes(mode));
    return {
      'sandbox.isolation': ok && found !== undefined ? found[1] : 'none',
    };
  }
}
