'use client';

import { useTranslation } from 'react-i18next';
import { Flex, Heading, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { McpMyServerEntry, McpToolInfo } from '../../types';
import { McpInstanceRow } from './mcp-instance-row';

// ========================================
// Props
// ========================================

/** Minimal shape shared by catalog templates and custom (non-catalog) instances,
 * so this layout can render a details page for either. */
export interface McpServerDetailsHeader {
  typeId: string | null;
  displayName: string;
  description: string;
  icon?: string | null;
}

interface McpServerDetailsLayoutProps {
  header: McpServerDetailsHeader | null;
  instances: McpMyServerEntry[];
  isLoading: boolean;
  onBack: () => void;
  onAddInstance: () => void;
  onManageInstance: (instance: McpMyServerEntry) => void;
  onDeleteInstance: (instance: McpMyServerEntry) => void;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onDisconnect: (instance: McpMyServerEntry) => void;
  busyInstanceId?: string | null;
  onRefreshAll?: () => void | Promise<void>;
  isRefreshingAll?: boolean;
  /** Per-instance discovered tools state, keyed by instance id. */
  toolsState: Record<string, { tools: McpToolInfo[]; error?: string; loading: boolean } | undefined>;
  onDiscoverTools: (instance: McpMyServerEntry) => void;
}

// ========================================
// Component
// ========================================

export function McpServerDetailsLayout({
  header,
  instances,
  isLoading,
  onBack,
  onAddInstance,
  onManageInstance,
  onDeleteInstance,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
  busyInstanceId,
  onRefreshAll,
  isRefreshingAll = false,
  toolsState,
  onDiscoverTools,
}: McpServerDetailsLayoutProps) {
  const { t } = useTranslation();

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
      {/* Breadcrumb */}
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
            {t('workspace.mcpServers.details.breadcrumbLabel')}
          </Text>
        </button>
        <MaterialIcon name="chevron_right" size={14} color="var(--gray-9)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {header?.displayName ?? ''}
        </Text>
      </Flex>

      {/* Header: icon + name + actions */}
      <Flex justify="between" align="start" gap="4" style={{ width: '100%' }}>
        <Flex align="center" gap="4">
          <HeaderIcon header={header} />
          <Flex direction="column" gap="1">
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {header?.displayName ?? ''}
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {header?.description ?? ''}
            </Text>
          </Flex>
        </Flex>

        <Flex align="center" gap="2">
          {onRefreshAll && instances.length > 0 ? (
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              disabled={isRefreshingAll || isLoading}
              onClick={() => void onRefreshAll()}
              style={{
                cursor: isRefreshingAll ? 'wait' : 'pointer',
                gap: 'var(--space-1)',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  animation: isRefreshingAll ? 'spin 0.8s linear infinite' : undefined,
                }}
              >
                <MaterialIcon name="refresh" size={16} color="var(--gray-11)" />
              </span>
              {t('workspace.mcpServers.details.refreshAll')}
            </Button>
          ) : null}
          <Button
            variant="solid"
            size="2"
            onClick={onAddInstance}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="add" size={16} color="white" />
            {t('workspace.mcpServers.details.addInstance')}
          </Button>
        </Flex>
      </Flex>

      {/* Instance list */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
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
            {t('workspace.mcpServers.details.noInstances')}
          </Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="4">
          {instances.map((instance) => (
            <McpInstanceRow
              key={instance._id}
              instance={instance}
              isBusy={busyInstanceId === instance._id}
              onManage={() => onManageInstance(instance)}
              onDelete={() => onDeleteInstance(instance)}
              onAuthenticate={() => onAuthenticate(instance)}
              onReauthenticate={() => onReauthenticate(instance)}
              onDisconnect={() => onDisconnect(instance)}
              toolsResult={toolsState[instance._id]}
              onDiscoverTools={() => onDiscoverTools(instance)}
            />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function HeaderIcon({ header }: { header: McpServerDetailsHeader | null }) {
  if (!header) return null;
  return (
    <Flex
      align="center"
      justify="center"
      style={{
        width: 48,
        height: 48,
        padding: 10,
        backgroundColor: 'var(--gray-a2)',
        borderRadius: 'var(--radius-2)',
        flexShrink: 0,
        overflow: 'hidden',
      }}
    >
      {header.icon ? (
        <img src={header.icon} alt="" width={28} height={28} />
      ) : (
        <MaterialIcon name="hub" size={28} color="var(--gray-10)" />
      )}
    </Flex>
  );
}
