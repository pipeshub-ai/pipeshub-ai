'use client';

import { KnowledgeBaseApi } from '@/knowledge-base/api';
import type { RecordDetailsResponse } from '@/knowledge-base/types';
import { isLocalFsConnectorType } from '@/app/(main)/workspace/connectors/utils/local-fs-helpers';

interface ElectronLocalFsOpenPayload {
  connectorId: string;
  localFsRelativePath?: string | null;
  absolutePath?: string | null;
}

interface ElectronLocalFsOpenResult {
  ok: boolean;
  error?: string;
  code?: string;
}

interface ElectronApi {
  isElectron?: boolean;
  localFs?: {
    openRecordSource?: (
      payload: ElectronLocalFsOpenPayload,
    ) => Promise<ElectronLocalFsOpenResult>;
  };
}

export interface OpenRecordSourceInput {
  recordId: string;
  connector?: string;
  origin?: string;
  webUrl?: string;
  hideWeburl?: boolean;
}

export interface OpenRecordSourceDeps {
  getRecordDetails?: (recordId: string) => Promise<RecordDetailsResponse>;
  openWindow?: (url: string) => void;
  navigateCurrentWindow?: (url: string) => void;
  electronApi?: ElectronApi;
}

export type OpenRecordSourceResult =
  | { opened: 'native' }
  | { opened: 'web-fallback'; error?: string }
  | { opened: 'web'; url: string }
  | { opened: 'none'; error: string };

function getElectronApi(): ElectronApi | undefined {
  return (globalThis as unknown as { electronAPI?: ElectronApi }).electronAPI;
}

function getDefaultFallbackUrl(input: OpenRecordSourceInput): string {
  const webUrl = input.webUrl?.trim();
  if (webUrl && !input.hideWeburl) return webUrl;
  return `/record/${encodeURIComponent(input.recordId)}/`;
}

function isInternalRecordUrl(url: string): boolean {
  return url.startsWith('/record/');
}

function openFallbackUrl(
  url: string,
  deps: OpenRecordSourceDeps,
  electronApi?: ElectronApi,
): void {
  if (electronApi?.isElectron && isInternalRecordUrl(url)) {
    (deps.navigateCurrentWindow ?? ((target) => window.location.assign(target)))(url);
    return;
  }
  (deps.openWindow ?? ((target) => window.open(target, '_blank', 'noopener,noreferrer')))(url);
}

function normalizeAbsolutePath(pathValue?: string | null): string | null {
  const trimmed = pathValue?.trim();
  if (!trimmed || trimmed.startsWith('storage://')) return null;
  return trimmed;
}

export async function openRecordSource(
  input: OpenRecordSourceInput,
  deps: OpenRecordSourceDeps = {},
): Promise<OpenRecordSourceResult> {
  const electronApi = deps.electronApi ?? getElectronApi();
  const fallbackUrl = getDefaultFallbackUrl(input);

  if (!isLocalFsConnectorType(input.connector ?? '')) {
    if (input.webUrl && !input.hideWeburl) {
      openFallbackUrl(input.webUrl, deps, electronApi);
      return { opened: 'web', url: input.webUrl };
    }
    return { opened: 'none', error: 'Source URL is unavailable.' };
  }

  let nativeError: string | undefined;
  const openNative = electronApi?.localFs?.openRecordSource;
  if (!openNative) {
    nativeError = 'Desktop Local FS opener is unavailable.';
    openFallbackUrl(fallbackUrl, deps, electronApi);
    return { opened: 'web-fallback', error: nativeError };
  }

  try {
    const getRecordDetails = deps.getRecordDetails ?? KnowledgeBaseApi.getRecordDetails;
    const details = await getRecordDetails(input.recordId);
    const record = details.record;
    const fileRecord = record.fileRecord;
    const connectorId = record.connectorId;
    const localFsRelativePath = fileRecord?.localFsRelativePath ?? null;
    const absolutePath = normalizeAbsolutePath(fileRecord?.path);

    if (connectorId && (localFsRelativePath || absolutePath)) {
      const result = await openNative({
        connectorId,
        localFsRelativePath,
        absolutePath,
      });
      if (result?.ok) return { opened: 'native' };
      nativeError = result?.error || 'Could not open local source.';
    } else {
      nativeError = 'Local source path is unavailable.';
    }
  } catch (error) {
    nativeError = error instanceof Error ? error.message : 'Could not load record details.';
  }

  openFallbackUrl(fallbackUrl, deps, electronApi);
  return { opened: 'web-fallback', error: nativeError };
}
