'use client';

import React, { useMemo, type ComponentType } from 'react';
import { useTranslation } from 'react-i18next';
import { Box } from '@radix-ui/themes';
import { EnterpriseFeatureNotice } from '@/app/components/ui/enterprise-badge';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import type { ChatKnowledgeFilters } from '@/chat/types';

/** The selection a chat filter panel edits, as the panel holds it. */
export interface FilterPanelExtrasProps {
  selection: ChatKnowledgeFilters;
  onSelectionChange: (next: ChatKnowledgeFilters) => void;
  /** Closes the filter panel. Unset where the panel has no close of its own. */
  onClose?: () => void;
}

/** One more tab in the chat filter panel, beside Connectors and Collections. */
export interface FilterPanelExtraTab {
  label: string;
  Panel: ComponentType<FilterPanelExtrasProps>;
  /** The filter panels open on this tab instead of Connectors. */
  openFirst?: boolean;
}

function QuickFiltersNotice() {
  const { t } = useTranslation();
  return (
    <Box style={{ padding: 'var(--space-2) 0' }}>
      <EnterpriseFeatureNotice title={t('chat.quickFilters.title')} description={t('chat.quickFilters.description')} />
    </Box>
  );
}

/**
 * The extra filter panel tab: here, what quick filters are and where they are available, for the people
 * that notice is shown to (none otherwise). Editions plug in via config.ts.
 */
export function useFilterPanelExtraTab(): FilterPanelExtraTab | null {
  const { t } = useTranslation();
  const show = useEnterprisePrompt();
  const label = t('chat.quickFilters.tab');
  return useMemo(() => (show ? { label, Panel: QuickFiltersNotice } : null), [show, label]);
}

/** Extras in the chat filter panel's footer. Editions plug in via config.ts. */
export function FilterPanelFooterExtras(_props: FilterPanelExtrasProps) {
  return null;
}
