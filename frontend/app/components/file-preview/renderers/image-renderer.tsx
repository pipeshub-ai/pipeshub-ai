'use client';

import { useState, useMemo } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';
import type { PreviewCitation } from '../types';

interface ImageRendererProps {
  fileUrl: string;
  fileName: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

export function ImageRenderer({ fileUrl, fileName, citations, activeCitationId, onHighlightClick }: ImageRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [error, setError] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Filter citations that have bounding boxes for overlay display
  const overlays = useMemo(() => {
    if (!citations?.length) return [];
    return citations
      .filter((c) => c.boundingBox && c.boundingBox.length >= 2)
      .map((c) => {
        const box = c.boundingBox!;
        // boundingBox is array of {x,y} points (normalized 0-1).
        // Compute bounding rect from all points.
        const xs = box.map((p) => p.x);
        const ys = box.map((p) => p.y);
        const left = Math.min(...xs) * 100;
        const top = Math.min(...ys) * 100;
        const right = Math.max(...xs) * 100;
        const bottom = Math.max(...ys) * 100;
        return {
          id: c.id,
          left: `${left}%`,
          top: `${top}%`,
          width: `${right - left}%`,
          height: `${bottom - top}%`,
        };
      });
  }, [citations]);

  if (!fileUrl || fileUrl.trim() === '') {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--slate-9)' }}>
          image
        </span>
        <Text size="3" weight="medium" color="gray">
          Image file URL not available
        </Text>
      </Flex>
    );
  }

  if (error) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--red-9)' }}>
          broken_image
        </span>
        <Text size="3" weight="medium" color="red">
          Failed to load image
        </Text>
        <Text size="2" color="gray">
          The image file could not be displayed
        </Text>
      </Flex>
    );
  }

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        width: '100%',
        height: '100%',
        // padding: 'var(--space-4)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <Box
        style={{
          position: 'relative',
          maxWidth: '100%',
          maxHeight: '100%',
          borderRadius: 'var(--radius-3)',
          overflow: 'hidden',
          backgroundColor: isDark ? 'var(--slate-3)' : 'white',
          boxShadow: '0px 12px 32px -16px rgba(0, 0, 0, 0.06), 0px 8px 40px 0px rgba(0, 0, 0, 0.05)',
        }}
      >
        {isLoading && (
          <Flex
            align="center"
            justify="center"
            style={{
              position: 'absolute',
              inset: 0,
              backgroundColor: 'var(--slate-2)',
            }}
          >
            <Text size="2" color="gray">
              Loading image...
            </Text>
          </Flex>
        )}
        <img
          src={fileUrl}
          alt={fileName}
          onLoad={() => setIsLoading(false)}
          onError={() => {
            setError(true);
            setIsLoading(false);
          }}
          style={{
            display: 'block',
            maxWidth: '100%',
            maxHeight: '100%',
            width: 'auto',
            height: 'auto',
            objectFit: 'contain',
          }}
        />
        {/* Citation overlay boxes */}
        {overlays.map((overlay) => {
          const isActive = activeCitationId === overlay.id;
          return (
            <Box
              key={overlay.id}
              role="button"
              tabIndex={0}
              aria-label={`Go to citation ${overlay.id}`}
              onClick={() => onHighlightClick?.(overlay.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onHighlightClick?.(overlay.id);
                }
              }}
              style={{
                position: 'absolute',
                left: overlay.left,
                top: overlay.top,
                width: overlay.width,
                height: overlay.height,
                backgroundColor: isActive
                  ? 'rgba(16, 185, 129, 0.30)'
                  : 'rgba(16, 185, 129, 0.15)',
                border: isActive
                  ? '2px solid rgba(16, 185, 129, 0.8)'
                  : '1.5px solid rgba(16, 185, 129, 0.5)',
                borderRadius: '2px',
                cursor: 'pointer',
                transition: 'background-color 0.2s ease, border-color 0.2s ease',
                pointerEvents: 'auto',
                zIndex: isActive ? 3 : 1,
              }}
            />
          );
        })}
      </Box>
    </Flex>
  );
}
