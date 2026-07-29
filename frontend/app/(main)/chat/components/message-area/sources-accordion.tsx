'use client';

import React, { useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { SourcesTab } from './response-tabs/citations/sources-tab';
import { CitationsTab } from './response-tabs/citations/citations-tab';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations/types';
import type { SourcesViewMode } from '../../types';

interface ViewModePillProps {
  label: string;
  count: number;
  isActive: boolean;
  onClick: () => void;
}

function ViewModePill({ label, count, isActive, onClick }: ViewModePillProps) {
  return (
    <Flex
      align="center"
      gap="1"
      onClick={onClick}
      style={{
        cursor: 'pointer',
        padding: '2px var(--space-2)',
        borderRadius: 'var(--radius-full)',
        backgroundColor: isActive ? 'var(--accent-3)' : 'transparent',
        transition: 'background-color 0.15s ease',
      }}
    >
      <Text
        size="1"
        weight={isActive ? 'medium' : 'regular'}
        style={{ color: isActive ? 'var(--accent-11)' : 'var(--slate-a11)' }}
      >
        {label}
      </Text>
      <Text
        size="1"
        style={{ color: isActive ? 'var(--accent-11)' : 'var(--slate-9)' }}
      >
        {count}
      </Text>
    </Flex>
  );
}

interface SourcesAccordionProps {
  citationMaps: CitationMaps;
  callbacks?: CitationCallbacks;
  /** While streaming, counts may still be growing — accordion stays collapsed by default regardless. */
  isStreaming?: boolean;
}

/**
 * Collapsible panel that surfaces a message's sources and citations without
 * a full tab bar — replaces the old Answer/Sources/Citation tabs. Reuses the
 * existing `SourcesTab` / `CitationsTab` list rendering (and therefore
 * `ReferenceCard`) so the citation UI stays a single source of truth.
 */
export function SourcesAccordion({ citationMaps, callbacks, isStreaming = false }: SourcesAccordionProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(false);
  const [viewMode, setViewMode] = useState<SourcesViewMode>('sources');

  const sourcesCount = citationMaps.sourcesOrder.length;
  const citationCount = Object.keys(citationMaps.citationsOrder).length;

  if (sourcesCount === 0 || isStreaming) return null;

  // If sources are cleared while the citations view is active (edge case on
  // regenerate), fall back to the sources view rather than showing an empty panel.
  const effectiveViewMode: SourcesViewMode = viewMode === 'citation' && citationCount === 0 ? 'sources' : viewMode;

  return (
    <Box
      style={{
        marginTop: 'var(--space-3)',
        marginBottom: 'var(--space-2)',
        border: '1px solid var(--slate-a5)',
        borderRadius: 'var(--radius-3)',
        overflow: 'hidden',
      }}
    >
      {/* Header — always visible, toggles expand/collapse */}
      <Flex
        align="center"
        justify="between"
        onClick={() => setIsExpanded((prev) => !prev)}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          backgroundColor: 'var(--olive-a2)',
        }}
      >
        <Flex align="center" gap="2">
          <MaterialIcon name="link" size={ICON_SIZES.SECONDARY} color="var(--slate-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {sourcesCount} {t('chat.sources')}
          </Text>
        </Flex>
        <MaterialIcon
          name={isExpanded ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}
          size={ICON_SIZES.SECONDARY}
          color="var(--slate-11)"
        />
      </Flex>

      {/* Expanded body — sources/citations sub-toggle + list */}
      {isExpanded && (
        <Box style={{ borderTop: '1px solid var(--slate-a5)', padding: '0 var(--space-3)' }}>
          <Flex align="center" gap="2" style={{ padding: 'var(--space-2) 0' }}>
            <ViewModePill
              label={t('chat.sources')}
              count={sourcesCount}
              isActive={effectiveViewMode === 'sources'}
              onClick={() => setViewMode('sources')}
            />
            {citationCount > 0 && (
              <ViewModePill
                label={t('chat.citation')}
                count={citationCount}
                isActive={effectiveViewMode === 'citation'}
                onClick={() => setViewMode('citation')}
              />
            )}
          </Flex>

          <Box style={{ paddingBottom: 'var(--space-3)' }}>
            {effectiveViewMode === 'sources' ? (
              <SourcesTab citationMaps={citationMaps} callbacks={callbacks} />
            ) : (
              <CitationsTab citationMaps={citationMaps} callbacks={callbacks} />
            )}
          </Box>
        </Box>
      )}
    </Box>
  );
}
