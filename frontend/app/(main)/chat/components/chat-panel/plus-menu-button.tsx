'use client';

import React from 'react';
import { Box, DropdownMenu, IconButton, Switch, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
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
  /** Omitted, the glyph takes the button's own colour (Radix paints a disabled trigger). */
  color?: string;
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

/** A 16px leading glyph, hidden from assistive technology so a row is named by its words alone. */
function RowIcon({ name }: { name: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
      <MaterialIcon name={name} size={16} color="var(--text-secondary)" />
    </span>
  );
}

function CapabilityItem({
  icon,
  label,
  checked,
  disabled,
  disabledTooltip,
  onCheckedChange,
}: {
  icon: string;
  label: string;
  checked: boolean;
  disabled?: boolean;
  disabledTooltip?: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  const item = (
    <DropdownMenu.Item
      // 44px rows on a touch screen.
      className="touch-size"
      disabled={disabled}
      aria-label={label}
      // These are standing settings, often set together: flipping one keeps the menu open.
      onSelect={(event) => {
        event.preventDefault();
        if (!disabled) onCheckedChange(!checked);
      }}
    >
      <RowIcon name={icon} />
      {label}
      <span style={{ marginLeft: 'auto', paddingLeft: 'var(--space-3)', display: 'flex' }}>
        <Switch
          size="1"
          aria-label={label}
          checked={checked && !disabled}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
          // The row's own select would toggle a second time and undo this one.
          onClick={(event) => event.stopPropagation()}
        />
      </span>
    </DropdownMenu.Item>
  );
  return disabled && disabledTooltip ? <Tooltip content={disabledTooltip}>{item}</Tooltip> : item;
}

export interface PlusMenuButtonProps extends PlusMenuCapabilities {
  onAttachFiles: () => void;
  activeIconColor: string;
  disabled?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Extra trigger style from the toolbar (its optical pull, the phone touch size). */
  triggerStyle?: React.CSSProperties;
}

/**
 * The composer's "+": a menu with "Attach files" and the Internal search / Web search
 * capability switches. The same menu at every width; its rows grow to 44px on touch screens.
 */
export function PlusMenuButton({
  onAttachFiles,
  activeIconColor,
  disabled,
  open,
  onOpenChange,
  triggerStyle,
  ...capabilities
}: PlusMenuButtonProps) {
  const { t } = useTranslation();
  const showFilterBadge = hasNonDefaultSearchCapabilities(
    capabilities.internalSearch,
    capabilities.webSearch,
  );
  const notConfiguredLabel = t('chat.agentCapabilities.notConfiguredOnAgent', {
    defaultValue: 'Not configured on this agent',
  });
  const attachLabel = t('chat.attachmentTooltip', { defaultValue: 'Attach files' });
  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger disabled={disabled}>
        <IconButton
          variant="ghost"
          color="gray"
          size="2"
          className="touch-size"
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
            borderRadius: 'var(--radius-2)',
            cursor: disabled ? 'default' : 'pointer',
            ...triggerStyle,
          }}
        >
          <PlusMenuTriggerIcon color={disabled ? undefined : activeIconColor} showFilterBadge={showFilterBadge} />
        </IconButton>
      </DropdownMenu.Trigger>
      {/* Opens upward from the bottom-docked composer. A fixed 216px, so toggling a switch never resizes it. */}
      <DropdownMenu.Content
        data-composer-popover=""
        side="top"
        align="start"
        style={{
          width: 'calc(var(--space-9) * 3 + var(--space-5))',
          paddingBlock: 'calc(var(--space-1) * 1.5)',
          // Same surface as the settings row menus: frosted, one olive-3 border, no ring shadow
          backgroundColor: 'var(--frost-bg-strong)',
          backdropFilter: 'var(--frost-filter)',
          WebkitBackdropFilter: 'var(--frost-filter)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          boxShadow: 'none',
        }}
      >
        <DropdownMenu.Item className="touch-size" aria-label={attachLabel} onSelect={onAttachFiles}>
          <RowIcon name="attach_file" />
          {attachLabel}
        </DropdownMenu.Item>
        <DropdownMenu.Separator />
        <CapabilityItem
          icon="manage_search"
          label={t('chat.agentCapabilities.internalSearch', { defaultValue: 'Internal search' })}
          checked={capabilities.internalSearch}
          disabled={capabilities.agentHasInternalSearch === false}
          disabledTooltip={notConfiguredLabel}
          onCheckedChange={capabilities.onToggleInternalSearch}
        />
        <CapabilityItem
          icon="travel_explore"
          label={t('chat.agentCapabilities.webSearch', { defaultValue: 'Web search' })}
          checked={capabilities.webSearch}
          disabled={capabilities.agentHasWebSearch === false}
          disabledTooltip={notConfiguredLabel}
          onCheckedChange={capabilities.onToggleWebSearch}
        />
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
