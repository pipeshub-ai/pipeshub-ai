'use client';

import type { TFunction } from 'i18next';
import { ErrorType, isProcessedError } from '@/lib/api/api-error';

export function extractErrorCode(value: unknown, depth = 0): string | undefined {
  if (depth > 6 || value == null || typeof value !== 'object') return undefined;
  const rec = value as Record<string, unknown>;
  if (typeof rec.error_code === 'string' && rec.error_code.trim()) {
    return rec.error_code.trim();
  }
  for (const key of ['details', 'error']) {
    const nested = extractErrorCode(rec[key], depth + 1);
    if (nested) return nested;
  }
  return undefined;
}

export function resolveModelConfigSaveError(err: unknown, t: TFunction): string {
  if (isProcessedError(err)) {
    const fromDetails = extractErrorCode(err.details);
    const fromAxiosBody = extractErrorCode(
      (err.originalError as { response?: { data?: unknown } } | undefined)?.response?.data
    );
    const code = fromDetails ?? fromAxiosBody;
    if (code === 'outbound_connectivity') {
      return t('workspace.aiModels.configSaveOutboundError');
    }
    if (code === 'credential_reentry_required') {
      return t('workspace.aiModels.configSaveCredentialReentry');
    }
    if (code === 'health_check_timeout' || err.type === ErrorType.TIMEOUT_ERROR) {
      return t('workspace.aiModels.configSaveTimeoutError');
    }
    if (err.message.trim()) {
      return err.message.trim();
    }
  }

  return t('workspace.aiModels.configSaveErrorFallback');
}

/** A connection-wide key or forwarding save; names the models that block it when the connection spans endpoints. */
export function resolveConnectionSecretError(err: unknown, t: TFunction, fallbackKey: string): string {
  if (!isProcessedError(err)) return t(fallbackKey);
  const body = (err.originalError as { response?: { data?: unknown } } | undefined)?.response?.data;
  if (extractErrorCode(body) === 'credential_reentry_required') {
    const { models = [], reference } = body as { models?: { name?: unknown }[]; reference?: { name?: unknown } };
    return t('workspace.aiModels.connectionEndpointsDiffer', {
      models: models.map((model) => String(model.name ?? '')).filter(Boolean).join(', '),
      reference: String(reference?.name ?? ''),
    });
  }
  return err.message.trim() || t(fallbackKey);
}
