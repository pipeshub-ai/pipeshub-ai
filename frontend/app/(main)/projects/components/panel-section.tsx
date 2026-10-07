'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Shared bordered container for the right-column workspace cards (Project setup, Chat defaults, About). */
export function PanelCard({ children }: { children: React.ReactNode }) {
  return (
    <Box style={{ background: 'var(--olive-2)', border: '1px solid var(--olive-4)', borderRadius: 'var(--radius-3)' }}>
      {children}
    </Box>
  );
}

/** Uppercase small-caps header row shared by every right-column card, e.g. "PROJECT SETUP  0 / 5". */
export function PanelHeader({ title, trailing }: { title: string; trailing?: React.ReactNode }) {
  return (
    <Flex align="center" justify="between" style={{ padding: 'var(--space-3) var(--space-4)' }}>
      <Text
        size="1"
        weight="medium"
        style={{ color: 'var(--slate-9)', textTransform: 'uppercase', letterSpacing: '0.04em' }}
      >
        {title}
      </Text>
      {trailing}
    </Flex>
  );
}

interface PanelRowProps {
  icon?: string;
  label: string;
  /** Plain string renders as muted small text; pass a node (e.g. a `Badge`) for richer values. */
  value: React.ReactNode;
  onClick?: () => void;
  isFirst?: boolean;
}

/** A static (non-expandable) label/value row — used by the Chat defaults and About cards. */
export function PanelRow({ icon, label, value, onClick, isFirst = false }: PanelRowProps) {
  return (
    <Flex
      align="center"
      justify="between"
      gap="2"
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      style={{
        padding: 'var(--space-3) 0',
        borderTop: isFirst ? 'none' : '1px solid var(--olive-4)',
        cursor: onClick ? 'pointer' : 'default',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        {icon && <MaterialIcon name={icon} size={16} color="var(--slate-10)" />}
        <Text size="2" style={{ color: 'var(--slate-12)' }}>
          {label}
        </Text>
      </Flex>
      <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
        {typeof value === 'string' ? (
          <Text size="1" style={{ color: 'var(--slate-9)', textAlign: 'right' }}>
            {value}
          </Text>
        ) : (
          value
        )}
        {onClick && <MaterialIcon name="chevron_right" size={16} color="var(--slate-8)" style={{ flexShrink: 0 }} />}
      </Flex>
    </Flex>
  );
}
