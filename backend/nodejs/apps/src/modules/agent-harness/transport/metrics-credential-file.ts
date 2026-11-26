import { chown, mkdir, rename, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { detachedInterval } from '../../../libs/context/orgContext';

/**
 * C9.04: the deployment's own Prometheus (the optional `monitoring` profile) reads the merged metrics route with the
 * scraper's role credential (SEC-C-10: it opens /metrics and nothing else), which Node keeps in the KV store and may
 * rotate. With `HARNESS_METRICS_CREDENTIAL_FILE` set,
 * Node writes the current credential to that file (mode 0600, owned by the scraper's user) at start and every minute,
 * so Prometheus' `credentials_file`, re-read on each scrape, follows a rotation. The credential is never logged.
 */
export const METRICS_CREDENTIAL_FILE_ENV = 'HARNESS_METRICS_CREDENTIAL_FILE';
export const METRICS_CREDENTIAL_OWNER_ENV = 'HARNESS_METRICS_CREDENTIAL_OWNER';
/** The `prom/prometheus` image's user (`nobody`). */
export const DEFAULT_SCRAPER_OWNER = 65534;
const REFRESH_MS = 60_000;

interface Logger {
  info(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
}

export interface MetricsCredentialFile {
  /** Writes the credential now; true when a file was written. */
  refresh(): Promise<boolean>;
  stop(): void;
}

function owner(raw: string | undefined): number {
  const trimmed = raw?.trim() ?? '';
  const value = trimmed !== '' ? Number(trimmed) : DEFAULT_SCRAPER_OWNER;
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(
      `${METRICS_CREDENTIAL_OWNER_ENV} must be a numeric uid, got ${JSON.stringify(raw)}`,
    );
  }
  return value;
}

/** `null` when the setting is unset: nothing is written, and no timer runs. */
export function startMetricsCredentialFile(
  env: NodeJS.ProcessEnv,
  credential: () => Promise<string | null>,
  logger: Logger,
  refreshMs: number = REFRESH_MS,
): MetricsCredentialFile | null {
  const path = env[METRICS_CREDENTIAL_FILE_ENV]?.trim() ?? '';
  if (path === '') return null;
  const uid = owner(env[METRICS_CREDENTIAL_OWNER_ENV]);
  let last: string | null = null;
  const refresh = async (): Promise<boolean> => {
    try {
      const current = await credential();
      if (current === null || current === last) return false;
      await mkdir(dirname(path), { recursive: true });
      const staged = join(dirname(path), `.${String(process.pid)}.credential`);
      await writeFile(staged, current, { mode: 0o600 });
      // Only root may give the file away; a non-root Node and scraper share a uid instead.
      if (process.getuid?.() === 0) await chown(staged, uid, uid);
      await rename(staged, path);
      last = current;
      logger.info('agent_harness.metrics.credential_file_written', { path });
      return true;
    } catch (error) {
      logger.warn('agent_harness.metrics.credential_file_failed', {
        path,
        errorType: error instanceof Error ? error.name : typeof error,
      });
      return false;
    }
  };
  void refresh();
  const timer = detachedInterval(() => void refresh(), refreshMs);
  return {
    refresh,
    stop: () => {
      clearInterval(timer);
    },
  };
}
