'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useChatStore } from '@/chat/store';
import { CollectionsTab } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab';
import { ModelSelectorPanel } from '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import type { AgentStrategy, QueryMode } from '@/chat/types';

const AGENT_STRATEGIES: AgentStrategy[] = ['auto', 'quick', 'verify', 'deep'];

const AGENT_STRATEGY_ICONS: Record<AgentStrategy, string> = {
  auto: 'auto_awesome',
  quick: 'bolt',
  verify: 'fact_check',
  deep: 'psychology',
};

type ActivePanel = 'root' | 'models' | 'connectors' | 'agent-strategy';

interface MobileQueryOptionsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Mobile-only bottom sheet that houses query setting controls collapsed
 * behind the meatball (more_horiz) button in the chat toolbar.
 *
 * Navigation is state-driven: tapping a row slides into a sub-panel
 * (models, connectors, or agent strategy), and the back chevron returns
 * to the root list. All content renders inside a single MobileBottomSheet.
 */
export function MobileQueryOptionsSheet({ open, onOpenChange }: MobileQueryOptionsSheetProps) {
  const [activePanel, setActivePanel] = useState<ActivePanel>('root');
  const { t } = useTranslation();

  const settings = useChatStore((s) => s.settings);
  const setAgentStrategy = useChatStore((s) => s.setAgentStrategy);
  const setFilters = useChatStore((s) => s.setFilters);
  const setSelectedModel = useChatStore((s) => s.setSelectedModel);

  const handleClose = () => {
    onOpenChange(false);
    // Reset to root after animation completes
    setTimeout(() => setActivePanel('root'), 300);
  };

  const handleBack = () => setActivePanel('root');

  // ── Panel titles ──
  const panelTitles: Record<ActivePanel, string> = {
    root: t('chat.queryOptions', { defaultValue: 'Query options' }),
    models: t('chat.models', { defaultValue: 'Models' }),
    connectors: t('nav.connectors', { defaultValue: 'Connectors' }),
    'agent-strategy': t('chat.agentStrategy.triggerTitle', { defaultValue: 'Agent mode' }),
  };

  return (
    <MobileBottomSheet
      open={open}
      onOpenChange={handleClose}
      title={panelTitles[activePanel]}
      onBack={activePanel !== 'root' ? handleBack : undefined}
    >
      {activePanel === 'root' && (
        <RootPanel
          queryMode={settings.queryMode}
          agentStrategy={settings.agentStrategy}
          onNavigate={setActivePanel}
        />
      )}
      {activePanel === 'models' && (
        <ModelSelectorPanel
          selectedModel={settings.selectedModel}
          onModelSelect={(model) => { setSelectedModel(model); handleBack(); }}
          hideHeader
        />
      )}
      {activePanel === 'connectors' && (
        <CollectionsTab
          selectedKbIds={settings.filters?.kb ?? []}
          onToggleKb={(kbId) => {
            const current = settings.filters?.kb ?? [];
            const next = current.includes(kbId)
              ? current.filter((id) => id !== kbId)
              : [...current, kbId];
            setFilters({ apps: settings.filters?.apps ?? [], kb: next });
          }}
        />
      )}
      {activePanel === 'agent-strategy' && (
        <AgentStrategyPanel
          agentStrategy={settings.agentStrategy}
          onSelect={(strategy) => {
            setAgentStrategy(strategy);
            handleBack();
          }}
        />
      )}
    </MobileBottomSheet>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Root panel
// ────────────────────────────────────────────────────────────────────────────

interface RootPanelProps {
  queryMode: QueryMode;
  agentStrategy: AgentStrategy;
  onNavigate: (panel: ActivePanel) => void;
}

function RootPanel({ queryMode, agentStrategy, onNavigate }: RootPanelProps) {
  const { t } = useTranslation();

  const currentStrategyLabel = t(`chat.agentStrategy.modes.${agentStrategy}.label`);

  return (
    <Flex direction="column" gap="5">
      {/* Manage section */}
      <Flex direction="column" gap="3">
        <Text size="1" weight="medium" style={{ color: 'var(--gray-11)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {t('chat.manage', { defaultValue: 'Manage' })}
        </Text>
        <Flex direction="column" gap="2">
          <ManageRow
            icon="memory"
            label={t('chat.models', { defaultValue: 'Models' })}
            onClick={() => onNavigate('models')}
          />
          <ManageRow
            icon="hub"
            label={t('nav.connectors', { defaultValue: 'Connectors' })}
            onClick={() => onNavigate('connectors')}
          />
          {queryMode === 'agent' && (
            <ManageRow
              icon="smart_toy"
              label={t('chat.agentStrategy.triggerTitle', { defaultValue: 'Agent mode' })}
              subtitle={currentStrategyLabel}
              onClick={() => onNavigate('agent-strategy')}
            />
          )}
        </Flex>
      </Flex>
    </Flex>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Agent strategy sub-panel
// ────────────────────────────────────────────────────────────────────────────

interface AgentStrategyPanelProps {
  agentStrategy: AgentStrategy;
  onSelect: (id: AgentStrategy) => void;
}

function AgentStrategyPanel({ agentStrategy, onSelect }: AgentStrategyPanelProps) {
  const { t } = useTranslation();

  return (
    <Flex direction="column" gap="2">
      {AGENT_STRATEGIES.map((id) => (
        <AgentStrategyRow
          key={id}
          id={id}
          label={t(`chat.agentStrategy.modes.${id}.label`)}
          hint={t(`chat.agentStrategy.modes.${id}.hint`)}
          selected={agentStrategy === id}
          onSelect={onSelect}
        />
      ))}
    </Flex>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Agent strategy selectable row
// ────────────────────────────────────────────────────────────────────────────

interface AgentStrategyRowProps {
  id: AgentStrategy;
  label: string;
  hint: string;
  selected: boolean;
  onSelect: (id: AgentStrategy) => void;
}

function AgentStrategyRow({ id, label, hint, selected, onSelect }: AgentStrategyRowProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="3"
      onClick={() => onSelect(id)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-1)',
        border: `1px solid ${selected ? 'var(--accent-6)' : 'var(--olive-3)'}`,
        backgroundColor: selected
          ? 'var(--accent-2)'
          : isHovered
            ? 'var(--olive-3)'
            : 'var(--olive-2)',
        cursor: 'pointer',
        transition: 'background-color 0.12s ease, border-color 0.12s ease',
      }}
    >
      <MaterialIcon
        name={AGENT_STRATEGY_ICONS[id]}
        size={ICON_SIZES.PRIMARY}
        color={selected ? 'var(--accent-9)' : 'var(--gray-11)'}
      />
      <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: selected ? 'var(--accent-11)' : 'var(--gray-12)' }}>
          {label}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.4 }}>
          {hint}
        </Text>
      </Flex>
      {selected && (
        <MaterialIcon name="check_circle" size={ICON_SIZES.PRIMARY} color="var(--accent-9)" />
      )}
    </Flex>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Manage row item
// ────────────────────────────────────────────────────────────────────────────

interface ManageRowProps {
  icon: string;
  label: string;
  subtitle?: string;
  onClick?: () => void;
  disabled?: boolean;
}

function ManageRow({ icon, label, subtitle, onClick, disabled }: ManageRowProps) {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <Flex
      align="center"
      justify="between"
      onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: isHovered && !disabled ? 'var(--olive-3)' : 'var(--olive-2)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background-color 0.12s ease',
      }}
    >
      <Flex align="center" gap="3">
        <MaterialIcon name={icon} size={ICON_SIZES.PRIMARY} color="var(--gray-11)" />
        <Flex direction="column" gap="0">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {label}
          </Text>
          {subtitle && (
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {subtitle}
            </Text>
          )}
        </Flex>
      </Flex>
      <MaterialIcon name="chevron_right" size={ICON_SIZES.PRIMARY} color="var(--gray-9)" />
    </Flex>
  );
}
