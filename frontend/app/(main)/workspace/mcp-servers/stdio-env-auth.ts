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

const SECRET_NAME_PARTS = /TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|PWD|KEY|CREDENTIAL|COOKIE|SESSION|PRIVATE|SIGNATURE|AUTH/;

/** Whether a credential field named after an env var should be masked (`CLIENT_SECRET`, `DB_PASSWORD`, `GITHUB_PAT`). */
export function isSecretFieldName(name: string): boolean {
  const upper = name.toUpperCase();
  return SECRET_NAME_PARTS.test(upper) || /(^|_)PAT($|_)/.test(upper);
}

export function isMultiEnvAuthComplete(
  requiredEnv: string[],
  values: Record<string, string>
): boolean {
  return requiredEnv.every((key) => Boolean(values[key]?.trim()));
}
