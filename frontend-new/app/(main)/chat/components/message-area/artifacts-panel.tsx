'use client';

import React, { useState, useEffect } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
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

async function handleDownload(artifact: ChatArtifact): Promise<void> {
  if (artifact.recordId) {
    try {
      await KnowledgeBaseApi.streamDownloadRecord(artifact.recordId, artifact.fileName);
      return;
    } catch {
      // Fall back to raw URL if record API fails
    }
  }
  if (artifact.downloadUrl) {
    const link = document.createElement('a');
    link.href = artifact.downloadUrl;
    link.download = artifact.fileName;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
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

  useEffect(() => {
    if (!artifact.recordId) {
      setSrc(artifact.downloadUrl || undefined);
      return;
    }
    let revoked = false;
    KnowledgeBaseApi.streamRecord(artifact.recordId)
      .then((blob) => {
        if (revoked) return;
        setSrc(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (!revoked && artifact.downloadUrl) setSrc(artifact.downloadUrl);
      });
    return () => {
      revoked = true;
      if (src?.startsWith('blob:')) URL.revokeObjectURL(src);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
