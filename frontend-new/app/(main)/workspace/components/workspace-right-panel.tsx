'use client';

import React from 'react';
import { Dialog, Flex, Box, Text, Button, IconButton, VisuallyHidden, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

interface WorkspaceRightPanelProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;

  /** Panel header */
  title: string;
  icon?: React.ReactNode;
  /** Optional React node to replace the plain title text (e.g. an instance switcher dropdown) */
  titleNode?: React.ReactNode;

  /** Optional action buttons rendered in the header (e.g. Import CSV) */
  headerActions?: React.ReactNode;

  /** Panel body content */
  children: React.ReactNode;

  /** Footer button labels */
  primaryLabel?: string;
  secondaryLabel?: string;

  /** Footer button states */
  primaryDisabled?: boolean;
  primaryLoading?: boolean;

  /** Footer button callbacks */
  onPrimaryClick?: () => void;
  onSecondaryClick?: () => void;

  /** Hide the footer entirely (for read-only panels) */
  hideFooter?: boolean;

  /** Tooltip shown on the primary button when it is disabled */
  primaryTooltip?: string;
}

// ========================================
// Component
// ========================================

export function WorkspaceRightPanel({
  open,
  onOpenChange,
  title,
  icon,
  titleNode,
  headerActions,
  children,
  primaryLabel = 'Submit',
  secondaryLabel = 'Cancel',
  primaryDisabled = false,
  primaryLoading = false,
  onPrimaryClick,
  onSecondaryClick,
  hideFooter = false,
  primaryTooltip,
}: WorkspaceRightPanelProps) {
  const handleClose = () => onOpenChange(false);
  const handleSecondaryClick = onSecondaryClick ?? handleClose;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{title}</Dialog.Title>
        </VisuallyHidden>

        {/* ── Header ── */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 8px 8px 16px',
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" gap="2">
            {icon && (
              typeof icon === 'string'
                ? <MaterialIcon name={icon} size={20} color="var(--slate-12)"/>
                : icon
            )}
            {titleNode ?? (
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {title}
              </Text>
            )}
          </Flex>

          <Flex align="center" gap="2">
            {headerActions}
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={handleClose}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="close" size={18} color="var(--slate-11)" />
            </IconButton>
          </Flex>
        </Flex>

        {/* ── Body ── */}
        <Box
          style={{
            flex: 1,
            overflow: 'auto',
            padding: '16px',
            background: 'var(--effects-translucent)',
            // backdropFilter: 'blur(8px)',
          }}
        >
          {children}
        </Box>

        {/* ── Footer ── */}
        {!hideFooter && (
          <Flex
            align="center"
            justify="end"
            gap="2"
            style={{
              padding: '8px 8px 8px 16px',
              borderTop: '1px solid var(--olive-3)',
              background: 'var(--effects-translucent)',
              backdropFilter: 'blur(8px)',
              flexShrink: 0,
            }}
          >
            <Button
              variant="outline"
              color="gray"
              size="2"
              onClick={handleSecondaryClick}
              disabled={primaryLoading}
              style={{ cursor: primaryLoading ? 'not-allowed' : 'pointer' }}
            >
              {secondaryLabel}
            </Button>
            {primaryTooltip && (primaryDisabled || primaryLoading) ? (
              <Tooltip content={primaryTooltip}>
                <Button
                  variant="solid"
                  size="2"
                  onClick={onPrimaryClick}
                  disabled={primaryDisabled || primaryLoading}
                  style={{ cursor: primaryDisabled || primaryLoading ? 'not-allowed' : 'pointer' }}
                >
                  {primaryLoading ? 'Loading...' : primaryLabel}
                </Button>
              </Tooltip>
            ) : (
              <Button
                variant="solid"
                size="2"
                onClick={onPrimaryClick}
                disabled={primaryDisabled || primaryLoading}
                style={{ cursor: primaryDisabled || primaryLoading ? 'not-allowed' : 'pointer', backgroundColor: primaryDisabled || primaryLoading ? 'var(--slate-6)' : 'var(--emerald-9)' }}
              >
                {primaryLoading ? 'Loading...' : primaryLabel}
              </Button>
            )}
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}

export type { WorkspaceRightPanelProps };
