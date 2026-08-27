'use client';

import React from 'react';
import { Box, Flex, IconButton, Popover, Separator, Switch, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { DEFAULT_AGENT_CAPABILITIES } from '@/chat/types';

export function hasNonDefaultSearchCapabilities(
  internalSearch: boolean,
  webSearch: boolean,
): boolean {
  return (
    internalSearch !== DEFAULT_AGENT_CAPABILITIES.internalSearch ||
    webSearch !== DEFAULT_AGENT_CAPABILITIES.webSearch
  );
}

export function PlusMenuTriggerIcon({
  color,
  showFilterBadge,
}: {
  color: string;
  showFilterBadge: boolean;
}) {
  return (
    <Box style={{ position: 'relative', display: 'inline-flex' }}>
      <MaterialIcon name="add" size={ICON_SIZES.PRIMARY} color={color} />
      {showFilterBadge && (
        <Box
          data-testid="plus-menu-filter-badge"
          aria-hidden
          style={{
            position: 'absolute',
            top: -1,
            right: -1,
            width: 7,
            height: 7,
            borderRadius: '50%',
            backgroundColor: 'var(--red-9)',
            boxShadow: '0 0 0 1.5px var(--color-panel-solid)',
            pointerEvents: 'none',
          }}
        />
      )}
    </Box>
  );
}

export interface PlusMenuCapabilities {
  internalSearch: boolean;
  webSearch: boolean;
  onToggleInternalSearch: (enabled: boolean) => void;
  onToggleWebSearch: (enabled: boolean) => void;
  /**
   * `false` = the agent wasn't configured with this capability, so the toggle
   * is disabled. `undefined` = universal mode, capability always available.
   */
  agentHasInternalSearch?: boolean;
  agentHasWebSearch?: boolean;
  debugDisableSemantic: boolean;
  debugDisablePatternMatch: boolean;
  onToggleDebugDisableSemantic: (v: boolean) => void;
  onToggleDebugDisablePatternMatch: (v: boolean) => void;
}

interface PlusMenuContentProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
}

function PlusMenuRow({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      role={onClick ? 'menuitem' : undefined}
      tabIndex={onClick && !disabled ? 0 : undefined}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      onKeyDown={
        disabled || !onClick
          ? undefined
          : (e: React.KeyboardEvent) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick();
              }
            }
      }
      style={{
        padding: 'var(--space-2) var(--space-2)',
        borderRadius: 'var(--radius-2)',
        cursor: disabled ? 'not-allowed' : onClick ? 'pointer' : 'default',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {children}
    </Flex>
  );
}

function PlusMenuToggleRow({
  label,
  checked,
  disabled,
  disabledTooltip,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  disabledTooltip?: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  const row = (
    <PlusMenuRow
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
    >
      <Text size="2" style={{ color: 'var(--slate-12)' }}>
        {label}
      </Text>
      <Switch
        size="1"
        aria-label={label}
        checked={checked && !disabled}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        onClick={(e) => e.stopPropagation()}
      />
    </PlusMenuRow>
  );
  return disabled && disabledTooltip ? <Tooltip content={disabledTooltip}>{row}</Tooltip> : row;
}

/** Shared menu body — attach files + capability toggles — used by both the desktop popover and mobile sheet. */
export function PlusMenuContent({
  onAttachFiles,
  internalSearch,
  webSearch,
  onToggleInternalSearch,
  onToggleWebSearch,
  agentHasInternalSearch,
  agentHasWebSearch,
  debugDisableSemantic,
  debugDisablePatternMatch,
  onToggleDebugDisableSemantic,
  onToggleDebugDisablePatternMatch,
}: PlusMenuContentProps) {
  const { t } = useTranslation();
  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });

  return (
    <Flex direction="column" gap="1" style={{ minWidth: '200px' }}>
      <PlusMenuRow onClick={onAttachFiles}>
        <Flex align="center" gap="2">
          <MaterialIcon name="attach_file" size={ICON_SIZES.PRIMARY} color="var(--slate-11)" />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('chat.attachmentTooltip', { defaultValue: 'Attach files' })}
          </Text>
        </Flex>
      </PlusMenuRow>

      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />

      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.internalSearch', { defaultValue: 'Internal Search' })}
        checked={internalSearch}
        disabled={agentHasInternalSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleInternalSearch}
      />
      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.webSearch', { defaultValue: 'Web Search' })}
        checked={webSearch}
        disabled={agentHasWebSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleWebSearch}
      />

      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />

      <PlusMenuToggleRow
        label="Disable Semantic"
        checked={debugDisableSemantic}
        onCheckedChange={onToggleDebugDisableSemantic}
      />
      <PlusMenuToggleRow
        label="Disable Pattern Match"
        checked={debugDisablePatternMatch}
        onCheckedChange={onToggleDebugDisablePatternMatch}
      />
    </Flex>
  );
}

export interface PlusMenuButtonProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
  activeIconColor: string;
  disabled?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Desktop "+" trigger that replaces the old Agent / Internal Search / Web
 * Search mode pill. Opens a popover with "Attach files" plus the Internal
 * Search / Web Search capability toggles.
 */
export function PlusMenuButton({
  onAttachFiles,
  activeIconColor,
  disabled,
  open,
  onOpenChange,
  ...capabilities
}: PlusMenuButtonProps) {
  const { t } = useTranslation();
  const showFilterBadge = hasNonDefaultSearchCapabilities(
    capabilities.internalSearch,
    capabilities.webSearch,
  );
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger>
        <IconButton
          variant={open ? 'soft' : 'ghost'}
          color="gray"
          size="2"
          disabled={disabled}
          aria-label={
            showFilterBadge
              ? t('chat.plusMenu.ariaLabelFiltersApplied', {
                  defaultValue: 'Attach files and capabilities. Search filters applied',
                })
              : t('chat.plusMenu.ariaLabel', { defaultValue: 'Attach files and capabilities' })
          }
          style={{ margin: 0, cursor: disabled ? 'default' : 'pointer' }}
        >
          <PlusMenuTriggerIcon
            color={disabled ? 'var(--slate-5)' : activeIconColor}
            showFilterBadge={showFilterBadge}
          />
        </IconButton>
      </Popover.Trigger>
      <Popover.Content
        side="top"
        align="start"
        style={{
          padding: 'var(--space-2)',
          minWidth: '220px',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-3)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        <PlusMenuContent
          onAttachFiles={() => {
            onOpenChange(false);
            onAttachFiles();
          }}
          {...capabilities}
        />
      </Popover.Content>
    </Popover.Root>
  );
}

export interface PlusMenuSheetProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Mobile bottom-sheet variant of the "+" menu. */
export function PlusMenuSheet({
  onAttachFiles,
  open,
  onOpenChange,
  ...capabilities
}: PlusMenuSheetProps) {
  const { t } = useTranslation();
  return (
    <MobileBottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={t('chat.plusMenu.title', { defaultValue: 'Add & capabilities' })}
    >
      <PlusMenuContent
        onAttachFiles={() => {
          onOpenChange(false);
          onAttachFiles();
        }}
        {...capabilities}
      />
    </MobileBottomSheet>
  );
}
