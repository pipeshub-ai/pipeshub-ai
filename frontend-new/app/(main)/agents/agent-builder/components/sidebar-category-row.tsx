'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ToolsetSidebarStatus } from '../sidebar-toolset-utils';

function applyDragPayload(e: React.DragEvent, dragType: string, dragData?: Record<string, string>) {
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('application/reactflow', dragType);
  if (dragData) {
    Object.entries(dragData).forEach(([k, v]) => {
      if (v != null) e.dataTransfer.setData(k, v);
    });
  }
}

function StatusGlyphTooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip content={label}>
      <span
        tabIndex={0}
        role="img"
        aria-label={label}
        style={{ display: 'inline-flex', flexShrink: 0, lineHeight: 0, outline: 'none' }}
      >
        {children}
      </span>
    </Tooltip>
  );
}

export function SidebarCategoryRow(props: {
  groupLabel: string;
  groupIcon?: string;
  groupMaterialIcon?: string;
  itemCount: number;
  isExpanded: boolean;
  onToggle: () => void;
  dragType?: string;
  dragData?: Record<string, string>;
  onDragAttempt?: () => void;
  showConfigureIcon?: boolean;
  onConfigureClick?: () => void;
  /** View-only / locked palette: show configure (or auth) control in a disabled state like palette rows. */
  configureDisabled?: boolean;
  configureDisabledTooltip?: string;
  configureTooltip?: string;
  /** When true, use key icon (service account); else settings */
  configureUseKeyIcon?: boolean;
  configureIconColor?: string;
  toolsetStatus?: ToolsetSidebarStatus;
  children?: React.ReactNode;
}) {
  const {
    groupLabel,
    groupIcon,
    groupMaterialIcon,
    itemCount,
    isExpanded,
    onToggle,
    dragType,
    dragData,
    onDragAttempt,
    showConfigureIcon,
    onConfigureClick,
    configureDisabled = false,
    configureDisabledTooltip,
    configureTooltip,
    configureUseKeyIcon,
    configureIconColor = 'var(--slate-11)',
    toolsetStatus,
    children,
  } = props;

  const { t } = useTranslation();
  const dragZoneActive = Boolean(dragType || onDragAttempt);

  const statusTooltip =
    toolsetStatus === 'authenticated'
      ? t('agentBuilder.toolsetStatusAuthenticated')
      : toolsetStatus === 'needs_authentication'
        ? t('agentBuilder.toolsetStatusNeedsAuth')
        : toolsetStatus === 'registry'
          ? t('agentBuilder.toolsetStatusRegistry')
          : undefined;

  const openInNewHandler =
    toolsetStatus === 'needs_authentication' && onConfigureClick && !configureDisabled
      ? onConfigureClick
      : undefined;

  /** When configure is locked, {@link openInNewHandler} is never set. */
  const showDisabledAuthControl = configureDisabled && toolsetStatus === 'needs_authentication';

  const gearHandler =
    toolsetStatus !== 'needs_authentication' && showConfigureIcon && onConfigureClick && !configureDisabled
      ? onConfigureClick
      : undefined;

  const showDisabledGear =
    configureDisabled && showConfigureIcon && toolsetStatus !== 'needs_authentication';

  const configureGlyphColor =
    toolsetStatus === 'registry' ? 'var(--red-11)' : configureIconColor;

  const handleDragZoneStart = (e: React.DragEvent) => {
    if (!dragType) {
      e.preventDefault();
      onDragAttempt?.();
      return;
    }
    applyDragPayload(e, dragType, dragData);
  };

  const truncated = groupLabel.length > 22 ? `${groupLabel.slice(0, 22)}…` : groupLabel;

  return (
    <Box mb="1">
      <Flex
        align="center"
        gap="1"
        px="2"
        py="2"
        mx="1"
        style={{
          borderRadius: 'var(--radius-1)',
          border: '1px solid var(--olive-3)',
          background: 'var(--olive-2)',
          userSelect: 'none',
        }}
        className="agent-builder-sidebar-category-row"
      >
        <IconButton
          type="button"
          size="1"
          variant="ghost"
          color="gray"
          aria-expanded={isExpanded}
          aria-label={isExpanded ? t('agentBuilder.sidebarCollapseTools') : t('agentBuilder.sidebarExpandTools')}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          onPointerDown={(e) => e.stopPropagation()}
          style={{ flexShrink: 0 }}
        >
          <MaterialIcon
            name={isExpanded ? 'expand_more' : 'chevron_right'}
            size={16}
            color="var(--slate-11)"
          />
        </IconButton>

        <Flex
          align="center"
          gap="2"
          flexGrow="1"
          flexShrink="1"
          py="1"
          px="1"
          draggable={dragZoneActive}
          onDragStart={dragZoneActive ? handleDragZoneStart : undefined}
          onPointerDown={(e) => {
            if (dragZoneActive) e.stopPropagation();
          }}
          onClick={(e) => e.stopPropagation()}
          style={{
            flex: 1,
            minWidth: 0,
            cursor: dragType ? 'grab' : onDragAttempt && !dragType ? 'not-allowed' : 'default',
            borderRadius: 'var(--radius-1)',
          }}
        >
          {groupIcon ? (
            <img
              src={groupIcon}
              alt=""
              width={18}
              height={18}
              style={{ objectFit: 'contain', flexShrink: 0, display: 'block' }}
            />
          ) : groupMaterialIcon ? (
            <MaterialIcon name={groupMaterialIcon} size={18} color="var(--slate-11)" style={{ flexShrink: 0 }} />
          ) : (
            <MaterialIcon name="extension" size={18} color="var(--slate-11)" style={{ flexShrink: 0 }} />
          )}
          <Tooltip content={groupLabel}>
            <Text size="2" weight="medium" style={{ flex: 1, minWidth: 0, color: 'var(--slate-12)' }}>
              {truncated}
            </Text>
          </Tooltip>
          <Text
            size="1"
            style={{
              flexShrink: 0,
              color: 'var(--slate-11)',
              background: 'var(--olive-3)',
              padding: '2px 6px',
              borderRadius: 'var(--radius-1)',
              fontWeight: 500,
            }}
          >
            {itemCount}
          </Text>
          {toolsetStatus === 'authenticated' && statusTooltip ? (
            <StatusGlyphTooltip label={statusTooltip}>
              <MaterialIcon name="check_circle" size={18} color="var(--accent-11)" />
            </StatusGlyphTooltip>
          ) : null}
          {toolsetStatus === 'needs_authentication' ? (
            openInNewHandler ? (
              <Box onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
                <Tooltip content={configureTooltip || statusTooltip || t('agentBuilder.authenticateShort')}>
                  <IconButton
                    type="button"
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={(e) => {
                      e.stopPropagation();
                      openInNewHandler();
                    }}
                    aria-label={configureTooltip || statusTooltip || t('agentBuilder.authenticateShort')}
                  >
                    <MaterialIcon name="open_in_new" size={18} color="var(--amber-11)" />
                  </IconButton>
                </Tooltip>
              </Box>
            ) : showDisabledAuthControl ? (
              <Box onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
                <Tooltip
                  content={
                    configureDisabledTooltip ||
                    configureTooltip ||
                    statusTooltip ||
                    t('agentBuilder.authenticateShort')
                  }
                >
                  <IconButton
                    type="button"
                    size="1"
                    variant="ghost"
                    color="gray"
                    disabled
                    aria-label={configureDisabledTooltip || t('agentBuilder.authenticateShort')}
                    style={{ cursor: 'not-allowed', opacity: 0.55, flexShrink: 0 }}
                  >
                    <MaterialIcon name="open_in_new" size={18} color="var(--slate-11)" />
                  </IconButton>
                </Tooltip>
              </Box>
            ) : statusTooltip ? (
              <StatusGlyphTooltip label={statusTooltip}>
                <MaterialIcon name="open_in_new" size={18} color="var(--amber-11)" />
              </StatusGlyphTooltip>
            ) : null
          ) : null}
          {gearHandler ? (
            <Box onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
              <Tooltip content={configureTooltip || t('agentBuilder.configureShort')}>
                <IconButton
                  type="button"
                  size="1"
                  variant="ghost"
                  color="gray"
                  onClick={(e) => {
                    e.stopPropagation();
                    gearHandler();
                  }}
                  aria-label={configureTooltip || t('agentBuilder.configureShort')}
                >
                  <MaterialIcon
                    name={configureUseKeyIcon ? 'vpn_key' : 'settings'}
                    size={18}
                    color={configureGlyphColor}
                  />
                </IconButton>
              </Tooltip>
            </Box>
          ) : showDisabledGear ? (
            <Box onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
              <Tooltip
                content={configureDisabledTooltip || configureTooltip || t('agentBuilder.configureShort')}
              >
                <IconButton
                  type="button"
                  size="1"
                  variant="ghost"
                  color="gray"
                  disabled
                  aria-label={configureDisabledTooltip || t('agentBuilder.configureShort')}
                  style={{ cursor: 'not-allowed', opacity: 0.55, flexShrink: 0 }}
                >
                  <MaterialIcon
                    name={configureUseKeyIcon ? 'vpn_key' : 'settings'}
                    size={18}
                    color="var(--slate-11)"
                  />
                </IconButton>
              </Tooltip>
            </Box>
          ) : null}
        </Flex>
      </Flex>
      {isExpanded ? (
        <Box pl="3" pr="1" pt="1" style={{ borderLeft: '1px solid var(--olive-4)', marginLeft: 18 }}>
          {children}
        </Box>
      ) : null}
    </Box>
  );
}
