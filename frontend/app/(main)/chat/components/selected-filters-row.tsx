'use client';

import React from 'react';
import { SelectedCollections, type SelectedCollectionsProps } from '@/chat/components/selected-collections';
import { SelectedToolChips, type SelectedToolGroup } from '@/chat/components/selected-tool-chips';
import type { ChatKnowledgeFilters } from '@/chat/types';

export interface SelectedFiltersRowProps extends SelectedCollectionsProps {
  /**
   * The composer's own knowledge scope, which these pills show. Unset when the
   * pills show a saved agent's or a project's scope, or a message being regenerated.
   */
  selection?: ChatKnowledgeFilters;
  onSelectionChange?: (next: ChatKnowledgeFilters) => void;
  /** The actions and MCP tools picked by hand, one group per instance (none while every tool is on). */
  toolGroups?: SelectedToolGroup[];
  onRemoveToolGroup?: (instanceId: string) => void;
}

/** The composer's row of selected-source pills, then the tools picked, in one scrolling strip. Editions plug in via config.ts. */
export function SelectedFiltersRow({
  selection: _selection,
  onSelectionChange: _onSelectionChange,
  toolGroups,
  onRemoveToolGroup,
  ...pills
}: SelectedFiltersRowProps) {
  return (
    <SelectedCollections
      {...pills}
      trailing={
        toolGroups?.length ? (
          <SelectedToolChips groups={toolGroups} onRemove={pills.removable ? onRemoveToolGroup : undefined} />
        ) : undefined
      }
    />
  );
}
