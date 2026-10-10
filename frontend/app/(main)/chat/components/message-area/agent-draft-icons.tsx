'use client';

import React from 'react';
import { ConnectorIcon } from '@/app/components/ui';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { AGENT_TOOLSET_FALLBACK_ICON } from '@/app/(main)/agents/agent-builder/display-utils';
import type { DraftKnowledge, DraftToolset } from '../../types';

const ICON_SIZE = 18;

export function KnowledgeIcon({ source }: { source: Pick<DraftKnowledge, 'kind' | 'connectorType'> }) {
  const type = source.kind === 'collection' ? 'kb' : (source.connectorType || 'generic');
  return (
    <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
      <ConnectorIcon type={type} size={ICON_SIZE} />
    </span>
  );
}

export function ToolsetIcon({ toolset }: { toolset: Pick<DraftToolset, 'iconPath'> }) {
  return (
    <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
      <ThemeableAssetIcon
        {...themeableAssetIconPresets.agentBuilderCategoryRow}
        src={toolset.iconPath || AGENT_TOOLSET_FALLBACK_ICON}
        size={ICON_SIZE}
        fallbackSrc={AGENT_TOOLSET_FALLBACK_ICON}
      />
    </span>
  );
}
