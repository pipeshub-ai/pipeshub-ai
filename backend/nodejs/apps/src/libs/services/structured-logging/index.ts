/**
 * Logging provider port.
 *
 * The logger asks a provider for three things: the console format, whether to
 * keep the local log files, and how to show the request's org and user on a
 * record. The default provider keeps the text logging and shows both ids. A build
 * can supply its own provider through `structuredLoggingProvider` in
 * `src/edition.ts` without touching the logger. Mirrors
 * `backend/python/app/utils/structured_logging`.
 */
import type { Logform } from 'winston';
import { structuredLoggingProvider } from '../../../edition';

export interface StructuredLoggingProvider {
  /**
   * Format for the console transport. `defaultFormat` is the text format the
   * logger would use on its own. May throw on a misconfigured deployment;
   * callers must not swallow that.
   */
  consoleFormat(defaultFormat: Logform.Format): Logform.Format;

  /** Whether the logger also writes `error.log` and `combined.log`. */
  fileLoggingEnabled(): boolean;

  /**
   * How the request's org and user appear on a record: return them as they
   * are, replace a value (a hash), or leave a key out to hide it. Takes
   * precedence over `hashOrgId`.
   */
  logIdentity?(ids: LogIdentity): LogIdentity;

  /**
   * Legacy hook, used only when `logIdentity` is absent: a non-reversible
   * stand-in for `orgId`, set on the record as `organization_id_hash`; `null`
   * leaves the org off the record. Never return `orgId` itself. With this hook
   * neither raw id is shown.
   */
  hashOrgId?(orgId: string): string | null;
}

/** The request identity as it is put on a record (snake_case, as in Python). */
export interface LogIdentity {
  org_id?: string;
  user_id?: string;
}

/** Text to the console plus the local files; the org and user ids shown as they are. */
export class DefaultLoggingProvider implements StructuredLoggingProvider {
  consoleFormat(defaultFormat: Logform.Format): Logform.Format {
    return defaultFormat;
  }

  fileLoggingEnabled(): boolean {
    return true;
  }

  logIdentity(ids: LogIdentity): LogIdentity {
    return ids;
  }
}

function isProvider(value: unknown): value is StructuredLoggingProvider {
  const v = value as Partial<StructuredLoggingProvider> | null;
  return (
    !!v &&
    typeof v.consoleFormat === 'function' &&
    typeof v.fileLoggingEnabled === 'function' &&
    (typeof v.logIdentity === 'function' || typeof v.hashOrgId === 'function')
  );
}

let cached: StructuredLoggingProvider | undefined;

export function getProvider(): StructuredLoggingProvider {
  if (cached) return cached;
  const override = structuredLoggingProvider();
  if (override === null || override === undefined) {
    cached = new DefaultLoggingProvider();
  } else if (isProvider(override)) {
    cached = override;
  } else {
    throw new TypeError(
      'structuredLoggingProvider() in src/edition.ts must return a StructuredLoggingProvider or null',
    );
  }
  return cached;
}

/** Test hook: clear the memoized provider. */
export function resetProviderForTests(): void {
  cached = undefined;
}
