'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import {
  groupByTime,
  getNonEmptyGroups,
} from '@/lib/utils/group-by-time';
import { CollectionRow } from './collection-row';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

// ── Types ──

export interface CollectionSelectItem {
  id: string;
  name: string;
  updatedAt: number;
  counts?: {
    files: number;
    folders: number;
  };
}

// ── Transform API response ──

function transformToCollectionItems(
  knowledgeBases: KnowledgeBaseForChat[]
): CollectionSelectItem[] {
  return knowledgeBases.map((kb) => ({
    id: kb.id,
    name: kb.name,
    updatedAt: kb.updatedAtTimestamp ?? kb.createdAtTimestamp ?? 0,
  }));
}

// ── Component ──

interface CollectionsTabProps {
  selectedKbIds: string[];
  onToggleKb: (kbId: string) => void;
}

/**
 * Collections tab content showing a search bar and time-grouped
 * list of selectable collections. Fetches data lazily on mount.
 */
export function CollectionsTab({
  selectedKbIds,
  onToggleKb,
}: CollectionsTabProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [collections, setCollections] = useState<CollectionSelectItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const setCollectionNamesCache = useChatStore((s) => s.setCollectionNamesCache);
  const { t } = useTranslation();

  // Fetch collections on mount (search is performed locally)
  const fetchCollections = useCallback(async () => {
    try {
      setIsLoading(true);
      setHasError(false);
      const response = await ChatApi.listCollectionsForChat();
      const items = transformToCollectionItems(response.knowledgeBases);
      setCollections(items);

      // Populate collection name cache for display in ChatInput cards
      const nameMap: Record<string, string> = {};
      items.forEach((item) => { nameMap[item.id] = item.name; });
      setCollectionNamesCache(nameMap);
    } catch (err) {
      setHasError(true);
      console.error('Error fetching collections for chat:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  // Group collections by time, filtered locally by search query
  const groupedCollections = useMemo(() => {
    const filtered = searchQuery
      ? collections.filter((c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase())
        )
      : collections;
    const groups = groupByTime(filtered, (c) => c.updatedAt);
    return getNonEmptyGroups(groups);
  }, [collections, searchQuery]);

  return (
    <Flex direction="column" gap="2" style={{ flex: 1, overflow: 'hidden' }}>
      {/* Search bar */}
      <Flex align="center" gap="1" style={{ width: '98%' }}>
        <Flex
          align="center"
          gap="2"
          style={{
            flex: 1,
            height: 'var(--space-7)', /* was: 36px, delta: +4px */
            border: '1px solid var(--slate-a5)',
            borderRadius: 'var(--radius-2)',
            paddingLeft: 'var(--space-2)',
            paddingRight: 'var(--space-2)',
            backgroundColor: 'var(--slate-1)',
          }}
        >
          <MaterialIcon
            name="search"
            size={ICON_SIZES.PRIMARY}
            color="var(--slate-9)"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('chat.searchCollections')}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: 'var(--slate-12)',
              fontSize: 'var(--font-size-2)', /* was: 13px, delta: +1px */
              fontFamily: 'inherit',
            }}
          />
        </Flex>
        {/* Filter button — no-op for v0 */}
        <IconButton variant="ghost" color="gray" size="2" style={{width: 'var(--spacing-6)', marginLeft: 'var(--space-1)'}}>
          <MaterialIcon
            name="filter_list"
            size={ICON_SIZES.SECONDARY}
            color="var(--slate-11)"
          />
        </IconButton>
      </Flex>

      {/* Scrollable list */}
      <Flex
        direction="column"
        gap="1"
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
        }}
      >
        {isLoading && (
          <Flex
            align="center"
            justify="center"
            style={{ padding: 'var(--space-4)' }}
          >
           <LottieLoader variant="loader" size={48} />
          </Flex>
        )}

        {hasError && !isLoading && (
          <Flex
            align="center"
            justify="center"
            style={{ padding: 'var(--space-4)' }}
          >
            <Text size="2" style={{ color: 'var(--red-9)' }}>
              {t('chat.failedToLoadCollections')}
            </Text>
          </Flex>
        )}

        {!isLoading && !hasError && collections.length === 0 && (
          <Flex
            align="center"
            justify="center"
            style={{ padding: 'var(--space-4)' }}
          >
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {searchQuery ? t('message.noCollections') : t('chat.noCollectionsAvailable')}
            </Text>
          </Flex>
        )}

        {!isLoading &&
          !hasError &&
          groupedCollections.map(([label, items]) => (
            <Flex key={label} direction="column" gap="1">
              {/* Section label — matches TimeGroup styling */}
              <Flex
                align="center"
                style={{
                  height: '28px',
                  paddingLeft: 'var(--space-1)',
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 400,
                    lineHeight: 'var(--line-height-1)',
                    letterSpacing: '0.04px',
                    color: 'var(--olive-9)',
                  }}
                >
                  {label}
                </span>
              </Flex>

              {/* Collection rows */}
              {items.map((col) => (
                <CollectionRow
                  key={col.id}
                  id={col.id}
                  name={col.name}
                  isSelected={selectedKbIds.includes(col.id)}
                  onToggle={onToggleKb}
                  counts={col.counts}
                />
              ))}
            </Flex>
          ))}
      </Flex>
    </Flex>
  );
}
