'use client';

import React, { useState, useEffect } from 'react';
import { Flex, Text, IconButton, Avatar } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { apiClient } from '@/lib/api';
import { formatRelativeTime, formatEnabledDate } from '@/lib/utils/formatters';
import { OrgApi } from '../../../general/api';
import { SyncStatusPill } from './sync-status';
import { InfoRow, DotSeparator, ConnectButton, SyncButton } from './primitives';
import {
  deriveSyncStatus,
  getSyncStrategyLabel,
  getSyncIntervalLabel,
  getRecordsSelectedInfo,
  getTotalRecords,
  getSyncedRecords,
  getFailedRecords,
  getUnsupportedRecords,
} from './utils';
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
  onChevronClick,
}: InstanceCardProps) {
  // ── Org/User identity state ──
  const [identityName, setIdentityName] = useState<string>(instance.name);
  const [identityIcon, setIdentityIcon] = useState<string | null>(null);
  const [identityIconError, setIdentityIconError] = useState(false);

  // ── Enabled by user info ──
  const [enabledByName, setEnabledByName] = useState<string | null>(null);
  const [enabledByAvatar, setEnabledByAvatar] = useState<string | null>(null);

  // ── Fetch org logo for team scope (identity name stays as instance.name) ──
  useEffect(() => {
    if (scope !== 'team') return;
    let cancelled = false;

    async function fetchOrgLogo() {
      try {
        const logoUrl = await OrgApi.getLogoUrl();
        if (cancelled) return;
        if (logoUrl) {
          setIdentityIcon(logoUrl);
        }
      } catch {
        // Fall back to default icon
      }
    }

    fetchOrgLogo();
    return () => { cancelled = true; };
  }, [scope]);

  // ── Fetch user info (identity for personal, enabled-by for all) ──
  useEffect(() => {
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

          if (scope === 'personal') {
            setIdentityName(fullName || instance.name);
            if (userId) setIdentityIcon(`/api/v1/users/${userId}/dp`);
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
  }, [scope, instance.createdBy, instance.name]);

  // ── Derived data ──
  const effectiveStatus = deriveSyncStatus(instance, stats);
  const syncStrategy = getSyncStrategyLabel(config);
  const syncInterval = getSyncIntervalLabel(config);
  const recordsSelected = getRecordsSelectedInfo(config);
  const lastSynced = formatRelativeTime(instance.updatedAtTimestamp);
  const enabledDate = formatEnabledDate(instance.createdAtTimestamp);

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
            style={{ width: 32, height: 32, borderRadius: 'var(--radius-2)', flexShrink: 0, overflow: 'hidden' }}
          >
            {identityIcon && !identityIconError ? (
              <img
                src={identityIcon}
                alt={identityName}
                width={32}
                height={32}
                onError={() => setIdentityIconError(true)}
                style={{ display: 'block', objectFit: 'cover', width: 32, height: 32 }}
              />
            ) : (
              <MaterialIcon
                name={scope === 'team' ? 'business' : 'person'}
                size={18}
                color="var(--gray-9)"
              />
            )}
          </Flex>

          {/* Identity name */}
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--gray-12)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {identityName}
          </Text>

          {/* Sync button */}
          {instance._key && instance.isActive && instance.isConfigured && instance.isAuthenticated && (
            <SyncButton
              connectorId={instance._key}
              connectorName={instance.name}
            />
          )}

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
          label={recordsSelected?.label ?? 'RECORDS SELECTED'}
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
              SYNC STRATEGY
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
          <InfoRow label="SYNC STRATEGY" value="-" />
        )}

        {/* ── Enabled By ── */}
        {enabledByName ? (
          <Flex align="center" gap="4">
            <Text
              size="1"
              weight="medium"
              style={{ color: 'var(--gray-10)', width: 164, flexShrink: 0, textTransform: 'uppercase', letterSpacing: '0.04px', lineHeight: '16px' }}
            >
              ENABLED BY
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
          <InfoRow label="ENABLED BY" value="-" />
        )}

        {/* ── Last Synced ── */}
        <InfoRow label="LAST SYNCED" value={lastSynced} />
      </Flex>

      {/* ── Auth incomplete banner ── */}
      {effectiveStatus === 'auth_incomplete' && (
        <Flex
          align="center"
          justify="between"
          style={{ padding: 'var(--space-3) var(--space-4)', borderTop: '1px solid var(--gray-a4)', backgroundColor: 'var(--gray-a2)' }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Authenticate Personal Account
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              Connect your account to enable access and workspace configuration
            </Text>
          </Flex>
          <ConnectButton onClick={() => onManage?.(instance)} />
        </Flex>
      )}
    </Flex>
  );
}
