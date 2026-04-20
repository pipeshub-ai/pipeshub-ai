'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Grid, Heading, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceHeaderIconButton } from '../../components';
import type { SlackBotConfig, AgentOption } from '../types';
import { BotCard } from './bot-card';

const DEFAULT_ASSISTANT_ID = '__default_assistant__';

// ========================================
// Props
// ========================================

interface BotPageLayoutProps {
  configs: SlackBotConfig[];
  agents: AgentOption[];
  isLoading: boolean;
  onCreateBot: () => void;
  onRefresh: () => void;
  onManage: (configId: string) => void;
}

// ========================================
// Component
// ========================================

export function BotPageLayout({
  configs,
  agents,
  isLoading,
  onCreateBot,
  onRefresh,
  onManage,
}: BotPageLayoutProps) {
  const { t } = useTranslation();
  const agentMap = new Map(agents.map((a) => [a.id, a.name]));
  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 64,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      {/* ── Header ── */}
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.bots.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.bots.subtitle')}
          </Text>
        </Flex>

        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <CreateBotButton onClick={onCreateBot} />
          <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} />
          <WorkspaceHeaderIconButton
            icon="open_in_new"
            onClick={() => window.open('https://docs.pipeshub.com/integrations', '_blank')}
          />
        </Flex>
      </Flex>

      {/* ── Content ── */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.bots.loading')} />
        </Flex>
      ) : configs.length === 0 ? (
        <EmptyState onAdd={onCreateBot} />
      ) : (
        <Grid
          columns={{ initial: '2', md: '3', lg: '4' }}
          gap="4"
          style={{ width: '100%' }}
        >
          {configs.map((config) => {
            const isDefault = !config.agentId || config.agentId === DEFAULT_ASSISTANT_ID;
            const agentName = isDefault
              ? t('workspace.bots.defaultAssistant')
              : agentMap.get(config.agentId!) ?? config.agentId;
            return (
              <BotCard
                key={config.id}
                name={config.name}
                botType="slack"
                agentName={agentName}
                onManage={() => onManage(config.id)}
              />
            );
          })}
        </Grid>
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function CreateBotButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        height: 32,
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--accent-10)' : 'var(--accent-9)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name="add" size={16} color="white" />
      <span style={{ fontSize: 14, fontWeight: 500, color: 'white' }}>
        {t('workspace.bots.createBot')}
      </span>
    </button>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      style={{ width: '100%', flex: 1, paddingTop: 80 }}
    >
      <MaterialIcon name="smart_toy" size={48} color="var(--gray-9)" />
      <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {t('workspace.bots.noBots')}
      </Text>
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.bots.setupFirstBot')}
      </Text>
      <button
        type="button"
        onClick={onAdd}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        style={{
          appearance: 'none',
          margin: 0,
          marginTop: 8,
          font: 'inherit',
          outline: 'none',
          border: 'none',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          height: 36,
          padding: '0 16px',
          borderRadius: 'var(--radius-2)',
          backgroundColor: isHovered ? 'var(--accent-10)' : 'var(--accent-9)',
          cursor: 'pointer',
          transition: 'background-color 150ms ease',
        }}
      >
        <MaterialIcon name="add" size={16} color="white" />
        <span style={{ fontSize: 14, fontWeight: 500, color: 'white' }}>
          {t('workspace.bots.addBot')}
        </span>
      </button>
    </Flex>
  );
}
