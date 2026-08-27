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
    if (code === 'health_check_timeout' || err.type === ErrorType.TIMEOUT_ERROR) {
      return t('workspace.aiModels.configSaveTimeoutError');
    }
    if (err.message.trim()) {
      return err.message.trim();
    }
  }

  return t('workspace.aiModels.configSaveErrorFallback');
}
