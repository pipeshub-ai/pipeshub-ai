'use client';

import { Avatar, Badge, Box, Flex, Grid, Switch, Text } from '@radix-ui/themes';
import type { ComponentProps } from 'react';

type BadgeColor = ComponentProps<typeof Badge>['color'];

/** One cell of a glimpse table: plain text, a person with an avatar, or a status pill. */
export type GlimpseCell = string | { person: string } | { pill: string; color: BadgeColor };

/**
 * A blurred look at the Enterprise Edition page a placeholder stands in for. The content is
 * sample data, drawn blurred and kept away from assistive technology, so it is not UI copy.
 */
export interface Glimpse {
  stats?: { label: string; value: string; delta?: string }[];
  chart?: number[];
  field?: { label: string; value: string };
  table?: { columns: string[]; rows: GlimpseCell[][] };
  settings?: { label: string; on: boolean }[];
}

const AVATAR_COLORS: ComponentProps<typeof Avatar>['color'][] = ['blue', 'amber', 'crimson', 'violet', 'teal', 'orange'];

const PANEL = {
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-3)',
  backgroundColor: 'var(--color-panel-solid)',
} as const;

function initials(name: string) {
  return name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2);
}

function Cell({ cell }: { cell: GlimpseCell }) {
  if (typeof cell === 'string') {
    return (
      <Text size="2" truncate style={{ color: 'var(--slate-12)' }}>
        {cell}
      </Text>
    );
  }
  if ('person' in cell) {
    const color = AVATAR_COLORS[cell.person.length % AVATAR_COLORS.length];
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Avatar size="1" radius="full" color={color} fallback={initials(cell.person)} />
        <Text size="2" truncate style={{ color: 'var(--slate-12)' }}>
          {cell.person}
        </Text>
      </Flex>
    );
  }
  return (
    <Box>
      <Badge size="1" variant="soft" color={cell.color}>
        {cell.pill}
      </Badge>
    </Box>
  );
}

/** Fades out at the bottom so the message under it reads as the end of the page. */
export function EnterpriseGlimpse({ glimpse, height }: { glimpse: Glimpse; height: number }) {
  const { stats, chart, field, table, settings } = glimpse;
  const gridColumns = table ? `1.4fr ${table.columns.slice(1).map(() => 'minmax(0, 1fr)').join(' ')}` : undefined;

  return (
    <Box
      aria-hidden="true"
      inert
      data-testid="enterprise-glimpse"
      style={{
        height,
        overflow: 'hidden',
        filter: 'blur(2px)',
        userSelect: 'none',
        maskImage: 'linear-gradient(to bottom, black 25%, transparent 92%)',
        WebkitMaskImage: 'linear-gradient(to bottom, black 25%, transparent 92%)',
      }}
    >
      <Flex direction="column" gap="3">
        {stats && (
          <Grid columns="repeat(auto-fit, minmax(150px, 1fr))" gap="3">
            {stats.map((stat) => (
              <Flex key={stat.label} direction="column" gap="1" style={{ ...PANEL, padding: 'var(--space-3)' }}>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {stat.label}
                </Text>
                <Text size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {stat.value}
                </Text>
                {stat.delta && (
                  <Text size="1" style={{ color: 'var(--jade-11)' }}>
                    {stat.delta}
                  </Text>
                )}
              </Flex>
            ))}
          </Grid>
        )}

        {chart && (
          <Flex align="end" gap="2" style={{ ...PANEL, height: 120, padding: 'var(--space-3)' }}>
            {chart.map((value, i) => (
              <Box
                key={i}
                style={{ flex: 1, height: `${value}%`, borderRadius: 'var(--radius-1)', backgroundColor: 'var(--accent-a7)' }}
              />
            ))}
          </Flex>
        )}

        {field && (
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {field.label}
            </Text>
            <Flex align="center" justify="between" gap="3" style={{ ...PANEL, padding: 'var(--space-2) var(--space-3)' }}>
              <Text size="2" truncate style={{ color: 'var(--slate-11)', fontFamily: 'var(--code-font-family)' }}>
                {field.value}
              </Text>
              <Box style={{ width: 56, height: 24, flexShrink: 0, borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-a4)' }} />
            </Flex>
          </Flex>
        )}

        {table && (
          <Box style={{ ...PANEL, overflow: 'hidden' }}>
            <Grid
              columns={gridColumns}
              gap="4"
              align="center"
              style={{ padding: 'var(--space-2) var(--space-3)', backgroundColor: 'var(--gray-a2)' }}
            >
              {table.columns.map((column) => (
                <Text key={column} size="1" truncate style={{ color: 'var(--slate-11)' }}>
                  {column}
                </Text>
              ))}
            </Grid>
            {table.rows.map((row, r) => (
              <Grid
                key={r}
                columns={gridColumns}
                gap="4"
                align="center"
                style={{ padding: 'var(--space-2) var(--space-3)', borderTop: '1px solid var(--gray-a4)' }}
              >
                {row.map((cell, c) => (
                  <Cell key={c} cell={cell} />
                ))}
              </Grid>
            ))}
          </Box>
        )}

        {settings && (
          <Box style={PANEL}>
            {settings.map((setting, i) => (
              <Flex
                key={setting.label}
                align="center"
                justify="between"
                gap="4"
                style={{ padding: 'var(--space-3)', borderTop: i === 0 ? undefined : '1px solid var(--gray-a4)' }}
              >
                <Text size="2" style={{ color: 'var(--slate-12)' }}>
                  {setting.label}
                </Text>
                <Switch size="1" checked={setting.on} />
              </Flex>
            ))}
          </Box>
        )}
      </Flex>
    </Box>
  );
}
