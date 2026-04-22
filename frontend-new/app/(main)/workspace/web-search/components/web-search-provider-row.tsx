'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Badge, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type {
  WebSearchProviderMeta,
  WebSearchProviderState,
  ConfigurableProvider,
  WebSearchConfigStatus,
} from '../types';

// ========================================
// Types
// ========================================

interface WebSearchProviderRowProps {
  meta: WebSearchProviderMeta;
  state: WebSearchProviderState;
  isEditing: boolean;
  configStatus: WebSearchConfigStatus;
  anotherProviderEnabled: boolean;
  onToggle: (type: string) => void;
  onConfigure: (type: ConfigurableProvider) => void;
}

// ========================================
// Component
// ========================================

export function WebSearchProviderRow({
  meta,
  state,
  isEditing,
  configStatus,
  anotherProviderEnabled,
  onToggle,
  onConfigure,
}: WebSearchProviderRowProps) {
  const { t } = useTranslation();
  const isConfigurable = meta.configurable;
  const isConfigured = isConfigurable
    ? configStatus[state.type] ?? false
    : true;

  // ── Derive disabled state ────────────────────────────────
  let toggleDisabled = !isEditing;
  let disabledReason = '';

  if (isEditing) {
    if (isConfigurable && !isConfigured) {
      toggleDisabled = true;
      disabledReason = t('workspace.webSearch.disabledReasonConfigure');
    } else if (!state.enabled && anotherProviderEnabled) {
      toggleDisabled = true;
      disabledReason = t('workspace.webSearch.disabledReasonActive');
    }
  }

  // ── Badge rendering ─────────────────────────────────────
  const renderBadges = () => {
    if (state.enabled && state.isDefault) {
      return (
        <Badge color="blue" variant="soft" size="1">
          {t('workspace.webSearch.badges.default')}
        </Badge>
      );
    }
    if (state.enabled) {
      return (
        <Badge color="green" variant="soft" size="1">
          {t('workspace.webSearch.badges.active')}
        </Badge>
      );
    }
    if (isConfigurable) {
      return (
        <Badge color={isConfigured ? 'green' : 'orange'} variant="soft" size="1">
          {isConfigured ? t('workspace.mail.configured') : t('workspace.mail.notConfigured')}
        </Badge>
      );
    }
    return null;
  };

  return (
    <Flex
      align="center"
      gap="3"
      style={{
        padding: '12px 14px',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        background: 'var(--olive-2)',
      }}
    >
      {/* Icon */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-3)',
          flexShrink: 0,
        }}
      >
        {meta.iconType === 'image' ? (
          <img
            src={meta.icon}
            alt={meta.label}
            style={{ width: 18, height: 18, objectFit: 'contain' }}
          />
        ) : (
          <MaterialIcon name={meta.icon} size={18} color="var(--slate-11)" />
        )}
      </Flex>

      {/* Label + description */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {meta.label}
        </Text>
        <Text
          size="1"
          style={{
            color: 'var(--slate-10)',
            display: 'block',
            marginTop: 2,
            fontWeight: 300,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {meta.description}
        </Text>
      </Box>

      {/* Right side: badges + gear + toggle */}
      <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
        {renderBadges()}

        {isConfigurable && (
          <Tooltip content={t('workspace.webSearch.configureTip')}>
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={() => onConfigure(state.type as ConfigurableProvider)}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="settings" size={16} color="var(--slate-10)" />
            </IconButton>
          </Tooltip>
        )}

        {toggleDisabled && disabledReason ? (
          <Tooltip content={disabledReason}>
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              <Switch
                color="jade"
                size="2"
                checked={state.enabled}
                disabled={toggleDisabled}
                onCheckedChange={() => onToggle(state.type)}
              />
            </span>
          </Tooltip>
        ) : (
          <Switch
            color="jade"
            size="2"
            checked={state.enabled}
            disabled={toggleDisabled}
            onCheckedChange={() => onToggle(state.type)}
          />
        )}
      </Flex>
    </Flex>
  );
}
