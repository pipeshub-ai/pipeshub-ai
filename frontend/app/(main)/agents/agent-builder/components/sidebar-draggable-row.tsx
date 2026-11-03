'use client';

import React, { createContext, useContext } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { normalizePaletteLabel } from '../display-utils';

const PALETTE_ROW_MIN_HEIGHT = 44;

export function applyDragData(event: React.DragEvent, entries: Record<string, string>) {
  event.dataTransfer.effectAllowed = 'move';
  Object.entries(entries).forEach(([k, v]) => {
    if (v != null) event.dataTransfer.setData(k, v);
  });
}

/** Adds a palette row's drag payload to the canvas; set by the builder so rows work without drag. */
export const PaletteTapAddContext = createContext<((data: Record<string, string>) => void) | null>(null);

/** Tap handler for a row with this drag payload, or undefined when the row cannot be added. */
export function usePaletteTapAdd(data: Record<string, string> | undefined, disabled = false): (() => void) | undefined {
  const add = useContext(PaletteTapAddContext);
  return add && data && !disabled ? () => add(data) : undefined;
}

/** Visible "+" for touch screens and phones, where HTML5 drag does not start. */
export function TapAddButton({ onTap }: { onTap?: () => void }) {
  return onTap ? <TapAddIconButton onTap={onTap} /> : null;
}

function TapAddIconButton({ onTap }: { onTap: () => void }) {
  const { t } = useTranslation();
  const canHover = useCanHover();
  const isMobile = useIsMobile();
  if (canHover && !isMobile) return null;
  return (
    <IconButton
      type="button"
      size="1"
      variant="soft"
      className="touch-hit"
      aria-label={t('agentBuilder.addToCanvas')}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onTap();
      }}
      style={{ flexShrink: 0 }}
    >
      <MaterialIcon name="add" size={18} />
    </IconButton>
  );
}

export function DraggableRow({
  children,
  disabled,
  data,
  onBlocked,
  comfortable = false,
  title,
  mb,
  onTap,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  data: Record<string, string>;
  onBlocked?: () => void;
  /** Taller rows for main palette items (models, knowledge, connectors). */
  comfortable?: boolean;
  title?: string;
  mb?: React.ComponentProps<typeof Box>['mb'];
  /** Adds the row without dragging; defaults to the palette's tap-to-add. */
  onTap?: () => void;
}) {
  const paletteTap = usePaletteTapAdd(data, disabled);
  return (
    <Box
      draggable={!disabled}
      // Browsers send no dragstart for a row that can't be dragged, so pressing it is what says why.
      onPointerDown={disabled ? () => onBlocked?.() : undefined}
      onDragStart={(e) => {
        if (disabled) {
          e.preventDefault();
          onBlocked?.();
          return;
        }
        applyDragData(e, data);
      }}
      title={title}
      mb={mb}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        minWidth: 0,
        // Otherwise `.agent-builder-draggable-row` sets it: 36px, and a 44px touch target on mobile.
        minHeight: comfortable ? PALETTE_ROW_MIN_HEIGHT : undefined,
        padding: comfortable ? '0 14px' : '0 12px',
        boxSizing: 'border-box',
        gap: comfortable ? 10 : 8,
        cursor: disabled ? 'not-allowed' : 'grab',
        opacity: disabled ? 0.55 : 1,
        borderRadius: comfortable ? 'var(--radius-2)' : 'var(--radius-1)',
        border: comfortable ? '1px solid var(--olive-3)' : '1px solid transparent',
        backgroundColor: comfortable ? 'var(--olive-2)' : 'transparent',
      }}
      className={
        disabled
          ? 'agent-builder-draggable-row agent-builder-draggable-row--disabled'
          : 'agent-builder-draggable-row'
      }
    >
      {children}
      <TapAddButton onTap={disabled ? undefined : (onTap ?? paletteTap)} />
    </Box>
  );
}

/** One tool under a toolset or MCP server in the palette. */
export function SidebarToolDragRow({
  name,
  description,
  data,
  disabled,
  onBlocked,
  mb,
}: {
  name: string;
  description?: string;
  data: Record<string, string>;
  disabled?: boolean;
  onBlocked?: () => void;
  mb?: React.ComponentProps<typeof Box>['mb'];
}) {
  return (
    <DraggableRow data={data} disabled={disabled} onBlocked={onBlocked} title={description || undefined} mb={mb}>
      <MaterialIcon name="build" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" style={{ flexShrink: 0, lineHeight: 0 }} />
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 14,
          color: 'var(--slate-11)',
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
          textAlign: 'left',
        }}
      >
        {normalizePaletteLabel(name)}
      </span>
    </DraggableRow>
  );
}
