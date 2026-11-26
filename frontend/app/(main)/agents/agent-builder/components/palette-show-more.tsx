'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { BuilderCatalogPaging } from '../hooks/use-agent-builder-data';

/**
 * A palette group's "Show more": first the rows read but folded away, then the catalog's next page from the server
 * (R30-LAT-FU-1), then "Show less". Absent when there is nothing more and nothing to fold.
 */
export function PaletteShowMore({
  expanded,
  read,
  visible,
  paging,
  onToggle,
}: {
  expanded: boolean;
  /** Rows the palette has of this group. */
  read: number;
  /** Rows on screen now. */
  visible: number;
  paging?: BuilderCatalogPaging;
  onToggle: (expanded: boolean) => void;
}) {
  const { t } = useTranslation();
  const hasMore = paging?.hasMore === true;
  const hidden = read - visible;
  if (hidden <= 0 && !hasMore && !expanded) return null;
  const fetchNext = hasMore && hidden <= 0;
  // What is not on screen: the catalog's count when the server gives one, else the rows folded away.
  const count = paging?.total != null ? Math.max(paging.total - visible, 0) : fetchNext ? null : hidden;
  const label =
    expanded && !hasMore
      ? t('agentBuilder.showLess')
      : count != null && count > 0
        ? `${t('agentBuilder.showMore')} (${count})`
        : t('agentBuilder.showMore');
  const busy = fetchNext && paging?.loadingMore === true;

  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-busy={busy}
      disabled={busy}
      onClick={() => {
        if (fetchNext) {
          onToggle(true);
          void paging?.more();
        } else {
          onToggle(!expanded);
        }
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        gap: 4,
        padding: '6px 8px',
        cursor: busy ? 'progress' : 'pointer',
        userSelect: 'none',
        borderRadius: 'var(--radius-1)',
        border: 'none',
        background: 'none',
        fontFamily: 'inherit',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a3)'; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
    >
      <MaterialIcon name={expanded && !hasMore ? 'expand_less' : 'expand_more'} size={16} color="var(--accent-11)" />
      <Text size="1" weight="medium" style={{ color: 'var(--accent-11)' }}>
        {label}
      </Text>
    </button>
  );
}
