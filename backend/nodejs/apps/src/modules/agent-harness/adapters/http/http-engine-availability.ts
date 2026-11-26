import { EngineAvailability } from '../../ports/engine-availability';

/** How long one admission waits for the broker before it admits without the answer. */
export const AVAILABILITY_TIMEOUT_MS = 2_000;
/** An answer serves the admissions after it for this long: images come and go on an admin's schedule. */
export const AVAILABILITY_TTL_MS = 30_000;
/** `BrokerHealth.detail` key the broker reports the engines in (harness_core.sandbox.types). */
export const ENGINES_UNAVAILABLE = 'enginesUnavailable';
/** `BrokerHealth.detail` key: the unavailable engines whose profile no trusted key signed (harness_core.sandbox.types `ENGINES_UNTRUSTED`). */
export const ENGINES_UNTRUSTED = 'enginesUntrusted';
/** `BrokerHealth.detail` key: `missing` when engines have no sandbox here (harness_core.sandbox.types `ENGINE_SANDBOX`). */
export const ENGINE_SANDBOX = 'engineSandbox';
export const ENGINE_SANDBOX_MISSING = 'missing';
/** `BrokerHealth.detail` key: `kind@version=sha256:<hex>` per engine image (harness_core.sandbox.types `ENGINE_IMAGES`). */
export const ENGINE_IMAGES = 'engineImages';
/** `BrokerHealth.detail` key, only while gVisor is offered: `on` or `off` (harness_core.sandbox.types `GVISOR_SYSCALL_FILTER`). */
export const GVISOR_SYSCALL_FILTER = 'gvisorSyscallFilter';
const IMAGE_ENTRY = /^([^=,\s]+)=(sha256:[0-9a-f]{64})$/;

/** The broker's `engineImages` field: a malformed entry is dropped, never pinned (a bad pin fails the run). */
export function parseEngineImages(
  raw: unknown,
): ReadonlyMap<string, string> | null {
  if (typeof raw !== 'string') return null;
  const images = new Map<string, string>();
  for (const entry of raw.split(',')) {
    const match = IMAGE_ENTRY.exec(entry);
    if (match !== null) images.set(match[1] as string, match[2] as string);
  }
  return images;
}

/**
 * `EngineAvailability` over the broker's `GET {endpoint}/v1/health`, with the
 * deployment's worker credential, as every broker client authenticates (P5.1).
 * The endpoint and credential are read on every call; an answer is cached.
 */
export class HttpEngineAvailability implements EngineAvailability {
  private cached: { at: number; value: BrokerAnswer } | null = null;

  constructor(
    private readonly endpoint: () => Promise<string | null>,
    private readonly credential: () => Promise<string | null>,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly options: { ttlMs?: number; now?: () => number } = {},
  ) {}

  async unavailable(): Promise<ReadonlySet<string> | null> {
    return (await this.answer()).unavailable;
  }

  async untrusted(): Promise<ReadonlySet<string> | null> {
    return (await this.answer()).untrusted;
  }

  async modes(): Promise<ReadonlySet<string> | null> {
    return (await this.answer()).modes;
  }

  async sandboxMissing(): Promise<boolean> {
    return (await this.answer()).sandboxMissing;
  }

  async imageDigests(): Promise<ReadonlyMap<string, string> | null> {
    return (await this.answer()).images;
  }

  async gvisorSyscallFilter(): Promise<boolean | null> {
    return (await this.answer()).gvisorSyscallFilter;
  }

  private async answer(): Promise<BrokerAnswer> {
    const now = (this.options.now ?? Date.now)();
    const ttl = this.options.ttlMs ?? AVAILABILITY_TTL_MS;
    if (this.cached !== null && now - this.cached.at < ttl) {
      return this.cached.value;
    }
    const value = await this.ask();
    this.cached = { at: now, value };
    return value;
  }

  private async ask(): Promise<BrokerAnswer> {
    const [endpoint, credential] = await Promise.all([
      this.endpoint(),
      this.credential(),
    ]);
    if (endpoint === null) {
      return {
        unavailable: null,
        untrusted: null,
        modes: null,
        sandboxMissing: false,
        images: null,
        gvisorSyscallFilter: null,
      };
    }
    if (credential === null)
      throw new Error('worker credential not provisioned');
    const response = await this.fetchImpl(
      `${endpoint.replace(/\/+$/, '')}/v1/health`,
      {
        method: 'GET',
        headers: { Authorization: `Bearer ${credential}` },
        signal: AbortSignal.timeout(AVAILABILITY_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      throw new Error(`sandbox broker health: HTTP ${response.status}`);
    }
    const body = (await response.json()) as unknown;
    if (body === null || typeof body !== 'object' || Array.isArray(body)) {
      throw new Error('malformed broker health');
    }
    const record = body as Record<string, unknown>;
    const detail = record['detail'];
    const fields =
      detail !== null && typeof detail === 'object'
        ? (detail as Record<string, unknown>)
        : {};
    const modes = record['modes'];
    const filter = fields[GVISOR_SYSCALL_FILTER];
    return {
      unavailable: engineSet(fields[ENGINES_UNAVAILABLE]),
      untrusted: engineSet(fields[ENGINES_UNTRUSTED]),
      modes: Array.isArray(modes)
        ? new Set(
            modes.filter((mode): mode is string => typeof mode === 'string'),
          )
        : null,
      sandboxMissing: fields[ENGINE_SANDBOX] === ENGINE_SANDBOX_MISSING,
      images: parseEngineImages(fields[ENGINE_IMAGES]),
      gvisorSyscallFilter:
        filter === 'on' ? true : filter === 'off' ? false : null,
    };
  }
}

/** A comma-separated engine list of the broker's health; null when it does not say. */
function engineSet(raw: unknown): ReadonlySet<string> | null {
  return typeof raw === 'string'
    ? new Set(raw.split(',').filter((engine) => engine.length > 0))
    : null;
}

/** One broker health answer: unrunnable engines (and those of them with an untrusted profile), offered runtimes (null when not said), whether engines have no sandbox, their image digests, and whether gVisor filters its guests' syscalls (null when not said). */
interface BrokerAnswer {
  unavailable: ReadonlySet<string> | null;
  untrusted: ReadonlySet<string> | null;
  modes: ReadonlySet<string> | null;
  sandboxMissing: boolean;
  images: ReadonlyMap<string, string> | null;
  gvisorSyscallFilter: boolean | null;
}
