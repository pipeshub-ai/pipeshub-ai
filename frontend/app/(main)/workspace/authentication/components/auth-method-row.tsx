'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Badge, IconButton, Tooltip } from '@radix-ui/themes';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
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
  onToggle,
  onConfigure,
}: AuthMethodRowProps) {
  const { t } = useTranslation();
  const canHover = useCanHover();
  const isMobile = useIsMobile();
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
    }
  }

  const showConfigureButton = isConfigurable;

  // ── Badge colour ─────────────────────────────────────────
  const badgeColor = isConfigured ? 'green' : 'orange';
  const badgeLabel = isConfigured ? t('workspace.authentication.badges.configured') : t('workspace.authentication.badges.notConfigured');
  const badge = isConfigurable ? (
    <Badge color={badgeColor} variant="soft" size="1">
      {badgeLabel}
    </Badge>
  ) : null;

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
      }}
    >
      {/* Icon */}
      <Flex
        align="center"
        justify="center"
        style={{
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-a2)',
          flexShrink: 0,
          padding: 'var(--space-2)'
        }}
      >
        {meta.iconType === 'image' ? (
          <img
            src={meta.icon}
            alt={meta.label}
            style={{ width: 16, height: 16, objectFit: 'contain' }}
          />
        ) : (
          <MaterialIcon name={meta.icon} size={16} color="var(--slate-11)" />
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
            // Phones leave little room beside the badge and switch; two lines keep the sentence readable.
            ...(isMobile
              ? { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }
              : { whiteSpace: 'nowrap' as const }),
          }}
        >
          {meta.description}
        </Text>
        {/* Phones: the badge moves under the text so the description gets the row's width. */}
        {isMobile && badge && <Box style={{ marginTop: 4 }}>{badge}</Box>}
        {disabledReason && !canHover && (
          <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2 }}>
            {disabledReason}
          </Text>
        )}
      </Box>

      {/* Right side: config badge + gear + toggle */}
      <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
        {/* Config status badge (only for configurable methods) */}
        {!isMobile && badge}

        {/* Gear / configure button */}
        {showConfigureButton && (
          <Tooltip content={t('workspace.authentication.configure')}>
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              className="touch-hit"
              aria-label={`${t('workspace.authentication.configure')}: ${meta.label}`}
              onClick={() => onConfigure(state.type as ConfigurableMethod)}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="settings" size={16} color="var(--slate-10)" />
            </IconButton>
          </Tooltip>
        )}

        {/* Toggle — avoid `disabled` prop to prevent Radix muted styling */}
        {(() => {
          const toggle = (
            <Switch
              color="jade"
              size="2"
              checked={state.enabled}
              onCheckedChange={toggleDisabled ? undefined : () => onToggle(state.type)}
              style={toggleDisabled ? { pointerEvents: 'none' } : undefined}
              {...(toggleDisabled && { tabIndex: -1, 'aria-disabled': true })}
            />
          );
          if (disabledReason) {
            return (
              <Tooltip content={disabledReason}>
                <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                  {toggle}
                </span>
              </Tooltip>
            );
          }
          return toggle;
        })()}
      </Flex>
    </Flex>
  );
}
