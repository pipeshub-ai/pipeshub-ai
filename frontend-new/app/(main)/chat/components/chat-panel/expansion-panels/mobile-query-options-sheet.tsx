'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import type { ModelOverride } from '@/chat/types';
import { CollectionsTab } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab';
import { AgentScopedResourcesPanel } from '@/chat/components/chat-panel/expansion-panels/agent-scoped-resources-panel';
import { UniversalAgentResourcesPanel } from '@/chat/components/chat-panel/expansion-panels/universal-agent-resources-panel';
import { ModelSelectorPanel } from '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import { AgentStrategyModePanel } from '@/chat/components/chat-panel/expansion-panels/agent-strategy-mode-panel';
import type { AgentStrategy, QueryMode } from '@/chat/types';

type ActivePanel = 'root' | 'models' | 'connectors' | 'agent-strategy' | 'agent-resources' | 'universal-agent-resources';

interface MobileQueryOptionsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Agent conversation — meatball menu only exposes model (no connectors row). */
  isAgentChat?: boolean;
  /** Agent ID for filtering models to only those configured for the agent */
  agentId?: string | null;
}

/**
 * Mobile-only bottom sheet that houses query setting controls collapsed
 * behind the meatball (more_horiz) button in the chat toolbar.
 *
 * Navigation is state-driven: tapping a row slides into a sub-panel
 * (models, connectors, or agent strategy), and the back chevron returns
 * to the root list. All content renders inside a single MobileBottomSheet.
 */
export function MobileQueryOptionsSheet({
  open,
  onOpenChange,
  isAgentChat = false,
  agentId,
}: MobileQueryOptionsSheetProps) {
  const [activePanel, setActivePanel] = useState<ActivePanel>('root');
  const { t } = useTranslation();

  const settings = useChatStore((s) => s.settings);
  const setAgentStrategy = useChatStore((s) => s.setAgentStrategy);
  const setFilters = useChatStore((s) => s.setFilters);
  const setSelectedModelForCtx = useChatStore((s) => s.setSelectedModelForCtx);

  const modelCtxKey = ctxKeyFromAgent(agentId);
  const contextSelectedModel = settings.selectedModels[modelCtxKey] ?? null;
  const contextDefaultModel = settings.defaultModels[modelCtxKey] ?? null;
  const handleModelSelect = (model: ModelOverride | null) =>
    setSelectedModelForCtx(modelCtxKey, model);

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
    connectors: t('chat.connectorsCollectionsTitle', { defaultValue: 'Connectors and Collections' }),
    'agent-strategy': t('chat.agentStrategy.triggerTitle', { defaultValue: 'Agent mode' }),
    'agent-resources': t('chat.agentResources.sheetTitle', {
      defaultValue: 'Connectors, collections & actions',
    }),
    'universal-agent-resources': t('chat.agentResources.sheetTitle', {
      defaultValue: 'Connectors, collections & actions',
    }),
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
          isAgentChat={isAgentChat}
        />
      )}
      {activePanel === 'models' && (
        <ModelSelectorPanel
          selectedModel={contextSelectedModel ?? contextDefaultModel}
          onModelSelect={handleModelSelect}
          hideHeader
          agentId={agentId}
        />
      )}
      {activePanel === 'connectors' && (
        <CollectionsTab
          apps={settings.filters?.apps ?? []}
          kb={settings.filters?.kb ?? []}
          onSelectionChange={(next) => {
            setFilters({
              ...settings.filters,
              apps: next.apps,
              kb: next.kb,
            });
          }}
        />
      )}
      {activePanel === 'agent-resources' && (
        <Flex
          direction="column"
          style={{ flex: 1, minHeight: 0, maxHeight: 'min(70vh, 520px)', overflow: 'hidden' }}
        >
          <AgentScopedResourcesPanel />
        </Flex>
      )}
      {activePanel === 'universal-agent-resources' && (
        <Flex
          direction="column"
          style={{ flex: 1, minHeight: 0, maxHeight: 'min(70vh, 520px)', overflow: 'hidden' }}
        >
          <UniversalAgentResourcesPanel />
        </Flex>
      )}
      {activePanel === 'agent-strategy' && (
        <AgentStrategyModePanel
          activeStrategy={settings.agentStrategy}
          onSelect={(strategy) => {
            setAgentStrategy(strategy);
            handleBack();
          }}
          hideHeader
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
  isAgentChat?: boolean;
}

function RootPanel({ queryMode, agentStrategy, onNavigate, isAgentChat }: RootPanelProps) {
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
          {!isAgentChat && (queryMode !== 'web-search' && queryMode !== 'agent') ? (
            <ManageRow
              icon="hub"
              label={t('chat.connectorsCollectionsTitle', { defaultValue: 'Connectors and Collections' })}
              onClick={() => onNavigate('connectors')}
            />
          ) : !isAgentChat && queryMode === 'agent' ? (
            <ManageRow
              icon="apps"
              label={t('chat.agentResources.sheetTitle', {
                defaultValue: 'Connectors, collections & actions',
              })}
              onClick={() => onNavigate('universal-agent-resources')}
            />
          ) : isAgentChat ? (
            <ManageRow
              icon="apps"
              label={t('chat.agentResources.sheetTitle', {
                defaultValue: 'Connectors, collections & actions',
              })}
              onClick={() => onNavigate('agent-resources')}
            />
          ) : null}
          {!isAgentChat && queryMode === 'agent' && (
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
