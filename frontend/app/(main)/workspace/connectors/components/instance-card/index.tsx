'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, IconButton, Avatar, Switch, Tooltip, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { useUserDirectoryEntry } from '@/lib/hooks/use-user-directory-entry';
import { ShareSidebar } from '@/app/components/share';
import { createConnectorShareAdapter } from '../../share-adapter';
import {
  InfoRow,
  DotSeparator,
  ConnectButton,
  SyncButton,
  FullSyncButton,
} from './primitives';
import { InlineEditableName } from '../inline-editable-name';
import { InstanceSyncOperationIndicator, InstanceSetupStatusRow } from './instance-status-badges';
import { deriveSyncStatusState, getSyncStrategyLabel, getSyncIntervalLabel } from './utils';
import { CONNECTOR_INSTANCE_STATUS } from '../../constants';
import { usePollInstanceWhileSyncing } from '../../utils/use-poll-instance-while-syncing';
import type { ConnectorInstance, ConnectorConfig, ConnectorScope } from '../../types';

// ========================================
// Props
// ========================================

interface InstanceCardProps {
  instance: ConnectorInstance;
  /** Scope: team or personal */
  scope: ConnectorScope;
  /** Per-instance config from GET /connectors/{id}/config */
  config?: ConnectorConfig;
  onManage?: (instance: ConnectorInstance) => void;
  /** POST …/toggle — flips sync `isActive` */
  onToggleSyncActive?: (instance: ConnectorInstance) => void | Promise<void>;
  onChevronClick?: (instance: ConnectorInstance) => void;
  /** Refetch instance row + config shown on this card */
  onRefresh?: () => void | Promise<void>;
  /** Parent-driven refresh in progress (e.g. per-card or refresh-all) */
  isRefreshing?: boolean;
  /** Called when the user confirms leaving a shared connector. */
  onLeave?: (instance: ConnectorInstance) => void | Promise<void>;
}

// ========================================
// InstanceCard
// ========================================

export function InstanceCard({
  instance,
  scope,
  config,
  onManage,
  onToggleSyncActive,
  onChevronClick,
  onRefresh,
  isRefreshing = false,
  onLeave,
}: InstanceCardProps) {
  const { t } = useTranslation();

  // ── Org/User identity state ──
  const [identityName, setIdentityName] = useState<string>(instance.name);
  // ── Personal header: optional user avatar from updatedBy (title uses instance name) ──
  const [identityIcon, setIdentityIcon] = useState<string | null>(null);
  const [identityIconError, setIdentityIconError] = useState(false);
  const [enabledByName, setEnabledByName] = useState<string | null>(null);
  const [enabledByAvatar, setEnabledByAvatar] = useState<string | null>(null);
  const [syncToggleBusy, setSyncToggleBusy] = useState(false);
  const [isShareSidebarOpen, setIsShareSidebarOpen] = useState(false);
  const [leaveBusy, setLeaveBusy] = useState(false);

  // Share adapter — only created when needed (owner sharing)
  const shareAdapter = useMemo(() => {
    if (!instance._key) return null;
    return createConnectorShareAdapter(instance._key);
  }, [instance._key]);

  // This card is in "shared-with-me" mode when scope is 'shared'
  const isSharedWithMe = scope === 'shared';
  // Personal-scope connectors always belong to the current user — no cross-ID comparison needed.
  const isOwner = scope === 'personal';

  const updatedByEntry = useUserDirectoryEntry(instance.updatedBy);

  usePollInstanceWhileSyncing(instance._key, instance.status);

  useEffect(() => {
    setIdentityIconError(false);
    if (scope === 'personal') {
      setIdentityIcon(null);
    }
    if (!instance.updatedBy) {
      setIdentityIcon(null);
      setEnabledByName(null);
      setEnabledByAvatar(null);
      return;
    }
    if (!updatedByEntry) {
      setEnabledByName(null);
      setEnabledByAvatar(null);
      return;
    }

    const { fullName, resolvedUserId } = updatedByEntry;
    if (scope === 'personal' && resolvedUserId) {
      setIdentityIcon(`/api/v1/users/${resolvedUserId}/dp`);
    }

    const parts = fullName.trim().split(/\s+/);
    const displayName =
      parts.length > 1 ? `${parts[0]} ${parts[parts.length - 1][0]}` : parts[0] || '';
    setEnabledByName(displayName);
    if (resolvedUserId) setEnabledByAvatar(`/api/v1/users/${resolvedUserId}/dp`);
  }, [scope, instance.updatedBy, updatedByEntry]);

  const { status: effectiveStatus, oauthAuthIncompleteForSync: oauthAuthIncomplete } =
    deriveSyncStatusState(instance, undefined, config);
  const syncStrategy = getSyncStrategyLabel(config);
  const syncInterval = getSyncIntervalLabel(config);
  const lastSynced = formatRelativeTime(instance.updatedAtTimestamp);

  const canToggleSync =
    Boolean(instance._key) &&
    instance.supportsSync &&
    instance.isConfigured &&
    !oauthAuthIncomplete &&
    instance.status !== CONNECTOR_INSTANCE_STATUS.DELETING;

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
      <Flex direction="column" gap="4" style={{ padding: 16, minWidth: 325 }}>
        <Flex align="center" gap="2" style={{ width: '100%' }}>
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

          {/* Instance display name — inline editable (read-only in shared mode) */}
          {isSharedWithMe ? (
            <Text
              size="2"
              weight="medium"
              truncate
              style={{ flex: 1, overflow: 'hidden', color: 'var(--gray-12)' }}
            >
              {instance.name?.trim() || instance.type}
            </Text>
          ) : (
            <InlineEditableName
              connectorId={instance._key}
              name={instance.name?.trim() || instance.type}
              textSize="2"
              textWeight="medium"
              truncate
              style={{ flex: 1, overflow: 'hidden' }}
            />
          )}

          {onRefresh ? (
            <IconButton
              variant="outline"
              color="gray"
              size="1"
              aria-label={t('workspace.connectors.refreshInstance')}
              disabled={isRefreshing}
              onClick={() => {
                if (!isRefreshing) void onRefresh();
              }}
              style={{
                cursor: isRefreshing ? 'wait' : 'pointer',
                borderRadius: 'var(--radius-2)',
                width: 32,
                height: 32,
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  animation: isRefreshing ? 'spin 0.8s linear infinite' : undefined,
                }}
              >
                <MaterialIcon name="refresh" size={18} color="var(--gray-11)" />
              </span>
            </IconButton>
          ) : null}

          {/* Chevron — hidden when no handler provided (e.g. shared cards without navigation) */}
          {onChevronClick && (
            <IconButton
              variant="outline"
              color="gray"
              size="1"
              onClick={() => onChevronClick(instance)}
              style={{ cursor: 'pointer', borderRadius: 'var(--radius-2)', width: 32, height: 32, flexShrink: 0 }}
            >
              <MaterialIcon name="chevron_right" size={18} color="var(--gray-11)" />
            </IconButton>
          )}
        </Flex>

        <div style={{ height: 1, backgroundColor: 'var(--gray-a3)' }} />

        <InstanceSetupStatusRow instance={instance} config={config} />

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

        {/* ── Enabled By / Shared By ── */}
        {isSharedWithMe ? (
          /* For shared connectors show who granted access, not the updatedBy creator */
          instance.sharedBy?.name ? (
            <Flex align="center" gap="4">
              <Text
                size="1"
                weight="medium"
                style={{ color: 'var(--gray-10)', width: 164, flexShrink: 0, textTransform: 'uppercase', letterSpacing: '0.04px', lineHeight: '16px' }}
              >
                {t('workspace.connectors.sharedCard.sharedBy')}
              </Text>
              <Flex align="center" gap="2">
                <Avatar
                  size="1"
                  radius="small"
                  fallback={instance.sharedBy.name[0] ?? '?'}
                  style={{ width: 24, height: 24 }}
                />
                <Text size="2" style={{ color: 'var(--gray-12)' }}>
                  {instance.sharedBy.name}
                </Text>
              </Flex>
            </Flex>
          ) : (
            <InfoRow label={t('workspace.connectors.sharedCard.sharedBy')} value="-" />
          )
        ) : enabledByName ? (
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
            </Flex>
          </Flex>
        ) : (
          <InfoRow label={t('workspace.connectors.settingsTab.enabledBy')} value="-" />
        )}

        <InfoRow label="LAST SYNCED" value={lastSynced} />

        {/* Enable / pause connector sync (backend toggle) — hidden for shared-with-me. */}
        {!isSharedWithMe && instance._key && instance.supportsSync && (
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


        {/* Actions row: sync/indexing buttons (left) + Share/Leave (right). */}
        {((!isSharedWithMe && showIndexingActions) || isOwner || isSharedWithMe) && (
          <Flex
            align="center"
            justify="between"
            gap="2"
            style={{
              marginTop: 4,
              paddingTop: 12,
              borderTop: '1px solid var(--gray-a3)',
            }}
          >
            {/* Sync / indexing buttons — left side, hidden for shared-with-me */}
            {!isSharedWithMe && showIndexingActions ? (
              <Flex wrap="wrap" gap="2" align="center">
                <SyncButton connectorId={instance._key} connectorType={instance.type} />
                <FullSyncButton connectorId={instance._key} connectorType={instance.type} />
                <InstanceSyncOperationIndicator instance={instance} />
              </Flex>
            ) : (
              <div />
            )}

            {/* Share (owner) or Leave (shared-with-me) — right side */}
            <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
              {isOwner && instance._key && (
                <Button
                  variant="soft"
                  color="gray"
                  size="1"
                  onClick={() => setIsShareSidebarOpen(true)}
                  style={{ cursor: 'pointer' }}
                >
                  <MaterialIcon name="share" size={14} color="var(--gray-11)" />
                  {t('action.share')}
                </Button>
              )}
              {isSharedWithMe && (
                <Button
                  variant="soft"
                  color="red"
                  size="1"
                  disabled={leaveBusy}
                  onClick={async () => {
                    if (!onLeave || leaveBusy) return;
                    setLeaveBusy(true);
                    try {
                      await onLeave(instance);
                    } finally {
                      setLeaveBusy(false);
                    }
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  <MaterialIcon name="exit_to_app" size={14} color="var(--red-11)" />
                  {t('workspace.connectors.sharedCard.leave')}
                </Button>
              )}
            </Flex>
          </Flex>
        )}
      </Flex>

      {/* ── Auth incomplete banner ── */}
      {!isSharedWithMe && effectiveStatus === 'auth_incomplete' && (
        <Flex
          align="center"
          justify="between"
          style={{ padding: 'var(--space-3) var(--space-4)', borderTop: '1px solid var(--gray-a4)', backgroundColor: 'var(--gray-a2)' }}
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

      {/* Share sidebar — only for owners */}
      {isOwner && shareAdapter && (
        <ShareSidebar
          open={isShareSidebarOpen}
          onOpenChange={setIsShareSidebarOpen}
          adapter={shareAdapter}
        />
      )}
    </Flex>
  );
}
