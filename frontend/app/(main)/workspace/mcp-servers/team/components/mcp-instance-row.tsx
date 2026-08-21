'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Avatar, Badge, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { apiClient } from '@/lib/api';
import { isMcpInstanceReadOnly, McpInheritedBadge } from '@/config';
import type { McpMyServerEntry, McpToolInfo } from '../../types';
import { MCP_AUTH_MODE_LABELS, MCP_TRANSPORT_LABELS } from '../../types';

// ========================================
// Props
// ========================================

interface McpInstanceRowProps {
  instance: McpMyServerEntry;
  isBusy: boolean;
  onManage: () => void;
  onDelete: () => void;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
  onDisconnect: () => void;
  toolsResult?: { tools: McpToolInfo[]; error?: string; loading: boolean };
  onDiscoverTools: () => void;
}

// ========================================
// Component
// ========================================

export function McpInstanceRow({
  instance,
  isBusy,
  onManage,
  onDelete,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
  toolsResult,
  onDiscoverTools,
}: McpInstanceRowProps) {
  const { t } = useTranslation();
  const [toolsExpanded, setToolsExpanded] = useState(false);

  const managedByAdmin = instance.useAdminAuth;
  const needsAuth = instance.authMode !== 'none' && !managedByAdmin;
  const isReady = !needsAuth || instance.isAuthenticated;

  const hasDiscoveredTools = Boolean(toolsResult && !toolsResult.loading && !toolsResult.error && toolsResult.tools.length > 0);

  const isReadOnly = isMcpInstanceReadOnly(instance);
  const [createdByName, setCreatedByName] = useState<string | null>(null);
  const [createdByAvatar, setCreatedByAvatar] = useState<string | null>(null);

  useEffect(() => {
    if (!instance.createdBy || isReadOnly) return;
    let cancelled = false;

    async function fetchCreatedByUser() {
      try {
        const { data } = await apiClient.post('/api/v1/users/by-ids', {
          userIds: [instance.createdBy],
        });
        if (cancelled) return;

        const users = Array.isArray(data) ? data : data.users ?? [];
        if (users.length > 0) {
          const user = users[0] as Record<string, unknown>;
          const fullName = (user.name as string) ?? (user.fullName as string) ?? '';
          const userId = (user.id as string) ?? (user._id as string) ?? instance.createdBy;
          setCreatedByName(fullName.trim() || instance.createdBy);
          if (userId) setCreatedByAvatar(`/api/v1/users/${userId}/dp`);
        }
      } catch {
        /* ignore — fall back to the raw id */
      }
    }

    void fetchCreatedByUser();
    return () => {
      cancelled = true;
    };
  }, [instance.createdBy, isReadOnly]);

  return (
    <Flex
      direction="column"
      style={{
        backgroundColor: 'var(--gray-2)',
        border: '1px solid var(--gray-3)',
        borderRadius: 'var(--radius-4)',
        overflow: 'hidden',
      }}
    >
      <Flex direction="column" gap="4" style={{ padding: 16, minWidth: 325 }}>
        {/* Header row */}
        <Flex align="center" gap="2" style={{ width: '100%' }}>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: 8,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-2)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name={instance.isCustom ? 'dns' : 'hub'} size={16} color="var(--gray-10)" />
          </Flex>

          <Text size="2" weight="medium" style={{ flex: 1, minWidth: 0, color: 'var(--gray-12)' }}>
            {instance.name}
          </Text>

          {instance.isCustom && (
            <Badge color="gray" size="1">
              {t('workspace.mcpServers.custom')}
            </Badge>
          )}

          <McpInheritedBadge instance={instance} />

          <IconButton
            variant="outline"
            color="red"
            size="1"
            disabled={isReadOnly}
            title={isReadOnly ? t('workspace.mcpServers.deleteDisabledTooltip', 'This server cannot be deleted') : undefined}
            aria-label={t('workspace.mcpServers.cta.delete')}
            onClick={onDelete}
            style={{ cursor: isReadOnly ? 'not-allowed' : 'pointer', borderRadius: 'var(--radius-2)', width: 32, height: 32, flexShrink: 0 }}
          >
            <MaterialIcon name="delete" size={16} color="var(--red-11)" />
          </IconButton>

          <IconButton
            variant="outline"
            color="gray"
            size="1"
            aria-label={t('workspace.mcpServers.cta.manage')}
            onClick={onManage}
            style={{ cursor: 'pointer', borderRadius: 'var(--radius-2)', width: 32, height: 32, flexShrink: 0 }}
          >
            <MaterialIcon name="chevron_right" size={18} color="var(--gray-11)" />
          </IconButton>
        </Flex>

        <div style={{ height: 1, backgroundColor: 'var(--gray-a3)' }} />

        {/* CONFIGURATION */}
        <InfoRow label={t('workspace.mcpServers.details.configuration')}>
          {isReady ? (
            <Badge color="green" size="1">
              {t('workspace.mcpServers.status.ready')}
            </Badge>
          ) : (
            <Badge color="amber" size="1">
              {t('workspace.mcpServers.status.needsAuth')}
            </Badge>
          )}
        </InfoRow>

        {/* TRANSPORT */}
        <InfoRow label={t('workspace.mcpServers.details.transport')}>
          <Text size="2" style={{ color: 'var(--gray-12)' }}>
            {MCP_TRANSPORT_LABELS[instance.transport]}
          </Text>
          <DotSeparator />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {MCP_AUTH_MODE_LABELS[instance.authMode]}
          </Text>
          {managedByAdmin && (
            <>
              <DotSeparator />
              <Text size="2" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.mcpServers.sharedAuth')}
              </Text>
            </>
          )}
        </InfoRow>

        {/* CREATED BY — hidden when the instance is read-only */}
        {!isReadOnly && (
          <InfoRow label={t('workspace.mcpServers.details.createdBy', { defaultValue: 'Created by' })}>
            <Avatar
              size="1"
              src={createdByAvatar ?? undefined}
              fallback={createdByName?.[0] ?? '?'}
              radius="full"
            />
            <Text size="2" style={{ color: 'var(--gray-12)' }}>
              {createdByName ?? instance.createdBy}
            </Text>
          </InfoRow>
        )}

        {/* TOOLS */}
        <InfoRow label={t('workspace.mcpServers.details.tools')}>
          {!isReady ? (
            <Text size="2" style={{ color: 'var(--gray-9)' }}>
              {t('workspace.mcpServers.details.discoveryNeedsAuth')}
            </Text>
          ) : toolsResult?.loading ? (
            <Text size="2" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.details.discovering')}
            </Text>
          ) : toolsResult?.error ? (
            <Flex align="center" gap="2">
              <Text size="2" style={{ color: 'var(--red-11)' }}>
                {t('workspace.mcpServers.details.discoveryError')}
              </Text>
              <RetryButton onClick={onDiscoverTools} />
            </Flex>
          ) : hasDiscoveredTools ? (
            <button
              type="button"
              onClick={() => setToolsExpanded((v) => !v)}
              style={{
                appearance: 'none',
                margin: 0,
                padding: 0,
                border: 'none',
                background: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Text size="2" style={{ color: 'var(--accent-11)' }}>
                {t('workspace.mcpServers.details.discoveredTools', { count: toolsResult?.tools.length ?? 0 })}
              </Text>
              <MaterialIcon
                name={toolsExpanded ? 'expand_less' : 'expand_more'}
                size={16}
                color="var(--accent-11)"
              />
            </button>
          ) : (
            <DiscoverToolsButton onClick={onDiscoverTools} />
          )}
        </InfoRow>

        {toolsExpanded && hasDiscoveredTools && toolsResult && (
          <Flex
            direction="column"
            gap="2"
            style={{
              marginLeft: 164,
              padding: 'var(--space-3)',
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-3)',
              maxHeight: 280,
              overflowY: 'auto',
            }}
          >
            {toolsResult.tools.map((tool) => (
              <Flex key={tool.namespacedName || tool.name} direction="column" gap="0">
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                  {tool.name}
                </Text>
                {tool.description && (
                  <Text size="1" style={{ color: 'var(--gray-10)' }}>
                    {tool.description}
                  </Text>
                )}
              </Flex>
            ))}
          </Flex>
        )}
      </Flex>

      {needsAuth && (
        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            borderTop: '1px solid var(--gray-a4)',
            backgroundColor: 'var(--gray-a2)',
          }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {instance.isAuthenticated
                ? t('workspace.mcpServers.details.authBannerAuthenticatedTitle')
                : t('workspace.mcpServers.details.authBannerTitle')}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {instance.isAuthenticated
                ? t('workspace.mcpServers.details.authBannerAuthenticatedDescription')
                : t('workspace.mcpServers.details.authBannerDescription')}
            </Text>
          </Flex>

          <Flex align="center" gap="2">
            {instance.isAuthenticated ? (
              <>
                <ActionButton
                  icon="autorenew"
                  label={t('workspace.mcpServers.cta.reauthenticate')}
                  onClick={onReauthenticate}
                  disabled={isBusy}
                />
                <ActionButton
                  icon="link_off"
                  label={t('workspace.mcpServers.cta.disconnect')}
                  danger
                  onClick={onDisconnect}
                  disabled={isBusy}
                />
              </>
            ) : (
              <ActionButton
                icon="link"
                label={t('workspace.mcpServers.cta.connect')}
                onClick={onAuthenticate}
                disabled={isBusy}
              />
            )}
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Flex align="center" gap="4">
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--gray-10)',
          width: 164,
          flexShrink: 0,
          textTransform: 'uppercase',
          letterSpacing: '0.04px',
          lineHeight: '16px',
        }}
      >
        {label}
      </Text>
      <Flex align="center" gap="3">
        {children}
      </Flex>
    </Flex>
  );
}

function DotSeparator() {
  return (
    <span
      style={{
        width: 3,
        height: 3,
        borderRadius: '50%',
        backgroundColor: 'var(--gray-8)',
        flexShrink: 0,
      }}
    />
  );
}

function DiscoverToolsButton({ onClick }: { onClick: () => void }) {
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
        border: '1px solid var(--accent-a6)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        height: 24,
        padding: '0 10px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--accent-a4)' : 'var(--accent-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name="travel_explore" size={14} color="var(--accent-11)" />
      <span style={{ fontSize: 12, fontWeight: 500, lineHeight: '16px', color: 'var(--accent-11)' }}>
        {t('workspace.mcpServers.details.discoverTools')}
      </span>
    </button>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        appearance: 'none',
        margin: 0,
        padding: 0,
        border: 'none',
        background: 'none',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        gap: 4,
      }}
    >
      <MaterialIcon name="refresh" size={14} color="var(--accent-11)" />
      <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--accent-11)' }}>
        {t('workspace.mcpServers.details.retry')}
      </span>
    </button>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  danger,
  disabled,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const color = danger ? 'var(--red-11)' : 'var(--accent-11)';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: `1px solid ${danger ? 'var(--red-a6)' : 'var(--accent-a6)'}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-2)',
        height: 'var(--space-6)',
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        opacity: disabled ? 0.6 : 1,
        backgroundColor: isHovered && !disabled
          ? danger
            ? 'var(--red-a4)'
            : 'var(--accent-a4)'
          : danger
            ? 'var(--red-a3)'
            : 'var(--accent-a3)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={16} color={color} />
      <span style={{ fontSize: 14, fontWeight: 500, lineHeight: '20px', color }}>{label}</span>
    </button>
  );
}
