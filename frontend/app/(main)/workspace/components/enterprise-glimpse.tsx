'use client';

import { Avatar, Badge, Box, Button, Flex, Grid, Switch, Text } from '@radix-ui/themes';
import type { ComponentProps } from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

type BadgeColor = ComponentProps<typeof Badge>['color'];

/**
 * One cell of a glimpse table: plain text, grey text, a person or group with an avatar, grey
 * tags, a status pill, or a value with a smaller grey line under it.
 */
export type GlimpseCell =
  | string
  | { muted: string }
  | { person: string }
  | { tags: string[] }
  | { pill: string; color: BadgeColor }
  | { primary: string; secondary: string };

/**
 * A blurred look at the Enterprise Edition page a placeholder stands in for. The content is
 * sample data, drawn blurred and kept away from assistive technology, so it is not UI copy.
 */
export interface Glimpse {
  /** A search field and the page's main button, like an entity list's header. */
  toolbar?: { search: string; button: string; icon: string };
  /** Tabs with the first one selected, and the page's buttons on the right (the last is primary). */
  tabs?: { items: string[]; buttons?: string[] };
  /** A filter bar: dropdown chips, then search fields. */
  filters?: { chips: string[]; fields?: string[] };
  stats?: { label: string; value: string; delta?: string }[];
  chart?: number[];
  field?: { label: string; value: string };
  /** `widths` are grid track sizes, one per column; the first column is wider by default. */
  table?: { columns: string[]; rows: GlimpseCell[][]; widths?: string[] };
  settings?: { label: string; on: boolean }[];
}

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
  if ('primary' in cell) {
    return (
      <Flex direction="column" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" truncate style={{ color: 'var(--slate-12)' }}>
          {cell.primary}
        </Text>
        <Text size="1" truncate style={{ color: 'var(--gray-11)' }}>
          {cell.secondary}
        </Text>
      </Flex>
    );
  }
  if ('muted' in cell) {
    return (
      <Text size="2" truncate style={{ color: 'var(--slate-11)' }}>
        {cell.muted}
      </Text>
    );
  }
  if ('person' in cell) {
    // Drawn like the product's AvatarCell: one accent colour for everyone, not a colour each.
    return (
      <Flex align="center" gap="3" style={{ minWidth: 0 }}>
        <Avatar size="2" radius="small" variant="soft" fallback={initials(cell.person)} />
        <Text size="2" weight="medium" truncate style={{ color: 'var(--slate-12)' }}>
          {cell.person}
        </Text>
      </Flex>
    );
  }
  if ('tags' in cell) {
    return (
      <Flex gap="1" style={{ minWidth: 0, overflow: 'hidden' }}>
        {cell.tags.map((tag) => (
          <Badge key={tag} size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
            {tag}
          </Badge>
        ))}
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
  const { toolbar, tabs, filters, stats, chart, field, table, settings } = glimpse;
  const gridColumns = table
    ? (table.widths ?? ['1.4fr', ...table.columns.slice(1).map(() => '1fr')]).map((w) => `minmax(0, ${w})`).join(' ')
    : undefined;

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
        {toolbar && (
          <Flex align="center" justify="between" gap="3">
            <Flex
              align="center"
              gap="2"
              style={{ ...PANEL, height: 32, width: 260, maxWidth: '60%', padding: '0 var(--space-2)' }}
            >
              <MaterialIcon name="search" size={16} color="var(--gray-11)" />
              <Text size="2" truncate style={{ color: 'var(--gray-11)' }}>
                {toolbar.search}
              </Text>
            </Flex>
            <Button size="2">
              <MaterialIcon name={toolbar.icon} size={16} color="currentColor" />
              {toolbar.button}
            </Button>
          </Flex>
        )}

        {tabs && (
          <Flex align="end" justify="between" gap="3" wrap="wrap" style={{ boxShadow: 'inset 0 -1px 0 var(--gray-a5)' }}>
            <Flex gap="4">
              {tabs.items.map((item, i) => (
                <Text
                  key={item}
                  size="2"
                  weight={i === 0 ? 'medium' : 'regular'}
                  style={{
                    paddingBottom: 'var(--space-2)',
                    color: i === 0 ? 'var(--slate-12)' : 'var(--slate-11)',
                    boxShadow: i === 0 ? 'inset 0 -2px 0 var(--accent-9)' : undefined,
                  }}
                >
                  {item}
                </Text>
              ))}
            </Flex>
            {tabs.buttons && (
              <Flex gap="2" style={{ paddingBottom: 'var(--space-2)' }}>
                {tabs.buttons.map((label, i, all) => {
                  const primary = i === all.length - 1;
                  return (
                    <Button key={label} size="1" variant={primary ? 'solid' : 'soft'} color={primary ? undefined : 'gray'}>
                      {label}
                    </Button>
                  );
                })}
              </Flex>
            )}
          </Flex>
        )}

        {filters && (
          <Flex gap="2" wrap="wrap">
            {filters.chips.map((chip) => (
              <Button key={chip} size="1" variant="outline" color="gray">
                {chip}
                <MaterialIcon name="expand_more" size={14} color="currentColor" />
              </Button>
            ))}
            {filters.fields?.map((placeholder) => (
              <Box
                key={placeholder}
                style={{ ...PANEL, height: 24, width: 120, padding: '0 var(--space-2)', display: 'flex', alignItems: 'center' }}
              >
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {placeholder}
                </Text>
              </Box>
            ))}
          </Flex>
        )}

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
