'use client';

import type { ReactNode } from 'react';
import { FolderIcon } from '@/app/components/ui';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { NodeType } from '../types';
import { isKbCollectionsHubApp } from './all-records-transformer';

const HUB_FOLDER_TYPES: readonly NodeType[] = ['kb', 'app', 'folder', 'recordGroup'];

function normalizeConnectorKey(connector?: string | null): string {
  return (connector ?? '').toLowerCase().replace(/[^a-z0-9-]/g, '');
}

/** Folder and file marks get the app in the corner so the row says where it came from. */
function withAppMark(icon: ReactNode, connector: string | null | undefined, size: number): ReactNode {
  const key = normalizeConnectorKey(connector);
  if (!key) return icon;
  const type = resolveConnectorType(key);
  if (type === 'generic' || type === 'kb' || type === 'knowledge-base') return icon;
  // Small enough to read as a corner mark, not a second icon competing with the first.
  const badge = Math.max(9, Math.round(size * 0.55));
  return (
    <span style={{ position: 'relative', display: 'inline-flex', width: size, height: size, flexShrink: 0 }}>
      {icon}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          right: -2,
          bottom: -2,
          width: badge,
          height: badge,
          borderRadius: 999,
          background: 'var(--olive-1)',
          boxShadow: '0 0 0 1px var(--olive-3)',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <ConnectorIcon type={type} size={badge - 2} />
      </span>
    </span>
  );
}

export interface KbNodeNameIconProps {
  /** Knowledge Hub row (sidebar / hub table) */
  isKnowledgeHub: boolean;
  nodeType?: NodeType;
  connector?: string | null;
  subType?: string | null;
  extension?: string | null;
  mimeType?: string | null;
  /** Legacy collections row (`KnowledgeBaseItem`) */
  legacyType?: 'folder' | 'file';
  legacyFileType?: string;
  name?: string;
  isSelected?: boolean;
  size?: number;
  /** Placeholder stub for an out-of-scope ancestor — rendered with a distinct icon. */
  isPlaceholder?: boolean;
}

export function KbNodeNameIcon({
  isKnowledgeHub,
  nodeType,
  connector,
  subType,
  extension,
  mimeType,
  legacyType,
  legacyFileType,
  name,
  isSelected = false,
  size = 16,
  isPlaceholder = false,
}: KbNodeNameIconProps) {
  const isHubFolder =
    isKnowledgeHub &&
    !!nodeType &&
    HUB_FOLDER_TYPES.includes(nodeType as NodeType);

  const isLegacyFolder = !isKnowledgeHub && legacyType === 'folder';

  const isFolder = isHubFolder || isLegacyFolder;
  const isApp = isKnowledgeHub && nodeType === 'app';

  if (isFolder && isApp) {
    if (isKbCollectionsHubApp({ connector, subType })) {
      return <FolderIcon variant="default" size={size} color="var(--emerald-11)" />;
    }
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
    return withAppMark(
      <FolderIcon variant="default" size={size} color="var(--accent-9)" />,
      connector,
      size,
    );
  }

  // Placeholder stubs (out-of-scope ancestors) hold hierarchy but carry no real content;
  // show a muted document icon so they read as distinct from real synced records.
  if (isPlaceholder) {
    return withAppMark(
      <MaterialIcon name="description" size={size} color="var(--slate-9)" />,
      connector,
      size,
    );
  }

  const ext = isKnowledgeHub
    ? extension || mimeType?.split('/')[1] || undefined
    : legacyFileType;

  return withAppMark(
    <FileIcon
      extension={ext}
      filename={name}
      size={size}
      fallbackIcon="description"
    />,
    connector,
    size,
  );
}
