'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Dialog, Flex, IconButton, Text, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';

interface TableFullscreenWrapperProps {
  children: React.ReactNode;
}

/**
 * Wraps any table (markdown GFM or CSV) with an expand button and a fullscreen dialog.
 *
 * Button placement: a tiny ghost icon sits in a dedicated 24 px toolbar row ABOVE
 * the table border. This avoids every absolute-positioning edge case (overflow
 * clipping, z-index conflicts with sticky headers, wrapper-vs-table width mismatch).
 *
 * Fullscreen horizontal scroll: the dialog uses a SINGLE outer scroll container
 * (`overflow: auto` on the flex-fill Box).  Inside it a `min-width:100% / width:max-content`
 * div forces the wide table to overflow that outer Box rather than the inner table Box,
 * so the horizontal scrollbar is always pinned at the visible bottom of the dialog.
 *
 * CSS variables used (set on the outer scroll Box so they cascade to all cells):
 *   --table-cell-max-width   : none   (removes the 280 px per-column cap)
 *   --table-wrapper-max-height : none (removes the 55 vh row cap)
 */
export function TableFullscreenWrapper({ children }: TableFullscreenWrapperProps) {
  const [open, setOpen] = useState(false);
  const { t } = useTranslation();

  return (
    <Box style={{ marginBottom: 'var(--space-3)' }}>
      {/* ── Expand toolbar ── */}
      <Flex
        justify="end"
        align="center"
        style={{ marginBottom: '2px' }}
      >
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          title={t('chat.expandTable')}
          style={{ cursor: 'pointer', color: 'var(--slate-9)' }}
          onClick={() => setOpen(true)}
        >
          <MaterialIcon name="open_in_full" size={ICON_SIZES.SECONDARY} />
        </IconButton>
      </Flex>

      {/* ── Constrained table ── */}
      {children}

      {/* ── Fullscreen dialog ── */}
      <Dialog.Root open={open} onOpenChange={setOpen}>
        {open && (
          <Box
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'rgba(28, 32, 36, 0.5)',
              zIndex: 999,
              cursor: 'pointer',
            }}
            onClick={() => setOpen(false)}
          />
        )}

        <Dialog.Content
          style={{
            maxWidth: '92vw',
            width: '92vw',
            maxHeight: '88vh',
            height: '88vh',
            padding: 0,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            zIndex: 1000,
          }}
        >
          <VisuallyHidden>
            <Dialog.Title>{t('chat.fullTableView')}</Dialog.Title>
          </VisuallyHidden>

          {/* Header bar */}
          <Flex
            align="center"
            justify="between"
            style={{
              padding: 'var(--space-3) var(--space-4)',
              borderBottom: '1px solid var(--slate-6)',
              flexShrink: 0,
            }}
          >
            <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('chat.fullTableView')}
            </Text>
            <Dialog.Close>
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} />
              </IconButton>
            </Dialog.Close>
          </Flex>

          {/*
           * Single outer scroll container — handles BOTH vertical and horizontal
           * scrolling for the entire table.
           *
           * The inner `div` uses `min-width: 100%; width: max-content` so that when
           * the table is wider than the dialog, the overflow is reported to THIS Box
           * (not to the inner table Box), placing the horizontal scrollbar at the
           * fixed bottom of this visible area — always accessible without scrolling
           * to the end of the table rows.
           *
           * CSS vars cascade to every <th> / <td> / table-Box in `children`,
           * removing the normal-view width and height caps.
           */}
          <Box
            style={{
              flex: 1,
              minHeight: 0,
              overflow: 'auto',
              padding: 'var(--space-3)',
              '--table-cell-max-width': 'min(35vw, 480px)',
              '--table-wrapper-max-height': 'none',
            } as React.CSSProperties}
          >
            <div style={{ minWidth: '100%', width: 'max-content' }}>
              {children}
            </div>
          </Box>
        </Dialog.Content>
      </Dialog.Root>
    </Box>
  );
}
