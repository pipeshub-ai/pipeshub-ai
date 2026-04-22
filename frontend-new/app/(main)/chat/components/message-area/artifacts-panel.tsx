'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { isSignedUrl, isTrustedApiUrl } from '../../utils/parse-download-markers';
import type { ChatArtifact } from '../../types';

interface ArtifactsPanelProps {
  artifacts: ChatArtifact[];
  onPreview?: (artifact: ChatArtifact) => void;
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
    // Untrusted URL (e.g. injected via compromised RAG content) — refuse.
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
  // Use a ref to capture the current blob: URL so the cleanup closure does not
  // close over a stale `src` value (the previous implementation silently
  // leaked object URLs because the cleanup ran before setSrc committed).
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const setAndTrack = (value: string | undefined) => {
      if (cancelled) return;
      // Revoke any prior blob URL before overwriting it.
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
      if (value?.startsWith('blob:')) {
        blobUrlRef.current = value;
      }
      setSrc(value);
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

  return (
    <img
      src={src}
      alt={artifact.fileName}
      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
    />
  );
}

function ArtifactCard({
  artifact,
  onPreview,
}: {
  artifact: ChatArtifact;
  onPreview?: (artifact: ChatArtifact) => void;
}) {
  const icon = getIconForMime(artifact.mimeType);
  const showThumbnail = isPreviewableImage(artifact.mimeType);

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
            onClick={() => onPreview(artifact)}
            style={{ cursor: 'pointer' }}
          >
            <span className="material-icons-outlined" style={{ fontSize: 18 }}>visibility</span>
          </IconButton>
        )}
        <IconButton
          size="1"
          variant="ghost"
          onClick={() => handleDownload(artifact)}
          style={{ cursor: 'pointer' }}
        >
          <span className="material-icons-outlined" style={{ fontSize: 18 }}>download</span>
        </IconButton>
      </Flex>
    </Flex>
  );
}
