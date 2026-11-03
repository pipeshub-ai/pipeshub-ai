'use client';

import { useEffect, useRef, useState } from 'react';
import { Box } from '@radix-ui/themes';
import { FileIcon } from '@/app/components/ui';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import type { ArtifactListItem } from '../types';

interface ArtifactThumbnailProps {
  item: ArtifactListItem;
  /** Icon size when there is no image to show. */
  iconSize?: number;
}

/**
 * The image itself for image artifacts, the file icon for everything else.
 * Images are fetched only once the card scrolls into view, so a page of
 * fifty cards does not fire fifty downloads up front.
 */
export function ArtifactThumbnail({ item, iconSize = 40 }: ArtifactThumbnailProps) {
  const isImage = !!item.mimeType?.startsWith('image/');
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!isImage || visible) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: '200px' });
    observer.observe(el);
    return () => observer.disconnect();
  }, [isImage, visible]);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    let url: string | null = null;
    KnowledgeBaseApi.streamRecord(item.artifactId, { version: item.version, suppressErrorToast: true })
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        // A thumbnail is decoration: the card still works with the icon.
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      // A revoked URL must not stay in the <img> while the next version loads.
      setSrc(null);
    };
  }, [visible, item.artifactId, item.version]);

  return (
    <Box
      ref={ref}
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      {src ? (
        <img
          src={src}
          alt={item.name}
          onError={() => setSrc(null)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      ) : (
        <FileIcon filename={item.name} mimeType={item.mimeType || undefined} size={iconSize} />
      )}
    </Box>
  );
}
