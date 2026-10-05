'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Checkbox, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { TableSkeleton } from '@/app/components/data-display';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar/constants';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { toast } from '@/lib/store/toast-store';
import { KbNodeNameIcon } from '../../utils/kb-node-name-icon';
import { TrashApi } from '../api';
import type { TrashItem, TrashListResponse } from '../types';
import {
  deletedByLabel,
  formatTrashDate,
  itemDisplayName,
  itemTypeLabel,
  locationLabel,
  orderForRestore,
  outcomeOfBulk,
  outcomeOfBulkError,
  outcomeOfSingle,
  outcomeOfSingleError,
  pageAfterRemoval,
  removalLabel,
  restoreMessage,
  retentionNote,
  type RestoreOutcome,
} from '../utils';

export const TRASH_PAGE_SIZE = 25;
/** Long enough to read a list of reasons; a plain success uses the default. */
const PROBLEM_TOAST_MS = 8000;

const COLUMN_WIDTHS = {
  location: '160px',
  deletedAt: '120px',
  deletedBy: '150px',
  removedAfter: '130px',
  actions: '112px',
} as const;

const headerCellStyle: React.CSSProperties = { padding: '0 var(--space-2)', flexShrink: 0 };
const cellTextStyle: React.CSSProperties = {
  color: 'var(--slate-11)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

interface RowProps {
  item: TrashItem;
  collectionName: string;
  isMobile: boolean;
  isSelected: boolean;
  isRestoring: boolean;
  disabled: boolean;
  onSelect: () => void;
  onRestore: () => void;
}

function TrashRow({ item, collectionName, isMobile, isSelected, isRestoring, disabled, onSelect, onRestore }: RowProps) {
  const { t, i18n } = useTranslation();
  const name = itemDisplayName(item, t);
  return (
    <Flex
      role="row"
      aria-label={name}
      align="center"
      style={{
        minHeight: '56px',
        borderBottom: '1px solid var(--olive-3)',
        backgroundColor: isSelected ? 'var(--olive-2)' : 'var(--olive-1)',
      }}
    >
      <Flex role="cell" align="center" justify="center" style={{ width: '38px', padding: '0 var(--space-2)', flexShrink: 0 }}>
        <Checkbox
          size="1"
          checked={isSelected}
          disabled={disabled}
          onCheckedChange={onSelect}
          aria-label={t('collections.trash.selectItem', { name })}
          style={{ cursor: 'pointer' }}
        />
      </Flex>

      <Flex role="cell" direction="column" justify="center" gap="1" style={{ flex: 1, minWidth: 0, padding: 'var(--space-2)' }}>
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <KbNodeNameIcon
            isKnowledgeHub
            nodeType={item.isFolder ? 'folder' : 'record'}
            mimeType={item.mimeType ?? undefined}
            name={item.name ?? undefined}
            size={20}
          />
          <Tooltip content={name} delayDuration={300}>
            <Text size="2" style={{ ...cellTextStyle, color: 'var(--slate-12)' }}>
              {name}
            </Text>
          </Tooltip>
        </Flex>
        <Text size="1" style={cellTextStyle}>
          {itemTypeLabel(item, t)}
          {isMobile ? ` · ${formatTrashDate(item.deletedAtTimestamp, i18n.language)}` : ''}
        </Text>
        {item.parentInTrash && item.parentName && (
          <Flex align="center" gap="1">
            <MaterialIcon name="info" size={14} color="var(--amber-11)" />
            <Text size="1" style={{ color: 'var(--amber-11)' }}>
              {t('collections.trash.parentInTrash', { folder: item.parentName })}
            </Text>
          </Flex>
        )}
      </Flex>

      {!isMobile && (
        <>
          <Box role="cell" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.location }}>
            <Text as="p" size="2" style={cellTextStyle}>{locationLabel(item, collectionName, t)}</Text>
          </Box>
          <Box role="cell" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.deletedAt }}>
            <Text as="p" size="2" style={cellTextStyle}>{formatTrashDate(item.deletedAtTimestamp, i18n.language)}</Text>
          </Box>
          <Box role="cell" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.deletedBy }}>
            <Text as="p" size="2" style={cellTextStyle}>{deletedByLabel(item, t)}</Text>
          </Box>
          <Box role="cell" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.removedAfter }}>
            <Text as="p" size="2" style={cellTextStyle}>{removalLabel(item, t, i18n.language)}</Text>
          </Box>
        </>
      )}

      <Flex role="cell" justify="end" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.actions }}>
        <LoadingButton
          size="1"
          variant="soft"
          loading={isRestoring}
          loadingLabel={t('collections.trash.restoring')}
          disabled={disabled}
          onClick={onRestore}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="restore_from_trash" size={16} />
          {t('collections.trash.restore')}
        </LoadingButton>
      </Flex>
    </Flex>
  );
}

export function RecentlyDeletedView({ kbId }: { kbId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const isMobile = useIsMobile();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<TrashListResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [restoringIds, setRestoringIds] = useState<Set<string>>(new Set());
  const [collectionName, setCollectionName] = useState('');
  // Only the newest list request may write, so a slow earlier page never replaces a later one.
  const latestRequest = useRef(0);

  const load = useCallback(
    async (targetPage: number) => {
      const request = ++latestRequest.current;
      setIsLoading(true);
      try {
        const result = await TrashApi.list(kbId, targetPage, TRASH_PAGE_SIZE);
        if (request !== latestRequest.current) return;
        const total = result.pagination?.totalCount ?? 0;
        if (result.items.length === 0 && targetPage > 1 && total > 0) {
          setPage(pageAfterRemoval(targetPage, TRASH_PAGE_SIZE, total));
          return;
        }
        setData(result);
        setLoadError(null);
        setSelected((previous) => new Set(result.items.filter((i) => previous.has(i.id)).map((i) => i.id)));
      } catch (error) {
        if (request !== latestRequest.current) return;
        setLoadError(getUserFacingErrorMessage(error, t('collections.trash.loadFailed')));
      } finally {
        if (request === latestRequest.current) setIsLoading(false);
      }
    },
    [kbId, t],
  );

  useEffect(() => {
    void load(page);
  }, [load, page]);

  useEffect(() => {
    let current = true;
    TrashApi.collectionName(kbId)
      .then((name) => {
        if (current) setCollectionName(name);
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [kbId]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const selectedItems = useMemo(() => items.filter((item) => selected.has(item.id)), [items, selected]);
  const isRestoring = restoringIds.size > 0;
  const allSelected = items.length > 0 && selectedItems.length === items.length;

  const toggle = useCallback((id: string) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const restore = useCallback(
    async (toRestore: TrashItem[]) => {
      if (toRestore.length === 0) return;
      const ordered = orderForRestore(toRestore);
      const ids = ordered.map((item) => item.id);
      setRestoringIds(new Set(ids));
      let outcome: RestoreOutcome;
      if (ordered.length === 1) {
        try {
          outcome = outcomeOfSingle(ordered[0], await TrashApi.restoreOne(ordered[0].id), t);
        } catch (error) {
          outcome = outcomeOfSingleError(ordered[0], error, t);
        }
      } else {
        try {
          outcome = outcomeOfBulk(ordered, await TrashApi.restoreMany(ids), t);
        } catch (error) {
          outcome = outcomeOfBulkError(ordered, error, t);
        }
      }
      setRestoringIds(new Set());
      const message = restoreMessage(outcome, t);
      toast[message.variant](message.title, {
        description: message.description,
        ...(message.variant === 'success' ? {} : { duration: PROBLEM_TOAST_MS }),
      });
      if (outcome.restored > 0) {
        setSelected(new Set());
        await load(page);
      }
    },
    [load, page, t],
  );

  const backToCollection = useCallback(() => {
    router.push(`/knowledge-base?nodeType=app&nodeId=${encodeURIComponent(kbId)}`);
  }, [router, kbId]);

  const pagination = data?.pagination;
  const from = pagination && pagination.totalCount > 0 ? (pagination.page - 1) * pagination.limit + 1 : 0;
  const to = pagination ? Math.min(pagination.page * pagination.limit, pagination.totalCount) : 0;
  const hasPrev = (pagination?.page ?? 1) > 1;
  const hasNext = !!pagination && pagination.page < pagination.totalPages;
  const title = t('collections.trash.title');

  return (
    <Flex direction="column" style={{ height: '100%', width: '100%', overflow: 'hidden' }}>
      <Flex
        align="center"
        justify="between"
        gap="3"
        style={{
          height: '40px',
          padding: isMobile ? `0 var(--space-2) 0 ${MOBILE_HAMBURGER_GUTTER_PX}px` : '0 var(--space-3)',
          borderBottom: '1px solid var(--olive-3)',
          backgroundColor: 'var(--effects-translucent)',
          backdropFilter: 'blur(8px)',
          flexShrink: 0,
        }}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <IconButton variant="ghost" size="1" color="gray" onClick={backToCollection} aria-label={t('collections.trash.back')} style={{ cursor: 'pointer' }}>
            <MaterialIcon name="arrow_back" size={18} color="var(--slate-11)" />
          </IconButton>
          {collectionName && (
            <>
              <Text size="2" weight="medium" style={{ ...cellTextStyle, cursor: 'pointer' }} onClick={backToCollection}>
                {collectionName}
              </Text>
              <MaterialIcon name="chevron_right" size={16} color="var(--slate-9)" />
            </>
          )}
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap' }}>
            {title}
          </Text>
        </Flex>
        <Button variant="ghost" size="1" color="gray" onClick={() => void load(page)} disabled={isLoading} style={{ cursor: 'pointer', fontSize: '14px' }}>
          <MaterialIcon name="refresh" size={16} color="var(--slate-11)" />
          {!isMobile && t('action.refresh')}
        </Button>
      </Flex>

      <Box style={{ padding: 'var(--space-3)', flexShrink: 0 }}>
        <Callout.Root color="gray" size="1" variant="surface">
          <Callout.Icon>
            <MaterialIcon name="schedule" size={16} />
          </Callout.Icon>
          <Callout.Text size="1">{retentionNote(data?.retention, t)}</Callout.Text>
        </Callout.Root>
      </Box>

      {selectedItems.length > 0 && (
        <Flex
          align="center"
          justify="between"
          gap="3"
          wrap="wrap"
          style={{ padding: '0 var(--space-3) var(--space-3)', flexShrink: 0 }}
        >
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('collections.trash.selected', { count: selectedItems.length })}
          </Text>
          <Flex gap="2">
            <Button variant="ghost" size="1" color="gray" disabled={isRestoring} onClick={() => setSelected(new Set())} style={{ cursor: 'pointer' }}>
              {t('collections.trash.clearSelection')}
            </Button>
            <LoadingButton
              size="1"
              loading={isRestoring && restoringIds.size > 1}
              loadingLabel={t('collections.trash.restoring')}
              disabled={isRestoring}
              onClick={() => void restore(selectedItems)}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="restore_from_trash" size={16} color="white" />
              {t('collections.trash.restoreSelected', { count: selectedItems.length })}
            </LoadingButton>
          </Flex>
        </Flex>
      )}

      <Flex role="table" aria-label={title} direction="column" style={{ flex: 1, minHeight: 0 }}>
        <Flex
          role="row"
          align="center"
          style={{
            height: 'var(--space-9)',
            borderTop: '1px solid var(--olive-3)',
            borderBottom: '1px solid var(--olive-3)',
            backgroundColor: 'var(--olive-2)',
            flexShrink: 0,
          }}
        >
          <Flex role="columnheader" align="center" justify="center" style={{ width: '38px', padding: '0 var(--space-2)', flexShrink: 0 }}>
            <Checkbox
              size="1"
              checked={allSelected}
              disabled={items.length === 0 || isRestoring}
              onCheckedChange={() => setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)))}
              aria-label={t('collections.trash.selectAll')}
              style={{ cursor: 'pointer' }}
            />
          </Flex>
          <Box role="columnheader" style={{ flex: 1, padding: '0 var(--space-2)' }}>
            <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>{t('collections.trash.columns.name')}</Text>
          </Box>
          {!isMobile && (
            <>
              <Box role="columnheader" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.location }}>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>{t('collections.trash.columns.location')}</Text>
              </Box>
              <Box role="columnheader" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.deletedAt }}>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>{t('collections.trash.columns.deletedAt')}</Text>
              </Box>
              <Box role="columnheader" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.deletedBy }}>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>{t('collections.trash.columns.deletedBy')}</Text>
              </Box>
              <Box role="columnheader" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.removedAfter }}>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>{t('collections.trash.columns.removedAfter')}</Text>
              </Box>
            </>
          )}
          <Box role="columnheader" style={{ ...headerCellStyle, width: COLUMN_WIDTHS.actions }} />
        </Flex>

        <Box className="no-scrollbar" style={{ flex: 1, overflowY: 'auto' }}>
          {isLoading && !data && !loadError ? (
            <TableSkeleton rows={5} hasRowActions hasCheckbox rowHeight={56} />
          ) : loadError && !data ? (
            <Flex direction="column" align="center" gap="3" style={{ padding: 'var(--space-8) var(--space-4)', textAlign: 'center' }}>
              <MaterialIcon name="error_outline" size={32} color="var(--slate-9)" />
              <Text size="2" style={{ color: 'var(--slate-11)', maxWidth: '28rem' }}>{loadError}</Text>
              <Button variant="soft" size="1" onClick={() => void load(page)} style={{ cursor: 'pointer' }}>
                {t('action.tryAgain')}
              </Button>
            </Flex>
          ) : items.length === 0 ? (
            <Flex direction="column" align="center" gap="2" style={{ padding: 'var(--space-8) var(--space-4)', textAlign: 'center' }}>
              <MaterialIcon name="delete_outline" size={32} color="var(--slate-9)" />
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>{t('collections.trash.emptyTitle')}</Text>
              <Text size="2" style={{ color: 'var(--slate-11)', maxWidth: '28rem' }}>{t('collections.trash.empty')}</Text>
            </Flex>
          ) : (
            items.map((item) => (
              <TrashRow
                key={item.id}
                item={item}
                collectionName={collectionName}
                isMobile={isMobile}
                isSelected={selected.has(item.id)}
                isRestoring={restoringIds.has(item.id) && restoringIds.size === 1}
                disabled={isRestoring}
                onSelect={() => toggle(item.id)}
                onRestore={() => void restore([item])}
              />
            ))
          )}
          {loadError && data && (
            <Box style={{ padding: 'var(--space-3)' }}>
              <Callout.Root color="red" size="1">
                <Callout.Text size="1">{loadError}</Callout.Text>
              </Callout.Root>
            </Box>
          )}
        </Box>
      </Flex>

      {pagination && pagination.totalCount > 0 && (
        <Flex
          justify="between"
          align="center"
          style={{
            padding: 'var(--space-2) var(--space-4)',
            borderTop: '1px solid var(--olive-3)',
            background: 'var(--olive-2)',
            flexShrink: 0,
          }}
        >
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {t('collections.trash.showing', { from, to, total: pagination.totalCount })}
          </Text>
          <Flex gap="3" align="center">
            <Button variant="ghost" size="1" color="gray" disabled={!hasPrev || isLoading} onClick={() => setPage((p) => p - 1)} style={{ cursor: 'pointer' }}>
              <MaterialIcon name="chevron_left" size={16} />
              {t('collections.trash.previous')}
            </Button>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{pagination.page}</Text>
            <Button variant="ghost" size="1" color="gray" disabled={!hasNext || isLoading} onClick={() => setPage((p) => p + 1)} style={{ cursor: 'pointer' }}>
              {t('collections.trash.next')}
              <MaterialIcon name="chevron_right" size={16} />
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}
