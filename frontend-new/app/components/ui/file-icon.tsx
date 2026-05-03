'use client';

/**
 * File-type glyphs: inline SVG with theme tokens such as `var(--file-icon-fill)`.
 * For static SVG assets that use `currentColor` (for example LLM provider marks), prefer
 * `ThemeableAssetIcon` from `./themeable-asset-icon` over a raw `<img>` so light/dark themes
 * keep contrast.
 */

import React from 'react';
import { MaterialIcon } from './MaterialIcon';
import { FILE_TYPE_ICON_MAP } from './file-type-icons';
import { getFileExtension, getMimeTypeExtension } from '@/lib/utils/file-icon-utils';

interface FileIconProps {
  extension?: string;
  filename?: string;
  mimeType?: string;
  size?: number;
  className?: string;
  fallbackIcon?: string;
}

export function FileIcon({
  extension,
  filename,
  mimeType,
  size = 20,
  className,
  fallbackIcon = 'insert_drive_file',
}: FileIconProps) {
  // Extract extension from: 1) explicit prop, 2) filename, 3) MIME type
  const ext = extension ||
              (filename ? getFileExtension(filename) : null) ||
              (mimeType ? getMimeTypeExtension(mimeType) : null);

  if (!ext) {
    return (
      <MaterialIcon
        name={fallbackIcon}
        size={size}
        color="var(--slate-9)"
      />
    );
  }

  const normalizedExt = ext.toLowerCase().replace(/^\./, '');
  const IconComponent = FILE_TYPE_ICON_MAP[normalizedExt];

  if (!IconComponent) {
    return (
      <MaterialIcon
        name={fallbackIcon}
        size={size}
        color="var(--slate-9)"
      />
    );
  }

  return (
    <span className={className} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      <IconComponent size={size} />
    </span>
  );
}
