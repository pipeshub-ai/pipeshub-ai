'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ToolsetsApi,
  type BuilderSidebarToolset,
  type ToolsetsFilterCounts,
  type ToolsetsListPagination,
} from '@/app/(main)/toolsets/api';
import { useToastStore } from '@/lib/store/toast-store';
import type { ActionInstanceAuthTab } from '../components/action-type-details-layout';

const TYPE_INSTANCE_PAGE_SIZE = 20;

export interface UseToolsetTypeInstanceListOptions {
  toolsetTypeParam: string | null;
  instanceFilterTab: ActionInstanceAuthTab;
  refreshKey: number;
  /** Team page passes `isAdmin`; personal always passes true when on type detail. */
  enabled: boolean;
}

/**
 * Paginated GET /my-toolsets for a single `toolsetType` (workspace actions type detail).
 */
export function useToolsetTypeInstanceList({
  toolsetTypeParam,
  instanceFilterTab,
  refreshKey,
  enabled,
}: UseToolsetTypeInstanceListOptions) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [instanceSearch, setInstanceSearch] = useState('');
  const [debouncedInstanceSearch, setDebouncedInstanceSearch] = useState('');
  const [typeListPage, setTypeListPage] = useState(1);
  const [typeListInstances, setTypeListInstances] = useState<BuilderSidebarToolset[]>([]);
  const [typeListPagination, setTypeListPagination] = useState<ToolsetsListPagination | null>(null);
  const [typeListFilterCounts, setTypeListFilterCounts] = useState<ToolsetsFilterCounts | null>(null);
  const [typeListLoading, setTypeListLoading] = useState(false);
  const [typeListRefreshing, setTypeListRefreshing] = useState(false);
  const typeListHasLoadedForScopeRef = useRef(false);

  useEffect(() => {
    setInstanceSearch('');
    setDebouncedInstanceSearch('');
    setTypeListPage(1);
  }, [toolsetTypeParam]);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedInstanceSearch(instanceSearch.trim()), 300);
    return () => window.clearTimeout(id);
  }, [instanceSearch]);

  useEffect(() => {
    setTypeListPage(1);
  }, [toolsetTypeParam, instanceFilterTab, debouncedInstanceSearch]);

  useEffect(() => {
    typeListHasLoadedForScopeRef.current = false;
  }, [toolsetTypeParam, instanceFilterTab, typeListPage, debouncedInstanceSearch]);

  useEffect(() => {
    if (!enabled || !toolsetTypeParam) return;
    let cancelled = false;
    const soft = typeListHasLoadedForScopeRef.current;
    if (soft) {
      setTypeListLoading(false);
      setTypeListRefreshing(true);
    } else {
      setTypeListRefreshing(false);
      setTypeListLoading(true);
    }
    void (async () => {
      try {
        const authStatus =
          instanceFilterTab === 'authenticated'
            ? ('authenticated' as const)
            : instanceFilterTab === 'not_authenticated'
              ? ('not-authenticated' as const)
              : undefined;
        const res = await ToolsetsApi.getMyToolsets({
          page: typeListPage,
          limit: TYPE_INSTANCE_PAGE_SIZE,
          search: debouncedInstanceSearch || undefined,
          includeRegistry: false,
          toolsetType: toolsetTypeParam,
          authStatus,
        });
        if (cancelled) return;
        setTypeListInstances(res.toolsets);
        setTypeListPagination(res.pagination ?? null);
        if (res.filterCounts) {
          setTypeListFilterCounts({
            all: res.filterCounts.all,
            authenticated: res.filterCounts.authenticated,
            notAuthenticated: res.filterCounts.notAuthenticated,
          });
        } else {
          setTypeListFilterCounts(null);
        }
        typeListHasLoadedForScopeRef.current = true;
      } catch {
        if (!cancelled) {
          addToast({ variant: 'error', title: t('workspace.actions.loadError') });
        }
      } finally {
        if (!cancelled) {
          setTypeListLoading(false);
          setTypeListRefreshing(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    addToast,
    debouncedInstanceSearch,
    enabled,
    instanceFilterTab,
    t,
    toolsetTypeParam,
    typeListPage,
    refreshKey,
  ]);

  return {
    instanceSearch,
    setInstanceSearch,
    typeListPage,
    setTypeListPage,
    typeListInstances,
    typeListPagination,
    typeListFilterCounts,
    typeListLoading,
    typeListRefreshing,
  };
}
