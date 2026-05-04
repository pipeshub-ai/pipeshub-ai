'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { Spinner } from '@/app/components/ui/spinner';
import { isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import type { ChatArtifact } from '../../types';

interface ArtifactsPanelProps {
  artifacts: ChatArtifact[];
  onPreview?: (artifact: ChatArtifact) => void | Promise<void>;
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
      await KnowledgeBaseApi.streamDownloadRecord(artifact.recordId, artifact.fileName);
      return;
    } catch {
      // Intentionally fall through to URL classification below — but without
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

export function ArtifactsPanel({ artifacts, onPreview }: ArtifactsPanelProps) {
  const [collapsed, setCollapsed] = useState(false);

  if (!artifacts.length) return null;

  return (
    <Box style={{ marginTop: 'var(--space-3)' }}>
      <Flex
        align="center"
        gap="2"
        style={{ cursor: 'pointer', marginBottom: collapsed ? 0 : 'var(--space-2)' }}
        onClick={() => setCollapsed((v) => !v)}
      >
        <span className="material-icons-outlined" style={{ fontSize: 18, color: 'var(--accent-11)' }}>
          {collapsed ? 'expand_more' : 'expand_less'}
        </span>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {artifacts.length} artifact{artifacts.length !== 1 ? 's' : ''} generated
        </Text>
      </Flex>

      {!collapsed && (
        <Flex direction="column" gap="2">
          {artifacts.map((artifact) => (
            <ArtifactCard key={artifact.id} artifact={artifact} onPreview={onPreview} />
          ))}
        </Flex>
      )}
    </Box>
  );
}

function ArtifactThumbnail({ artifact }: { artifact: ChatArtifact }) {
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

    KnowledgeBaseApi.streamRecord(artifact.recordId)
      .then((blob) => {
        if (cancelled) return;
        setAndTrack(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (cancelled) return;
        setAndTrack(trustedFallbackUrl());
      });

    return () => {
      cancelled = true;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [artifact.recordId, artifact.downloadUrl]);

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
          <span
            className="material-icons-outlined"
            style={{ fontSize: 20, color: 'var(--accent-11)' }}
          >
            image
          </span>
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
}: {
  artifact: ChatArtifact;
  onPreview?: (artifact: ChatArtifact) => void | Promise<void>;
}) {
  const icon = getIconForMime(artifact.mimeType);
  const showThumbnail = isPreviewableImage(artifact.mimeType);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
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
    } finally {
      if (mountedRef.current) setIsDownloading(false);
    }
  };

  const runPreview = async () => {
    if (!onPreview || isPreviewing) return;
    setIsPreviewing(true);
    try {
      await onPreview(artifact);
    } finally {
      if (mountedRef.current) setIsPreviewing(false);
    }
  };

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
          <ArtifactThumbnail artifact={artifact} />
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
          <span className="material-icons-outlined" style={{ fontSize: 20, color: 'var(--accent-11)' }}>
            {icon}
          </span>
        </Flex>
      )}

      <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {artifact.fileName}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {artifact.artifactType} {artifact.sizeBytes > 0 ? `· ${formatFileSize(artifact.sizeBytes)}` : ''}
        </Text>
      </Flex>

      <Flex gap="1">
        {onPreview && (
          <IconButton
            size="1"
            variant="ghost"
            onClick={runPreview}
            disabled={isPreviewing}
            aria-label={isPreviewing ? 'Loading preview' : 'Preview artifact'}
            style={{ cursor: isPreviewing ? 'wait' : 'pointer' }}
          >
            {isPreviewing ? (
              <Spinner size={16} />
            ) : (
              <span className="material-icons-outlined" style={{ fontSize: 18 }}>visibility</span>
            )}
          </IconButton>
        )}
        <IconButton
          size="1"
          variant="ghost"
          onClick={runDownload}
          disabled={isDownloading}
          aria-label={isDownloading ? 'Downloading artifact' : 'Download artifact'}
          style={{ cursor: isDownloading ? 'wait' : 'pointer' }}
        >
          {isDownloading ? (
            <Spinner size={16} />
          ) : (
            <span className="material-icons-outlined" style={{ fontSize: 18 }}>download</span>
          )}
        </IconButton>
      </Flex>
    </Flex>
  );
}
