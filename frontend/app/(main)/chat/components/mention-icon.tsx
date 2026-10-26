'use client';

import React from 'react';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import type { NodeType } from '@/app/(main)/knowledge-base/types';

interface MentionIconProps {
  nodeType: string;
  connector?: string | null;
  extension?: string | null;
  mimeType?: string | null;
  name: string;
  size?: number;
}

/** The app a mentioned node comes from; a collection's own items show their file type instead. */
export function MentionIcon({ nodeType, connector, extension, mimeType, name, size = 13 }: MentionIconProps) {
  const app = (connector ?? '').trim();
  if (app && app.toUpperCase() !== 'KB') {
    return <KbNodeNameIcon isKnowledgeHub nodeType="app" connector={app} name={name} size={size} />;
  }
  return (
    <KbNodeNameIcon
      isKnowledgeHub
      nodeType={(nodeType || 'record') as NodeType}
      connector={app || null}
      extension={extension}
      mimeType={mimeType}
      name={name}
      size={size}
    />
  );
}
