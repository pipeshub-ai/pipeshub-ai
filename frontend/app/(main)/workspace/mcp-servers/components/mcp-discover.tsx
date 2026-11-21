'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Grid, Heading, IconButton, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { McpServersApi } from '../api';
import { MCP_CATEGORIES } from '../categories';
import { McpServerCard } from '../team/components/mcp-server-card';
import type { McpCategory, McpMyServerEntry, McpServerTemplate } from '../types';

// Long enough that typing a phrase asks the server once, short enough to feel live.
export const DISCOVER_SEARCH_DELAY_MS = 250;

interface McpDiscoverProps {
  /** The catalog entries this person may add. */
  catalog: McpServerTemplate[];
  instances: McpMyServerEntry[];
  description: string;
  /** Shown above the grid when nothing is searched or picked, such as servers popular here. */
  highlights?: ReactNode;
  onOpen: (template: McpServerTemplate) => void;
  onAdd: (template: McpServerTemplate) => void;
}

type Found = { query: string; templates: McpServerTemplate[] };

/** The catalog to browse: search by name or by what a server does, or pick a category. */
export function McpDiscover({ catalog, instances, description, highlights, onOpen, onAdd }: McpDiscoverProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<McpCategory | null>(null);
  const [found, setFound] = useState<Found | null>(null);
  const request = useRef(0);
  const q = query.trim();

  // The backend ranks a search (names, then what servers do); the category narrows here, at once.
  useEffect(() => {
    const id = ++request.current;
    if (!q) {
      setFound(null);
      return;
    }
    const timer = setTimeout(() => {
      McpServersApi.getCatalog({ search: q, limit: 200 })
        .then((res) => {
          if (id === request.current) setFound({ query: q, templates: res.templates });
        })
        .catch(() => {
          // A failed search falls back to names and descriptions here rather than showing nothing.
          if (id !== request.current) return;
          const needle = q.toLowerCase();
          setFound({
            query: q,
            templates: catalog.filter(
              (tpl) => tpl.displayName.toLowerCase().includes(needle) || tpl.description.toLowerCase().includes(needle),
            ),
          });
        });
    }, DISCOVER_SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [q, catalog]);

  const addedCount = useMemo(() => {
    const counts = new Map<string, number>();
    instances.forEach((i) => i.typeId && counts.set(i.typeId, (counts.get(i.typeId) ?? 0) + 1));
    return counts;
  }, [instances]);

  const counts = useMemo(() => {
    const byCategory = new Map<McpCategory, number>();
    catalog.forEach((tpl) => tpl.category && byCategory.set(tpl.category, (byCategory.get(tpl.category) ?? 0) + 1));
    return byCategory;
  }, [catalog]);

  const searching = Boolean(q) && found?.query !== q;
  const shown = useMemo(() => {
    const offered = new Map(catalog.map((tpl) => [tpl.typeId, tpl]));
    const inCategory = (tpl: McpServerTemplate) => !category || tpl.category === category;
    if (q && found) {
      // Search results keep the server's ranking; only entries this person may add are listed.
      return found.templates
        .filter((tpl) => offered.has(tpl.typeId) && inCategory(tpl))
        .map((tpl) => ({
          ...offered.get(tpl.typeId)!,
          matchedCapability: tpl.matchedCapability,
        }));
    }
    return catalog.filter(inCategory).sort(
      (a, b) =>
        Number((addedCount.get(a.typeId) ?? 0) > 0) - Number((addedCount.get(b.typeId) ?? 0) > 0) ||
        a.displayName.localeCompare(b.displayName, undefined, {
          sensitivity: 'base',
        }),
    );
  }, [catalog, category, q, found, addedCount]);

  const label = (id: McpCategory) => t(`workspace.mcpServers.categories.${id}`);
  const chip = (id: McpCategory | null, text: string, count: number, icon?: string) => {
    const on = category === id;
    return (
      <Button
        key={id ?? 'all'}
        size={isMobile ? '2' : '1'}
        radius="full"
        variant={on ? 'soft' : 'outline'}
        color={on ? undefined : 'gray'}
        aria-pressed={on}
        onClick={() => setCategory(id)}
        style={{ cursor: 'pointer', flexShrink: 0 }}
      >
        {icon && <MaterialIcon name={icon} size={15} color="currentColor" />}
        {text}
        <Text size="1" style={{ color: on ? 'var(--accent-11)' : 'var(--gray-10)' }}>
          {count}
        </Text>
      </Button>
    );
  };

  const heading = q
    ? t('workspace.mcpServers.discover.resultsFor', {
        count: shown.length,
        query: q,
      })
    : category
      ? label(category)
      : t('workspace.mcpServers.discover.allServers');

  return (
    <Flex direction="column" gap="4" style={{ width: '100%' }} data-testid="mcp-discover">
      <Flex direction="column" gap="1">
        <Heading as="h2" size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.mcpServers.discover.title')}
        </Heading>
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {description}
        </Text>
      </Flex>

      <TextField.Root
        size={isMobile ? '3' : '2'}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t('workspace.mcpServers.discover.searchPlaceholder')}
        aria-label={t('workspace.mcpServers.discover.searchLabel')}
      >
        <TextField.Slot>
          <MaterialIcon name="search" size={16} color="var(--gray-9)" />
        </TextField.Slot>
        {query && (
          <TextField.Slot>
            <IconButton
              size="1"
              variant="ghost"
              color="gray"
              aria-label={t('workspace.mcpServers.discover.clearSearch')}
              onClick={() => setQuery('')}
            >
              <MaterialIcon name="close" size={16} color="var(--gray-10)" />
            </IconButton>
          </TextField.Slot>
        )}
      </TextField.Root>

      <Flex
        role="group"
        aria-label={t('workspace.mcpServers.discover.categoriesLabel')}
        gap="2"
        wrap={isMobile ? 'nowrap' : 'wrap'}
        className={isMobile ? 'no-scrollbar' : undefined}
        style={isMobile ? { overflowX: 'auto', paddingBottom: 'var(--space-1)' } : undefined}
      >
        {chip(null, t('workspace.mcpServers.discover.all'), catalog.length)}
        {MCP_CATEGORIES.filter((c) => counts.get(c.id)).map((c) =>
          chip(c.id, label(c.id), counts.get(c.id) ?? 0, c.icon),
        )}
      </Flex>

      {!q && !category && highlights}

      <Flex direction="column" gap="3">
        {(searching || shown.length > 0) && (
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }} aria-live="polite">
            {searching ? t('workspace.mcpServers.discover.searching') : heading}
          </Text>
        )}
        {!searching && shown.length === 0 ? (
          <Text size="2" style={{ color: 'var(--gray-11)' }} aria-live="polite">
            {t('workspace.mcpServers.discover.noMatch', { query: q })}
          </Text>
        ) : (
          <Grid
            gap="3"
            style={{
              width: '100%',
              gridTemplateColumns: 'repeat(auto-fill, minmax(min(240px, 100%), 1fr))',
            }}
          >
            {shown.map((tpl) => (
              <McpServerCard
                key={tpl.typeId}
                title={tpl.displayName}
                description={tpl.description}
                iconUrl={tpl.icon}
                typeId={tpl.typeId}
                categoryLabel={tpl.category ? label(tpl.category) : undefined}
                matchedCapability={tpl.matchedCapability}
                addedCount={addedCount.get(tpl.typeId) ?? 0}
                onOpen={() => onOpen(tpl)}
                onSetup={() => onAdd(tpl)}
              />
            ))}
          </Grid>
        )}
      </Flex>
    </Flex>
  );
}
