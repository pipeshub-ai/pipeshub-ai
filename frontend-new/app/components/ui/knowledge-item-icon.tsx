'use client';

import React from 'react';
import { FolderIcon } from './folder-icon';
import { FileIcon } from './file-icon';
import { ConnectorIcon } from './ConnectorIcon';
import type { ConnectorType } from './ConnectorIcon';

/**
 * The "kind" of knowledge item — determines which icon is rendered.
 *
 * - 'collection' / 'folder': renders FolderIcon
 * - 'file': renders FileIcon (uses extension/mimeType to pick the right icon)
 * - 'connector': renders ConnectorIcon
 */
export type KnowledgeItemKind = 'collection' | 'folder' | 'file' | 'connector';

interface KnowledgeItemIconProps {
  /** What kind of item this is */
  kind: KnowledgeItemKind;
  /** Size in px (default 20) */
  size?: number;
  /** File extension — used when kind='file' (e.g. 'pdf', 'docx') */
  extension?: string;
  /** File MIME type — used when kind='file' as fallback for extension */
  mimeType?: string;
  /** Filename — used when kind='file' to extract extension */
  filename?: string;
  /** Connector key — used when kind='connector' (e.g. 'google-drive', 'slack') */
  connectorType?: ConnectorType;
  /** Override color for folder icon (default: accent-11) */
  color?: string;
}

/**
 * Unified icon component for knowledge items across the app.
 *
 * Use this wherever you need to display an icon for a collection,
 * folder, file, or connector source — it picks the right visual
 * automatically based on the `kind` prop.
 *
 * @example
 * ```tsx
 * <KnowledgeItemIcon kind="collection" size={20} />
 * <KnowledgeItemIcon kind="file" extension="pdf" size={20} />
 * <KnowledgeItemIcon kind="connector" connectorType="slack" size={20} />
 * ```
 */
export function KnowledgeItemIcon({
  kind,
  size = 20,
  extension,
  mimeType,
  filename,
  connectorType,
  color = 'var(--accent-11)',
}: KnowledgeItemIconProps) {
  switch (kind) {
    case 'collection':
    case 'folder':
      return <FolderIcon size={size} color={color} />;

    case 'file':
      return (
        <FileIcon
          extension={extension}
          mimeType={mimeType}
          filename={filename}
          size={size}
        />
      );

    case 'connector':
      if (connectorType) {
        return <ConnectorIcon type={connectorType} size={size} />;
      }
      return <FolderIcon size={size} color={color} />;

    default:
      return <FolderIcon size={size} color={color} />;
  }
}
