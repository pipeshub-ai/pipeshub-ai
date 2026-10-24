'use client';

import { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { useChatStore } from '../../store';
import { isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import type { ChatArtifact } from '../../types';
import { isArtifactGone } from './artifacts-panel';

export interface ArtifactActions {
  onPreview: (artifact: ChatArtifact) => Promise<void>;
  onViewSource: (codeArtifactId: string) => Promise<void>;
}

/**
 * The `ArtifactsPanel` Preview and View source handlers, for every place that lists an answer's artifacts, so each
 * one opens the same preview.
 */
export function useArtifactActions(latestArtifactVersions?: ReadonlyMap<string, number>): ArtifactActions {
  const { t } = useTranslation();
  const setPreviewFile = useChatStore((s) => s.setPreviewFile);

  /**
   * Streams `artifact.recordId` at `overrideVersion` (defaulting to
   * `artifact.version`) and replaces the preview panel's content in place.
   * Used both for the initial "Preview" click and for every later version
   * switch (bound back to this same function as
   * `ChatPreviewFile.onVersionChange`) — one code path, so streaming options
   * (PDF conversion for PPT/DOCX, mime resolution, DOCX blob handling) never
   * drift between the two entry points.
   */
  const loadArtifactPreview = useCallback(
    async (artifact: ChatArtifact, overrideVersion?: number): Promise<void> => {
      const version = overrideVersion ?? artifact.version;
      const recordId = artifact.recordId;
      const isSwitch = overrideVersion !== undefined;
      const latestVersion = recordId ? latestArtifactVersions?.get(recordId) : undefined;
      const effectiveLatest =
        latestVersion !== undefined && version !== undefined
          ? Math.max(latestVersion, version)
          : latestVersion ?? version;
      const onVersionChange = recordId
        ? (v: number) => loadArtifactPreview(artifact, v)
        : undefined;

      if (recordId) {
        // A version switch (not the initial open) — keep the current
        // content on screen and only flip the small spinner in the version
        // pill, so the panel doesn't flash back to the loading skeleton.
        if (isSwitch) {
          const current = useChatStore.getState().previewFile;
          if (current?.id === recordId) setPreviewFile({ ...current, isSwitchingVersion: true });
        }
        try {
          const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
          const streamAsPdf =
            isPresentationFile(artifact.mimeType, artifact.fileName) ||
            isLegacyWordDocFile(artifact.mimeType, artifact.fileName);
          const blob = await KnowledgeBaseApi.streamRecord(recordId, {
            ...(streamAsPdf ? { convertTo: 'application/pdf' } : {}),
            ...(version !== undefined ? { version } : {}),
            // The card shows a deleted artifact itself; a version switch has no card to tell.
            ...(isSwitch ? {} : { suppressErrorToast: isArtifactGone }),
          });
          const resolvedType = resolvePreviewMimeAfterStream(
            artifact.mimeType,
            artifact.fileName,
            blob,
            streamAsPdf,
          );
          const isDocx = isDocxFile(artifact.mimeType, artifact.fileName);
          const objectUrl = isDocx ? '' : URL.createObjectURL(blob);

          // Release the previous blob URL now that nothing references it —
          // otherwise every version switch leaks one object URL.
          const previous = useChatStore.getState().previewFile;
          if (previous?.url?.startsWith('blob:')) URL.revokeObjectURL(previous.url);

          setPreviewFile({
            id: recordId,
            url: objectUrl,
            blob: isDocx ? blob : undefined,
            name: artifact.fileName,
            type: resolvedType,
            size: artifact.sizeBytes,
            hideFileDetails: true,
            showDownload: true,
            version,
            latestVersion: effectiveLatest,
            onVersionChange,
            isSwitchingVersion: false,
          });
          return;
        } catch (error) {
          if (isSwitch) {
            // Keep showing the last successfully loaded version rather than
            // falling back to a stale/foreign URL below.
            const current = useChatStore.getState().previewFile;
            if (current?.id === recordId) setPreviewFile({ ...current, isSwitchingVersion: false });
            return;
          }
          if (isArtifactGone(error)) throw error;
          // Initial-open failure — fall through to the URL-classification
          // fallback below (never trusts an arbitrary marker URL, see
          // `ArtifactsPanel`'s `handleDownload` docstring for the same rule).
        }
      }

      const fallbackUrl = artifact.downloadUrl?.trim();
      const trusted = !!fallbackUrl && (isTrustedApiUrl(fallbackUrl) || isSignedUrl(fallbackUrl));
      setPreviewFile({
        id: artifact.id,
        url: trusted ? fallbackUrl : '',
        ...(trusted ? {} : { error: t('artifacts.previewFailed') }),
        name: artifact.fileName,
        type: artifact.mimeType,
        size: artifact.sizeBytes,
        hideFileDetails: true,
        showDownload: true,
        version,
        latestVersion: effectiveLatest,
        onVersionChange,
      });
    },
    [latestArtifactVersions, setPreviewFile, t],
  );

  const onPreview = useCallback((artifact: ChatArtifact) => loadArtifactPreview(artifact), [loadArtifactPreview]);

  const onViewSource = useCallback(
    async (codeArtifactId: string): Promise<void> => {
      // The code artifact is registered through the same pipeline as any
      // other record, so it streams/previews via the standard KB record
      // APIs exactly like the `recordId` path above — no separate endpoint.
      try {
        const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
        const [details, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(codeArtifactId, { suppressErrorToast: isArtifactGone }),
          KnowledgeBaseApi.streamRecord(codeArtifactId, { suppressErrorToast: isArtifactGone }),
        ]);
        const objectUrl = URL.createObjectURL(blob);
        setPreviewFile({
          id: codeArtifactId,
          url: objectUrl,
          name: details.record.recordName,
          type: details.record.mimeType || 'text/plain',
          size: details.record.fileRecord?.sizeInBytes ?? blob.size,
          hideFileDetails: true,
          showDownload: true,
        });
      } catch (error) {
        // The card hides "view source" once the source is gone.
        if (isArtifactGone(error)) throw error;
      }
    },
    [setPreviewFile],
  );

  return useMemo(() => ({ onPreview, onViewSource }), [onPreview, onViewSource]);
}
