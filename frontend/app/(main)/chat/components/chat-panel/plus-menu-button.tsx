'use client';

import React from 'react';
import { Box, Flex, IconButton, Popover, Separator, Switch, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
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
      <MaterialIcon name="add" size={16} color={color} />
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
  const [hovered, setHovered] = React.useState(false);
  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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
        height: 32,
        padding: '0 var(--space-2)',
        borderRadius: 'var(--radius-1)',
        backgroundColor: hovered && !disabled ? 'var(--olive-3)' : undefined,
        outline: 'none',
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
}: PlusMenuContentProps) {
  const { t } = useTranslation();
  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <PlusMenuRow onClick={onAttachFiles}>
        <Flex align="center" gap="2">
          <MaterialIcon name="attach_file" size={16} color="var(--neutral-11)" />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('chat.attachmentTooltip', { defaultValue: 'Attach files' })}
          </Text>
        </Flex>
      </PlusMenuRow>

      <Separator size="4" style={{ margin: 'var(--space-1) 0' }} />

      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.internalSearch', { defaultValue: 'Internal search' })}
        checked={internalSearch}
        disabled={agentHasInternalSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleInternalSearch}
      />
      <PlusMenuToggleRow
        label={t('chat.agentCapabilities.webSearch', { defaultValue: 'Web search' })}
        checked={webSearch}
        disabled={agentHasWebSearch === false}
        disabledTooltip={notConfiguredLabel}
        onCheckedChange={onToggleWebSearch}
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
          variant="ghost"
          color="gray"
          size="2"
          className="touch-size"
          disabled={disabled}
          aria-label={
            showFilterBadge
              ? t('chat.plusMenu.ariaLabelFiltersApplied', {
                  defaultValue: 'Attach files and capabilities. Search filters applied',
                })
              : t('chat.plusMenu.ariaLabel', { defaultValue: 'Attach files and capabilities' })
          }
          style={{
            margin: 0,
            width: 32,
            height: 32,
            minWidth: 32,
            padding: 0,
            boxSizing: 'border-box',
            backgroundColor: open ? 'var(--olive-3)' : undefined,
            borderRadius: 'var(--radius-2)',
            cursor: disabled ? 'default' : 'pointer',
          }}
        >
          <PlusMenuTriggerIcon
            color={disabled ? 'var(--slate-5)' : activeIconColor}
            showFilterBadge={showFilterBadge}
          />
        </IconButton>
      </Popover.Trigger>
      <Popover.Content
        data-composer-popover=""
        side="top"
        align="start"
        // Opening from the mouse should not paint a focus ring on the first row.
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          width: 216,
          padding: 6,
          // Same surface as the settings row menus: frosted, one olive-3 border, no ring shadow
          backgroundColor: 'var(--frost-bg-strong)',
          backdropFilter: 'var(--frost-filter)',
          WebkitBackdropFilter: 'var(--frost-filter)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          boxShadow: 'none',
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
