'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Text } from '@radix-ui/themes';
import { ApiToolsetsApi } from '../../../workspace/api-toolsets/api';
import { ApiOperationsChecklist } from '../../../workspace/api-toolsets/components/api-operations-checklist';
import { usableByServiceAccount } from '../../../workspace/api-toolsets/toolset-form';
import type { ApiToolset } from '../../../workspace/api-toolsets/types';
import { attachedToolNames, toggleAttachment } from '../api-toolset-attachments';
import { SidebarCategoryRow } from './sidebar-category-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';

const WORKSPACE_PAGE = '/workspace/api-toolsets/';

const hintStyle: React.CSSProperties = { color: 'var(--slate-11)', padding: '4px 8px', display: 'block' };

/**
 * API toolset operations are picked with checkboxes here rather than dragged onto the canvas:
 * they are a flat list on the agent (`apiToolsetTools`), with no per-node settings to show.
 */
export function AgentBuilderApiToolsetsSection(props: {
  attached: string[];
  onChange: (entries: string[]) => void;
  isServiceAccount: boolean;
  structureLocked: boolean;
}) {
  const { attached, onChange, isServiceAccount, structureLocked } = props;
  const { t } = useTranslation();
  const [toolsets, setToolsets] = useState<ApiToolset[] | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    ApiToolsetsApi.listInstances()
      .then((list) => !cancelled && setToolsets(list))
      .catch(() => !cancelled && setToolsets([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (toolsets === null) return <AgentBuilderPaletteSkeletonList count={2} />;

  if (toolsets.length === 0) {
    return (
      <Text size="1" style={{ ...hintStyle, fontStyle: 'italic' }}>
        {t('agentBuilder.apiToolsets.none')}
      </Text>
    );
  }

  return (
    <Box>
      {toolsets.map((toolset) => {
        const selected = attachedToolNames(attached, toolset.id);
        const blocked = isServiceAccount && !usableByServiceAccount(toolset);
        const isExpanded = expanded[toolset.id] ?? selected.size > 0;
        return (
          <SidebarCategoryRow
            key={toolset.id}
            groupLabel={toolset.name}
            groupMaterialIcon="api"
            itemCount={selected.size > 0 ? selected.size : toolset.operations.length}
            isExpanded={isExpanded}
            onToggle={() => setExpanded((p) => ({ ...p, [toolset.id]: !isExpanded }))}
            toolsetStatus={toolset.isConnected ? 'authenticated' : 'needs_authentication'}
            showConfigureIcon={!toolset.isConnected}
            onConfigureClick={() => window.open(WORKSPACE_PAGE, '_blank', 'noopener')}
            configureTooltip={t('agentBuilder.apiToolsets.connectTooltip')}
          >
            {blocked ? (
              <Text size="1" style={{ ...hintStyle, color: 'var(--amber-11)' }}>
                {t('agentBuilder.apiToolsets.serviceAccountBlocked')}
              </Text>
            ) : !toolset.isConnected ? (
              <Text size="1" style={hintStyle}>
                {t('agentBuilder.apiToolsets.notConnected')}
              </Text>
            ) : null}
            <Box px="2">
              <ApiOperationsChecklist
                operations={toolset.operations}
                selected={selected}
                // A blocked toolset can still be cleared, so an agent that became a service account can be saved.
                disabled={structureLocked || (blocked && selected.size === 0)}
                onToggle={(toolName, checked) =>
                  onChange(toggleAttachment(attached, toolsets, toolset.id, toolName, checked && !blocked))
                }
              />
            </Box>
          </SidebarCategoryRow>
        );
      })}
    </Box>
  );
}
