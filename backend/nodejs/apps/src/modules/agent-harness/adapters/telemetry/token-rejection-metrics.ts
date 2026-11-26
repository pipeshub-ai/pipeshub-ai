import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';

/**
 * SEC-D-13 (ADR-OPS-03): the machine credentials, attempt tokens and gateway tokens the harness refused. The same
 * family, kinds and reasons as the Python services' (`harness_core.telemetry.security_signals`), so the merged
 * route shows one series per kind and reason whichever process refused.
 */
export const TOKEN_REJECTED_METRIC =
  'pipeshub_agent_harness_token_rejected_total';

export const TOKEN_KINDS = [
  'machine_credential',
  'attempt_token',
  'gateway_token',
] as const;
export type TokenKind = (typeof TOKEN_KINDS)[number] | 'other';

export const TOKEN_REASONS = [
  'missing',
  'malformed',
  'algorithm',
  'unknown_key',
  'signature',
  'issuer',
  'audience',
  'expired',
  'claims',
  'superseded',
  'credential',
] as const;

/** A token only someone without the key would present: what HarnessGatewayTokenRejected pages on. */
export const FORGED_REASONS = [
  'malformed',
  'algorithm',
  'unknown_key',
  'signature',
  'issuer',
  'audience',
  'credential',
] as const;

const rejected = metricsBackend.createCounter({
  name: TOKEN_REJECTED_METRIC,
  help: 'Machine credentials, attempt tokens and gateway tokens the harness refused, by kind and reason',
  labelNames: ['kind', 'reason'],
  // So the first forged token alerts, also after the registry was reset.
  zeroSeries: TOKEN_KINDS.flatMap((kind) =>
    FORGED_REASONS.map((reason) => ({ kind, reason })),
  ),
});

export type TokenRejectionRecorder = (kind: string, reason: string) => void;

export function tokenRejectionLabels(
  kind: string,
  reason: string,
): { kind: string; reason: string } {
  return {
    kind: (TOKEN_KINDS as readonly string[]).includes(kind) ? kind : 'other',
    reason: (TOKEN_REASONS as readonly string[]).includes(reason)
      ? reason
      : 'other',
  };
}

/** Never throws: a metrics fault must not change how a refusal is answered. */
export const recordTokenRejection: TokenRejectionRecorder = (kind, reason) => {
  try {
    rejected.inc(tokenRejectionLabels(kind, reason));
  } catch {
    // Counted nowhere rather than answered differently.
  }
};
