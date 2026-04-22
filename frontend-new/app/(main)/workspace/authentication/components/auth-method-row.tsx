'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Badge, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AuthMethodMeta, AuthMethodState, ConfigurableMethod, ConfigStatus } from '../types';

// ========================================
// Types
// ========================================

interface AuthMethodRowProps {
  meta: AuthMethodMeta;
  state: AuthMethodState;
  isEditing: boolean;
  configStatus: ConfigStatus;
  smtpConfigured: boolean;
  /** True when another method is currently ON — used to enforce single-method policy */
  anotherMethodEnabled: boolean;
  onToggle: (type: string) => void;
  onConfigure: (type: ConfigurableMethod) => void;
}

// ========================================
// Component
// ========================================

export function AuthMethodRow({
  meta,
  state,
  isEditing,
  configStatus,
  smtpConfigured,
  anotherMethodEnabled,
  onToggle,
  onConfigure,
}: AuthMethodRowProps) {
  const { t } = useTranslation();
  const isConfigurable = meta.configurable;
  const isConfigured = isConfigurable
    ? configStatus[state.type as keyof ConfigStatus] ?? false
    : true;

  // ── Derive disabled state ────────────────────────────────
  let toggleDisabled = !isEditing;
  let disabledReason = '';

  if (isEditing) {
    if (meta.requiresSmtp && !smtpConfigured) {
      toggleDisabled = true;
      disabledReason = t('workspace.authentication.disabledReasons.requiresSmtp');
    } else if (isConfigurable && !isConfigured) {
      toggleDisabled = true;
      disabledReason = t('workspace.authentication.disabledReasons.notConfigured');
    } else if (!state.enabled && anotherMethodEnabled) {
      toggleDisabled = true;
      disabledReason = t('workspace.authentication.disabledReasons.anotherActive');
    }
  }

  const showConfigureButton = isConfigurable;

  const switchStyle = {
    '--accent-9': 'rgba(0, 0, 51, 0.25)',
    '--accent-indicator': 'white',
  } as React.CSSProperties;

  // ── Badge colour ─────────────────────────────────────────
  const badgeColor = isConfigured ? 'green' : 'orange';
  const badgeLabel = isConfigured ? t('workspace.authentication.badges.configured') : t('workspace.authentication.badges.notConfigured');

  return (
    <Flex
      align="center"
      gap="3"
      py="3"
      px="4"
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        backgroundColor: 'var(--olive-2)',
        backdropFilter: 'blur(25px)',
        opacity: toggleDisabled ? 0.7 : 1,
      }}
    >
      {/* Icon */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: 'var(--space-9)',
          height: 'var(--space-9)',
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

      {/* Right side: config badge + gear + toggle */}
      <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
        {/* Config status badge (only for configurable methods) */}
        {isConfigurable && (
          <Badge color={badgeColor} variant="soft" size="1">
            {badgeLabel}
          </Badge>
        )}

        {/* Gear / configure button */}
        {showConfigureButton && (
          <Tooltip content={t('workspace.authentication.configure')}>
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={() => onConfigure(state.type as ConfigurableMethod)}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="settings" size={16} color="var(--slate-10)" />
            </IconButton>
          </Tooltip>
        )}

        {/* Toggle */}
        {toggleDisabled && disabledReason ? (
          <Tooltip content={disabledReason}>
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              <Switch
                color="gray"
                size="2"
                checked={state.enabled}
                disabled={toggleDisabled}
                onCheckedChange={() => onToggle(state.type)}
                style={switchStyle}
              />
            </span>
          </Tooltip>
        ) : (
          <Switch
            color="gray"
            size="2"
            checked={state.enabled}
            disabled={toggleDisabled}
            onCheckedChange={() => onToggle(state.type)}
            style={switchStyle}
          />
        )}
      </Flex>
    </Flex>
  );
}
