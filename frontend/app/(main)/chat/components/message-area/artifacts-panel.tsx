'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Box, Button, Flex, Text, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { Spinner } from '@/app/components/ui/spinner';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { isProcessedError } from '@/lib/api';
import { isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import type { ChatArtifact } from '../../types';

interface ArtifactsPanelProps {
  artifacts: ChatArtifact[];
  /** Rejects with a gone error (see `isArtifactGone`) when the artifact was deleted. */
  onPreview?: (artifact: ChatArtifact) => void | Promise<void>;
  /** Jump to the CODE artifact `artifact.derivedFromCodeArtifactId` was generated from. Rejects with a gone error when that artifact was deleted. */
  onViewSource?: (codeArtifactId: string) => void | Promise<void>;
  /** recordId -> highest version seen anywhere in the conversation — powers the "newer version available" hint (see `MessageList`). */
  latestArtifactVersions?: Map<string, number>;
}

const MIME_ICONS: Record<string, string> = {
  'image/': 'image',
  'text/csv': 'table_chart',
  'application/pdf': 'picture_as_pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml': 'description',
  'application/vnd.openxmlformats-officedocument.spreadsheetml': 'table_chart',
  'application/vnd.openxmlformats-officedocument.presentationml': 'slideshow',
  'text/html': 'code',
  'text/': 'article',
  'application/json': 'data_object',
};

function getIconForMime(mimeType: string): string {
  for (const [prefix, icon] of Object.entries(MIME_ICONS)) {
    if (mimeType.startsWith(prefix)) return icon;
  }
  return 'attach_file';
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isPreviewableImage(mimeType: string): boolean {
  return mimeType.startsWith('image/') && !mimeType.includes('svg');
}

/** The artifact was deleted, or the caller lost access to it. */
export function isArtifactGone(error: unknown): boolean {
  return isProcessedError(error) && (error.statusCode === 403 || error.statusCode === 404);
}

/**
 * SECURITY: This function is the ONLY way the UI reaches an artifact URL.
 * The URL ultimately comes from the saved LLM message content, which is a
 * prompt-injection surface in a RAG app. We therefore classify every URL:
 *
 * - A ``recordId`` is the trusted path — stream through our authenticated API.
 * - An HTTPS signed URL (S3 / Azure presigned) is a direct anchor click with
 *   ``rel="noopener noreferrer"``; no bearer token is attached.
 * - A URL pointing at our own configured API origin is also OK (the anchor
 *   click will carry the user's session cookies but not cross-origin).
 * - Anything else is refused — we never "fall back" to an arbitrary attacker
 *   URL even if the recordId stream fails.
 */
async function handleDownload(artifact: ChatArtifact): Promise<void> {
  if (artifact.recordId) {
    try {
      await KnowledgeBaseApi.streamDownloadRecord(artifact.recordId, artifact.fileName, {
        version: artifact.version,
        suppressErrorToast: isArtifactGone,
      });
      return;
    } catch (error) {
      // A deleted artifact must not resurface through a stale signed URL.
      if (isArtifactGone(error)) throw error;
      // Otherwise fall through to URL classification below — but without
      // the recordId path, we do NOT trust an arbitrary downloadUrl.
    }
  }

  const url = artifact.downloadUrl?.trim();
  if (!url) return;

  const trusted = isTrustedApiUrl(url) || isSignedUrl(url);
  if (!trusted) {
    if (typeof window !== 'undefined' && typeof console !== 'undefined') {
      // eslint-disable-next-line no-console
      console.warn('[artifacts-panel] Refusing to open untrusted artifact URL:', url);
    }
    return;
  }

  const link = document.createElement('a');
  link.href = url;
  link.download = artifact.fileName;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function ArtifactsPanel({
  artifacts,
  onPreview,
  onViewSource,
  latestArtifactVersions,
}: ArtifactsPanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);

  if (!artifacts.length) return null;

  return (
    <Box style={{ marginTop: 'var(--space-3)' }}>
      <Button
        variant="ghost"
        color="gray"
        size="2"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((v) => !v)}
        style={{ marginBottom: collapsed ? 0 : 'var(--space-2)' }}
      >
        <MaterialIcon name={collapsed ? 'expand_more' : 'expand_less'} size={18} color="var(--accent-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('chat.artifactsPanel.generated', { count: artifacts.length })}
        </Text>
      </Button>

      {!collapsed && (
        <Flex direction="column" gap="2">
          {artifacts.map((artifact) => {
            const latestVersion = artifact.recordId
              ? latestArtifactVersions?.get(artifact.recordId)
              : undefined;
            const newerVersionAvailable =
              !!artifact.version && !!latestVersion && latestVersion > artifact.version
                ? latestVersion
                : undefined;
            return (
              <ArtifactCard
                key={artifact.id}
                artifact={artifact}
                onPreview={onPreview}
                onViewSource={onViewSource}
                newerVersionAvailable={newerVersionAvailable}
              />
            );
          })}
        </Flex>
      )}
    </Box>
  );
}

function ArtifactThumbnail({ artifact, onGone }: { artifact: ChatArtifact; onGone: (gone: boolean) => void }) {
  const [src, setSrc] = useState<string | undefined>(undefined);
  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);
  // Use a ref to capture the current blob: URL so the cleanup closure does not
  // close over a stale `src` value (the previous implementation silently
  // leaked object URLs because the cleanup ran before setSrc committed).
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Reset load state whenever the underlying artifact identity changes so
    // we show the skeleton again instead of flashing a stale image.
    setLoaded(false);
    setErrored(false);

    const setAndTrack = (value: string | undefined) => {
      if (cancelled) return;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
      if (value?.startsWith('blob:')) {
        blobUrlRef.current = value;
      }
      setSrc(value);
      // No URL at all => treat as errored so we render the fallback icon
      // instead of leaving the skeleton spinning forever.
      if (!value) setErrored(true);
    };

    // SECURITY: if there is no recordId we will only render a raw URL when it
    // is explicitly trusted — never an arbitrary marker-supplied URL.
    const trustedFallbackUrl = () => {
      const u = artifact.downloadUrl?.trim();
      if (!u) return undefined;
      return isTrustedApiUrl(u) || isSignedUrl(u) ? u : undefined;
    };

    if (!artifact.recordId) {
      setAndTrack(trustedFallbackUrl());
      return () => {
        cancelled = true;
      };
    }

    KnowledgeBaseApi.streamRecord(artifact.recordId, {
      version: artifact.version,
      suppressErrorToast: isArtifactGone,
    })
      .then((blob) => {
        if (cancelled) return;
        setAndTrack(URL.createObjectURL(blob));
      })
      .catch((error) => {
        if (cancelled) return;
        if (isArtifactGone(error)) {
          onGone(true);
          return;
        }
        setAndTrack(trustedFallbackUrl());
      });

    return () => {
      cancelled = true;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [artifact.recordId, artifact.downloadUrl, artifact.version, onGone]);

  const showSkeleton = !src || (!loaded && !errored);

  return (
    <Box style={{ position: 'relative', width: '100%', height: '100%' }}>
      {showSkeleton && (
        <Flex
          align="center"
          justify="center"
          style={{
            position: 'absolute',
            inset: 0,
            backgroundColor: 'var(--slate-4)',
            backgroundImage:
              'linear-gradient(90deg, var(--slate-3) 0%, var(--slate-5) 50%, var(--slate-3) 100%)',
            backgroundSize: '400% 100%',
            animation: 'shimmer-sweep 1.4s linear infinite',
          }}
        >
          <Spinner size={14} color="var(--slate-11)" />
        </Flex>
      )}
      {errored && (
        <Flex
          align="center"
          justify="center"
          style={{
            position: 'absolute',
            inset: 0,
            backgroundColor: 'var(--accent-3)',
          }}
        >
          <MaterialIcon name="image" size={20} color="var(--accent-11)" />
        </Flex>
      )}
      {src && (
        <img
          src={src}
          alt={artifact.fileName}
          onLoad={() => setLoaded(true)}
          onError={() => setErrored(true)}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: 'block',
            opacity: loaded ? 1 : 0,
            transition: 'opacity 150ms ease-out',
          }}
        />
      )}
    </Box>
  );
}

function ArtifactCard({
  artifact,
  onPreview,
  onViewSource,
  newerVersionAvailable,
}: {
  artifact: ChatArtifact;
  onPreview?: (artifact: ChatArtifact) => void | Promise<void>;
  onViewSource?: (codeArtifactId: string) => void | Promise<void>;
  /** Set to the higher version number when a later turn has since produced a newer copy of this same artifact. */
  newerVersionAvailable?: number;
}) {
  const { t } = useTranslation();
  const icon = getIconForMime(artifact.mimeType);
  const showThumbnail = isPreviewableImage(artifact.mimeType);
  const [unavailable, setUnavailable] = useState(false);
  const [sourceGone, setSourceGone] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isViewingSource, setIsViewingSource] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runDownload = async () => {
    if (isDownloading) return;
    setIsDownloading(true);
    try {
      await handleDownload(artifact);
    } catch (error) {
      if (isArtifactGone(error) && mountedRef.current) setUnavailable(true);
    } finally {
      if (mountedRef.current) setIsDownloading(false);
    }
  };

  const runPreview = async () => {
    if (!onPreview || isPreviewing) return;
    setIsPreviewing(true);
    try {
      await onPreview(artifact);
    } catch (error) {
      if (isArtifactGone(error) && mountedRef.current) setUnavailable(true);
    } finally {
      if (mountedRef.current) setIsPreviewing(false);
    }
  };

  const runViewSource = async () => {
    if (!onViewSource || !artifact.derivedFromCodeArtifactId || isViewingSource) return;
    setIsViewingSource(true);
    try {
      await onViewSource(artifact.derivedFromCodeArtifactId);
    } catch (error) {
      // Only the source code is gone; this artifact itself may still exist.
      if (isArtifactGone(error) && mountedRef.current) setSourceGone(true);
    } finally {
      if (mountedRef.current) setIsViewingSource(false);
    }
  };

  if (unavailable) {
    return (
      <Flex
        align="center"
        gap="3"
        style={{
          padding: 'var(--space-2) var(--space-3)',
          borderRadius: 'var(--radius-2)',
          border: '1px dashed var(--slate-6)',
          backgroundColor: 'var(--slate-2)',
        }}
      >
        <Flex
          align="center"
          justify="center"
          style={{ width: 40, height: 40, borderRadius: 'var(--radius-1)', backgroundColor: 'var(--slate-3)', flexShrink: 0 }}
        >
          <MaterialIcon name="hide_source" size={20} color="var(--slate-9)" />
        </Flex>
        <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-10)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {artifact.fileName}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {t('chat.artifactsPanel.unavailable')}
          </Text>
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex
      align="center"
      gap="3"
      style={{
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--slate-6)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      {showThumbnail ? (
        <Box
          style={{
            width: 40,
            height: 40,
            borderRadius: 'var(--radius-1)',
            overflow: 'hidden',
            flexShrink: 0,
          }}
        >
          <ArtifactThumbnail artifact={artifact} onGone={setUnavailable} />
        </Box>
      ) : (
        <Flex
          align="center"
          justify="center"
          style={{
            width: 40,
            height: 40,
            borderRadius: 'var(--radius-1)',
            backgroundColor: 'var(--accent-3)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={icon} size={20} color="var(--accent-11)" />
        </Flex>
      )}

      <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
        <Flex align="center" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {artifact.fileName}
          </Text>
          {!!artifact.version && (
            <Text
              size="1"
              weight="medium"
              style={{
                color: 'var(--accent-11)',
                backgroundColor: 'var(--accent-3)',
                borderRadius: 'var(--radius-1)',
                padding: '0 6px',
                flexShrink: 0,
              }}
            >
              v{artifact.version}
            </Text>
          )}
        </Flex>
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {t(`artifacts.types.${artifact.artifactType}`, { defaultValue: artifact.artifactType })}{' '}
          {artifact.sizeBytes > 0 ? `· ${formatFileSize(artifact.sizeBytes)}` : ''}
        </Text>
        {newerVersionAvailable !== undefined && (
          <Text size="1" weight="medium" style={{ color: 'var(--amber-11)' }}>
            {t('chat.artifactsPanel.newerVersion', { version: newerVersionAvailable })}
          </Text>
        )}
      </Flex>

      <Flex gap="1">
        {onViewSource && artifact.derivedFromCodeArtifactId && !sourceGone && (
          <IconButton
            size="1"
            variant="ghost"
            onClick={runViewSource}
            disabled={isViewingSource}
            aria-label={isViewingSource ? t('chat.artifactsPanel.loadingSource') : t('chat.artifactsPanel.viewSource')}
            title={t('chat.artifactsPanel.viewSourceTitle')}
            style={{ cursor: isViewingSource ? 'wait' : 'pointer' }}
          >
            {isViewingSource ? (
              <Spinner size={16} />
            ) : (
              <MaterialIcon name="code" size={18} />
            )}
          </IconButton>
        )}
        {onPreview && (
          <IconButton
            size="1"
            variant="ghost"
            onClick={runPreview}
            disabled={isPreviewing}
            aria-label={isPreviewing ? t('chat.artifactsPanel.loadingPreview') : t('chat.artifactsPanel.preview')}
            style={{ cursor: isPreviewing ? 'wait' : 'pointer' }}
          >
            {isPreviewing ? (
              <Spinner size={16} />
            ) : (
              <MaterialIcon name="visibility" size={18} />
            )}
          </IconButton>
        )}
        <IconButton
          size="1"
          variant="ghost"
          onClick={runDownload}
          disabled={isDownloading}
          aria-label={isDownloading ? t('chat.artifactsPanel.downloading') : t('chat.artifactsPanel.download')}
          style={{ cursor: isDownloading ? 'wait' : 'pointer' }}
        >
          {isDownloading ? (
            <Spinner size={16} />
          ) : (
            <MaterialIcon name="download" size={18} />
          )}
        </IconButton>
      </Flex>
    </Flex>
  );
}
