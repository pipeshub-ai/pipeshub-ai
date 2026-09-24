import type { McpAuthenticatePayload } from './types';

/** True when STDIO api_token auth needs more than a single token field (e.g. Slack). */
export function needsMultiEnvAuth(requiredEnv: string[] | undefined | null): boolean {
  return (requiredEnv?.length ?? 0) > 1;
}

/** Build authenticate payload for STDIO servers that declare multiple required env vars. */
export function buildMultiEnvAuthPayload(
  requiredEnv: string[],
  optionalEnv: string[],
  values: Record<string, string>
): McpAuthenticatePayload {
  const env: Record<string, string> = {};
  for (const key of [...requiredEnv, ...optionalEnv]) {
    const value = values[key]?.trim();
    if (value) env[key] = value;
  }
  const primary = requiredEnv[0] ? env[requiredEnv[0]] : undefined;
  return {
    ...(primary ? { apiToken: primary } : {}),
    env,
  };
}

export function isMultiEnvAuthComplete(
  requiredEnv: string[],
  values: Record<string, string>
): boolean {
  return requiredEnv.every((key) => Boolean(values[key]?.trim()));
}
