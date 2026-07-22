'use client';

import React, { useCallback, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { MaterialIcon } from '@/app/components/ui';
import { IndexingStatsPanel } from '@/app/components/indexing-stats/indexing-stats-panel';
import { useKnowledgeBaseStore } from '../store';
import { KnowledgeBaseApi } from '../api';
import { useToastStore } from '@/lib/store/toast-store';
import type { IndexingStatus } from '../types';

export function CollectionStatsPanel() {
  const { t } = useTranslation();
  const router = useRouter();
  const collectionStatsPanel = useKnowledgeBaseStore((s) => s.collectionStatsPanel);
  const closeCollectionStatsPanel = useKnowledgeBaseStore((s) => s.closeCollectionStatsPanel);
  const collectionStats = useKnowledgeBaseStore((s) => s.collectionStats);
  const setCollectionStats = useKnowledgeBaseStore((s) => s.setCollectionStats);
  const setCollectionStatsLoading = useKnowledgeBaseStore((s) => s.setCollectionStatsLoading);
  const collectionStatsLoading = useKnowledgeBaseStore((s) => s.collectionStatsLoading);
  const addToast = useToastStore((s) => s.addToast);

  const [isRefreshBusy, setIsRefreshBusy] = useState(false);
  const [isReindexFailedBusy, setIsReindexFailedBusy] = useState(false);
  const [isManualIndexBusy, setIsManualIndexBusy] = useState(false);

  const collectionId = collectionStatsPanel.collectionId;
  const collectionName = collectionStatsPanel.collectionName;
  const stats = collectionId ? collectionStats[collectionId] : null;

  const fetchStats = useCallback(
    async (force = false) => {
      if (!collectionId) return;
      const existingStats = collectionStats[collectionId];
      if (!force && existingStats) return;
      try {
        setCollectionStatsLoading(true);
        const response = await KnowledgeBaseApi.getCollectionStats(collectionId);
        if (response.success && response.data) {
          setCollectionStats(collectionId, response.data);
        }
      } catch (error) {
        console.error('Failed to fetch collection stats', { collectionId, error });
        addToast({
          variant: 'error',
          title: t('collections.stats.fetchError', { defaultValue: 'Failed to load indexing stats' }),
        });
      } finally {
        setCollectionStatsLoading(false);
      }
    },
    [collectionId, collectionStats, setCollectionStats, setCollectionStatsLoading, addToast, t]
  );

  useEffect(() => {
    if (collectionStatsPanel.open && collectionId) {
      void fetchStats(true);
    }
  }, [collectionStatsPanel.open, collectionId]);

  const handleRefresh = useCallback(async () => {
    if (!collectionId || isRefreshBusy) return;
    try {
      setIsRefreshBusy(true);
      await fetchStats(true);
      addToast({
        variant: 'success',
        title: t('collections.stats.refreshSuccess'),
      });
    } catch (error) {
      console.error('Failed to refresh stats', { collectionId, error });
      addToast({
        variant: 'error',
        title: t('collections.stats.refreshError'),
      });
    } finally {
      setIsRefreshBusy(false);
    }
  }, [collectionId, isRefreshBusy, fetchStats, addToast, t]);

  const handleReindexFailed = useCallback(async () => {
    if (!collectionId || isReindexFailedBusy) return;
    try {
      setIsReindexFailedBusy(true);
      await KnowledgeBaseApi.reindexKnowledgeBase(collectionId, ['FAILED']);
      addToast({
        variant: 'success',
        title: t('collections.stats.reindexFailedStart'),
      });
      await fetchStats(true);
    } catch (error) {
      console.error('Failed to reindex failed records', { collectionId, error });
      addToast({
        variant: 'error',
        title: t('collections.stats.reindexFailedError'),
      });
    } finally {
      setIsReindexFailedBusy(false);
    }
  }, [collectionId, isReindexFailedBusy, fetchStats, addToast, t]);

  const handleManualIndex = useCallback(async () => {
    if (!collectionId || isManualIndexBusy) return;
    try {
      setIsManualIndexBusy(true);
      await KnowledgeBaseApi.reindexKnowledgeBase(collectionId, ['AUTO_INDEX_OFF']);
      addToast({
        variant: 'success',
        title: t('collections.stats.manualIndexStart'),
      });
      await fetchStats(true);
    } catch (error) {
      console.error('Failed to start manual indexing', { collectionId, error });
      addToast({
        variant: 'error',
        title: t('collections.stats.manualIndexError'),
      });
    } finally {
      setIsManualIndexBusy(false);
    }
  }, [collectionId, isManualIndexBusy, fetchStats, addToast, t]);

  const handleNavigateToRecords = useCallback(
    (indexingStatuses?: IndexingStatus[]) => {
      if (!collectionId) return;

      const params = new URLSearchParams();
      params.set('view', 'all-records');
      params.set('kbIds', collectionId);
      if (indexingStatuses && indexingStatuses.length > 0) {
        params.set('indexingStatus', indexingStatuses.join(','));
      }

      closeCollectionStatsPanel();
      router.push(`/knowledge-base?${params.toString()}`);
    },
    [collectionId, closeCollectionStatsPanel, router]
  );

  return (
    <WorkspaceRightPanel
      open={collectionStatsPanel.open}
      onOpenChange={(open) => {
        if (!open) closeCollectionStatsPanel();
      }}
      title={collectionName || t('collections.stats.title', { defaultValue: 'Analytics' })}
      icon={<MaterialIcon name="analytics" size={20} color="var(--emerald-11)" />}
      hideFooter
    >
      <IndexingStatsPanel
        stats={stats}
        loading={collectionStatsLoading}
        onRefresh={handleRefresh}
        onReindexFailed={handleReindexFailed}
        onManualIndex={handleManualIndex}
        onNavigateToRecords={handleNavigateToRecords}
        showSyncActions={true}
        isRefreshBusy={isRefreshBusy}
        isReindexFailedBusy={isReindexFailedBusy}
        isManualIndexBusy={isManualIndexBusy}
      />
    </WorkspaceRightPanel>
  );
}
