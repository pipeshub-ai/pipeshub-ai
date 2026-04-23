'use client';

import { FolderIcon } from '@/app/components/ui';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { NodeType } from '../types';

const HUB_FOLDER_TYPES: readonly NodeType[] = ['kb', 'app', 'folder', 'recordGroup'];

function normalizeConnectorKey(connector?: string | null): string {
  return (connector ?? '').toLowerCase().replace(/[^a-z0-9-]/g, '');
}

export interface KbNodeNameIconProps {
  /** Knowledge Hub row (sidebar / hub table) */
  isKnowledgeHub: boolean;
  nodeType?: NodeType;
  connector?: string | null;
  extension?: string | null;
  mimeType?: string | null;
  /** Legacy collections row (`KnowledgeBaseItem`) */
  legacyType?: 'folder' | 'file';
  legacyFileType?: string;
  name?: string;
  isSelected?: boolean;
  size?: number;
}

export function KbNodeNameIcon({
  isKnowledgeHub,
  nodeType,
  connector,
  extension,
  mimeType,
  legacyType,
  legacyFileType,
  name,
  isSelected = false,
  size = 16,
}: KbNodeNameIconProps) {
  const isHubFolder =
    isKnowledgeHub &&
    !!nodeType &&
    HUB_FOLDER_TYPES.includes(nodeType as NodeType);

  const isLegacyFolder = !isKnowledgeHub && legacyType === 'folder';

  const isFolder = isHubFolder || isLegacyFolder;
  const isApp = isKnowledgeHub && nodeType === 'app';

  if (isFolder && isApp) {
    const key = normalizeConnectorKey(connector);
    if (!key) {
      return (
        <MaterialIcon
          name="extension"
          size={size}
          color={isSelected ? 'var(--accent-9)' : 'var(--slate-11)'}
        />
      );
    }
    return <ConnectorIcon type={key} size={size} />;
  }

  if (isFolder) {
    return (
      <FolderIcon variant="default" size={size} color="var(--emerald-11)" />
    );
  }

  const ext = isKnowledgeHub
    ? extension || mimeType?.split('/')[1] || undefined
    : legacyFileType;

  return (
    <FileIcon
      extension={ext}
      filename={name}
      size={size}
      fallbackIcon="description"
    />
  );
}
