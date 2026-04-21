'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Heading, Text, Button } from '@radix-ui/themes';
import { ConnectorIcon, MaterialIcon } from '@/app/components/ui';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { InstanceCard } from './instance-card';
import type { Connector, ConnectorInstance, ConnectorConfig, ConnectorStatsResponse, ConnectorScope } from '../types';

// ========================================
// Props
// ========================================

interface ConnectorDetailsLayoutProps {
  /** Connector type info */
  connector: Connector | null;
  /** Scope: team or personal */
  scope: ConnectorScope;
  /** Scope label for breadcrumb */
  scopeLabel: string;
  /** Instances for this connector type */
  instances: ConnectorInstance[];
  /** Per-instance config data from API */
  instanceConfigs?: Record<string, ConnectorConfig>;
  /** Per-instance stats data from API */
  instanceStats?: Record<string, ConnectorStatsResponse['data']>;
  /** Loading state */
  isLoading: boolean;
  /** Navigate back to connectors list */
  onBack: () => void;
  /** Add another instance */
  onAddInstance: () => void;
  /** Open external docs */
  onOpenDocs?: () => void;
  /** Manage a specific instance (open panel) */
  onManageInstance: (instance: ConnectorInstance) => void;
  /** Start syncing an instance */
  onStartSync: (instance: ConnectorInstance) => void;
  /** Enable / disable sync (POST …/toggle with type sync) */
  onToggleSyncActive: (instance: ConnectorInstance) => void | Promise<void>;
  /** Open chevron → management panel */
  onInstanceChevron: (instance: ConnectorInstance) => void;
}

// ========================================
// ConnectorDetailsLayout
// ========================================

export function ConnectorDetailsLayout({
  connector,
  scope,
  scopeLabel,
  instances,
  instanceConfigs,
  instanceStats,
  isLoading,
  onBack,
  onAddInstance,
  onOpenDocs,
  onManageInstance,
  onStartSync,
  onToggleSyncActive,
  onInstanceChevron,
}: ConnectorDetailsLayoutProps) {
  const { t } = useTranslation();
  const connectorName = connector?.name ?? '';

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 32,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
      }}
    >
      {/* ── Breadcrumb ── */}
      <Flex align="center" gap="2">
        <button
          type="button"
          onClick={onBack}
          style={{
            appearance: 'none',
            margin: 0,
            padding: 0,
            border: 'none',
            outline: 'none',
            background: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <MaterialIcon name="arrow_back" size={16} color="var(--gray-11)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {scopeLabel}
          </Text>
        </button>
        <MaterialIcon name="chevron_right" size={14} color="var(--gray-9)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {connectorName}
        </Text>
      </Flex>

      {/* ── Header: icon + name + actions ── */}
      <Flex justify="between" align="start" gap="4" style={{ width: '100%' }}>
        <Flex align="center" gap="4">
          <ConnectorTypeIcon connector={connector} />
          <Flex direction="column" gap="1">
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {connectorName}
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {connector?.appDescription}
            </Text>
          </Flex>
        </Flex>

        <Flex align="center" gap="2">
          {onOpenDocs && (
            <button
              type="button"
              onClick={onOpenDocs}
              style={{
                appearance: 'none',
                margin: 0,
                padding: 8,
                border: '1px solid var(--gray-a4)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'transparent',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
            </button>
          )}
          <Button
            variant="solid"
            size="2"
            onClick={onAddInstance}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="add" size={16} color="white" />
            {t('workspace.connectors.addInstance')}
          </Button>
        </Flex>
      </Flex>

      {/* ── Instance list ── */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.connectors.loadingInstances')} />
        </Flex>
      ) : instances.length === 0 ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap="2"
          style={{ paddingTop: 80 }}
        >
          <MaterialIcon name="hub" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.connectors.noInstances')}
          </Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="4">
          {instances.map((instance) => (
            <InstanceCard
              key={`${instance._key}-${instance.isAuthenticated ? '1' : '0'}`}
              instance={instance}
              scope={scope}
              config={instance._key ? instanceConfigs?.[instance._key] : undefined}
              stats={instance._key ? instanceStats?.[instance._key] : undefined}
              onManage={onManageInstance}
              onStartSync={onStartSync}
              onToggleSyncActive={onToggleSyncActive}
              onChevronClick={onInstanceChevron}
            />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

// ========================================
// ConnectorTypeIcon
// ========================================

function ConnectorTypeIcon({ connector }: { connector: Connector | null }) {
  if (!connector) return null;

  return <ConnectorIcon type={connector.type} size={48} />;
}
