'use client';

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, IconButton, Avatar, Switch, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { apiClient } from '@/lib/api';
import { formatRelativeTime, formatEnabledDate } from '@/lib/utils/formatters';
import { SyncStatusPill } from './sync-status';
import {
  InfoRow,
  DotSeparator,
  ConnectButton,
  SyncButton,
  FullSyncButton,
  ReindexFailedButton,
  ManualIndexButton,
} from './primitives';
import {
  deriveSyncStatusState,
  getSyncStrategyLabel,
  getSyncIntervalLabel,
  getRecordsSelectedInfo,
  getTotalRecords,
  getSyncedRecords,
  getFailedRecords,
  getUnsupportedRecords,
} from './utils';
import { CONNECTOR_INSTANCE_STATUS } from '../../constants';
import type {
  ConnectorInstance,
  ConnectorConfig,
  ConnectorStatsResponse,
  ConnectorScope,
} from '../../types';

// ========================================
// Props
// ========================================

interface InstanceCardProps {
  instance: ConnectorInstance;
  /** Scope: team or personal */
  scope: ConnectorScope;
  /** Per-instance config from GET /connectors/{id}/config */
  config?: ConnectorConfig;
  /** Per-instance stats from GET /knowledgeBase/stats/{id} */
  stats?: ConnectorStatsResponse['data'];
  onManage?: (instance: ConnectorInstance) => void;
  onStartSync?: (instance: ConnectorInstance) => void;
  /** POST …/toggle — flips sync `isActive` */
  onToggleSyncActive?: (instance: ConnectorInstance) => void | Promise<void>;
  onChevronClick?: (instance: ConnectorInstance) => void;
}

// ========================================
// InstanceCard
// ========================================

export function InstanceCard({
  instance,
  scope,
  config,
  stats,
  onManage,
  onStartSync,
  onToggleSyncActive,
  onChevronClick,
}: InstanceCardProps) {
  const { t } = useTranslation();
  // ── Org/User identity state ──
  const [identityName, setIdentityName] = useState<string>(instance.name);
  // ── Personal header: optional creator avatar (title always uses instance name) ──
  const [identityIcon, setIdentityIcon] = useState<string | null>(null);
  const [identityIconError, setIdentityIconError] = useState(false);

  // ── Enabled by user info ──
  const [enabledByName, setEnabledByName] = useState<string | null>(null);
  const [enabledByAvatar, setEnabledByAvatar] = useState<string | null>(null);
  const [syncToggleBusy, setSyncToggleBusy] = useState(false);

  // ── Fetch user info (identity for personal, enabled-by for all) ──
  useEffect(() => {
    setIdentityIconError(false);
    if (scope === 'personal') {
      setIdentityIcon(null);
    }
    if (!instance.createdBy) return;
    let cancelled = false;

    async function fetchUserData() {
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

          if (scope === 'personal' && userId) {
            setIdentityIcon(`/api/v1/users/${userId}/dp`);
          }

          // "Viraj Gawde" → "Viraj G"
          const parts = fullName.trim().split(/\s+/);
          const displayName =
            parts.length > 1 ? `${parts[0]} ${parts[parts.length - 1][0]}` : parts[0] || '';
          setEnabledByName(displayName);
          if (userId) setEnabledByAvatar(`/api/v1/users/${userId}/dp`);
        }
      } catch {
        // Silently fail
      }
    }

    fetchUserData();
    return () => { cancelled = true; };
  }, [scope, instance.createdBy, instance._key]);

  // ── Derived data ──
  const { status: effectiveStatus, oauthAuthIncompleteForSync: oauthAuthIncomplete } =
    deriveSyncStatusState(instance, stats, config);
  const failedCount = stats?.stats?.indexingStatus?.FAILED ?? 0;
  const autoIndexOffCount = stats?.stats?.indexingStatus?.AUTO_INDEX_OFF ?? 0;
  const syncStrategy = getSyncStrategyLabel(config);
  const syncInterval = getSyncIntervalLabel(config);
  const recordsSelected = getRecordsSelectedInfo(config);
  const lastSynced = formatRelativeTime(instance.updatedAtTimestamp);
  const enabledDate = formatEnabledDate(instance.createdAtTimestamp);

  const canToggleSync =
    Boolean(instance._key) &&
    instance.supportsSync &&
    instance.isConfigured &&
    !oauthAuthIncomplete &&
    instance.status !== CONNECTOR_INSTANCE_STATUS.DELETING;

  /** Shown when the sync toggle is disabled for a reason the user can fix or understand. */
  const syncToggleHelp: string | null =
    instance.status === CONNECTOR_INSTANCE_STATUS.DELETING
      ? 'This connector is being removed.'
      : !instance.isConfigured
        ? 'Finish configuration before you can enable sync.'
        : oauthAuthIncomplete
          ? 'Authenticate this connector before you can enable sync.'
          : null;

  const syncSwitchDisabled =
    !canToggleSync || syncToggleBusy || !onToggleSyncActive;

  const showIndexingActions =
    Boolean(instance._key) &&
    instance.isActive &&
    instance.isConfigured &&
    !oauthAuthIncomplete &&
    instance.status !== CONNECTOR_INSTANCE_STATUS.DELETING;

  const syncSwitchControl = (
    <Switch
      size="1"
      checked={instance.isActive}
      disabled={syncSwitchDisabled}
      onCheckedChange={async () => {
        if (!onToggleSyncActive || !instance._key || syncToggleBusy) return;
        setSyncToggleBusy(true);
        try {
          await onToggleSyncActive(instance);
        } finally {
          setSyncToggleBusy(false);
        }
      }}
    />
  );

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
      {/* ── Main content ── */}
      <Flex direction="column" gap="4" style={{ padding: 16, minWidth: 325 }}>
        {/* ── Header row: identity + sync status + chevron ── */}
        <Flex align="center" gap="2" style={{ width: '100%' }}>
          {/* Identity icon */}
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: scope === 'personal' && identityIcon && !identityIconError ? 0 : 8,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-2)',
              flexShrink: 0,
              overflow: 'hidden',
            }}
          >
            {scope === 'personal' && identityIcon && !identityIconError ? (
              <img
                src={identityIcon}
                alt=""
                width={32}
                height={32}
                onError={() => setIdentityIconError(true)}
                style={{ display: 'block', objectFit: 'cover', width: 32, height: 32 }}
              />
            ) : (
              <ConnectorIcon type={instance.type} size={16} />
            )}
          </Flex>

          {/* Instance display name (user-chosen when created), not creator name */}
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--gray-12)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {instance.name?.trim() || instance.type}
          </Text>

          {/* Sync status pill */}
          <SyncStatusPill
            status={effectiveStatus}
            syncedRecords={getSyncedRecords(stats)}
            totalRecords={getTotalRecords(stats)}
            failedRecords={getFailedRecords(stats)}
            unsupportedRecords={getUnsupportedRecords(stats)}
            onStartSync={() => onStartSync?.(instance)}
          />

          {/* Chevron */}
          <IconButton
            variant="outline"
            color="gray"
            size="1"
            onClick={() => onChevronClick?.(instance)}
            style={{ cursor: 'pointer', borderRadius: 'var(--radius-2)', width: 32, height: 32, flexShrink: 0 }}
          >
            <MaterialIcon name="chevron_right" size={18} color="var(--gray-11)" />
          </IconButton>
        </Flex>

        {/* ── Separator ── */}
        <div style={{ height: 1, backgroundColor: 'var(--gray-a3)' }} />

        {/* ── Records Selected ── */}
        <InfoRow
          label={recordsSelected?.label ?? t('workspace.connectors.instanceCard.recordsSelected')}
          value={recordsSelected ? String(recordsSelected.count) : '-'}
        />

        {/* ── Sync Strategy ── */}
        {syncStrategy ? (
          <Flex align="center" gap="4">
            <Text
              size="1"
              weight="medium"
              style={{ color: 'var(--gray-10)', width: 164, flexShrink: 0, textTransform: 'uppercase', letterSpacing: '0.04px', lineHeight: '16px' }}
            >
              {t('workspace.connectors.instanceCard.syncStrategy')}
            </Text>
            <Flex align="center" gap="3">
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {syncStrategy}
              </Text>
              {syncInterval && (
                <>
                  <DotSeparator />
                  <Text size="2" style={{ color: 'var(--gray-11)' }}>
                    {syncInterval}
                  </Text>
                </>
              )}
            </Flex>
          </Flex>
        ) : (
          <InfoRow label={t('workspace.connectors.instanceCard.syncStrategy')} value="-" />
        )}

        {/* ── Enabled By ── */}
        {enabledByName ? (
          <Flex align="center" gap="4">
            <Text
              size="1"
              weight="medium"
              style={{ color: 'var(--gray-10)', width: 164, flexShrink: 0, textTransform: 'uppercase', letterSpacing: '0.04px', lineHeight: '16px' }}
            >
              {t('workspace.connectors.settingsTab.enabledBy')}
            </Text>
            <Flex align="center" gap="3">
              <Flex align="center" gap="2">
                <Avatar
                  size="1"
                  radius="small"
                  src={enabledByAvatar ?? undefined}
                  fallback={enabledByName?.[0] ?? '?'}
                  style={{ width: 24, height: 24 }}
                />
                <Text size="2" style={{ color: 'var(--gray-12)' }}>
                  {enabledByName}
                </Text>
              </Flex>
              {enabledDate && (
                <>
                  <DotSeparator />
                  <Text size="2" style={{ color: 'var(--gray-11)' }}>
                    {enabledDate}
                  </Text>
                </>
              )}
            </Flex>
          </Flex>
        ) : (
          <InfoRow label={t('workspace.connectors.settingsTab.enabledBy')} value="-" />
        )}

        {/* ── Last Synced ── */}
        <InfoRow label="LAST SYNCED" value={lastSynced} />

        {/* Enable / pause connector sync (backend toggle). */}
        {instance._key && instance.supportsSync && (
          <Flex align="center" gap="4" style={{ marginTop: 2 }}>
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
              SYNC ENABLED
            </Text>
            {syncToggleHelp && syncSwitchDisabled ? (
              <Tooltip content={syncToggleHelp} side="top">
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    verticalAlign: 'middle',
                    cursor: 'default',
                  }}
                >
                  {syncSwitchControl}
                </span>
              </Tooltip>
            ) : (
              syncSwitchControl
            )}
          </Flex>
        )}

        {/* Indexing / sync actions — only when sync is enabled. Header Start Sync is hidden for DELETING via deriveSyncStatusState → sync_disabled. */}
        {showIndexingActions && (
          <Flex
            wrap="wrap"
            gap="2"
            align="center"
            style={{
              marginTop: 4,
              paddingTop: 12,
              borderTop: '1px solid var(--gray-a3)',
            }}
          >
            <SyncButton connectorId={instance._key} connectorType={instance.type} />
            <FullSyncButton connectorId={instance._key} connectorType={instance.type} />
            <ReindexFailedButton
              connectorId={instance._key}
              connectorType={instance.type}
              failedCount={failedCount}
            />
            <ManualIndexButton
              connectorId={instance._key}
              connectorType={instance.type}
              autoIndexOffCount={autoIndexOffCount}
            />
          </Flex>
        )}
      </Flex>

      {/* ── Auth incomplete banner ── */}
      {effectiveStatus === 'auth_incomplete' && (
        <Flex
          align="center"
          justify="between"
          style={{ padding: '12px 16px', borderTop: '1px solid var(--gray-a4)', backgroundColor: 'var(--gray-a2)' }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.connectors.instanceCard.authBannerTitle')}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.connectors.instanceCard.authBannerDescription')}
            </Text>
          </Flex>
          <ConnectButton onClick={() => onManage?.(instance)} />
        </Flex>
      )}
    </Flex>
  );
}
