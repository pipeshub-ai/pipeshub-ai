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
const EMPTY_INSTANCES: BuilderSidebarToolset[] = [];

export interface UseToolsetTypeInstanceListOptions {
  toolsetTypeParam: string | null;
  instanceFilterTab: ActionInstanceAuthTab;
  refreshKey: number;
  /** Team page passes `isAdmin`; personal always passes true when on type detail. */
  enabled: boolean;
  /** `org` on the team page: the organization's instances, without the admin's personal ones. */
  scope?: 'org';
}

/**
 * Paginated GET /my-toolsets for a single `toolsetType` (workspace actions type detail).
 */
export function useToolsetTypeInstanceList({
  toolsetTypeParam,
  instanceFilterTab,
  refreshKey,
  enabled,
  scope,
}: UseToolsetTypeInstanceListOptions) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [instanceSearch, setInstanceSearch] = useState('');
  const [debouncedInstanceSearch, setDebouncedInstanceSearch] = useState('');
  const [typeListPage, setTypeListPage] = useState(1);
  /** Tagged with the type it was fetched for, so another type's rows and counts never render. */
  const [typeList, setTypeList] = useState<{
    toolsetType: string;
    instances: BuilderSidebarToolset[];
    pagination: ToolsetsListPagination | null;
    filterCounts: ToolsetsFilterCounts | null;
  } | null>(null);
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
          scope,
        });
        if (cancelled) return;
        setTypeList({
          toolsetType: toolsetTypeParam,
          instances: res.toolsets,
          pagination: res.pagination ?? null,
          filterCounts: res.filterCounts
            ? {
                all: res.filterCounts.all,
                authenticated: res.filterCounts.authenticated,
                notAuthenticated: res.filterCounts.notAuthenticated,
              }
            : null,
        });
        typeListHasLoadedForScopeRef.current = true;
      } catch {
        if (!cancelled) {
          // Record this type as loaded and empty, so the empty state shows instead of a stuck spinner.
          setTypeList((prev) =>
            prev?.toolsetType === toolsetTypeParam
              ? prev
              : { toolsetType: toolsetTypeParam, instances: [], pagination: null, filterCounts: null }
          );
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
    scope,
  ]);

  const current = typeList?.toolsetType === toolsetTypeParam ? typeList : null;

  return {
    instanceSearch,
    setInstanceSearch,
    typeListPage,
    setTypeListPage,
    typeListInstances: current?.instances ?? EMPTY_INSTANCES,
    typeListPagination: current?.pagination ?? null,
    typeListFilterCounts: current?.filterCounts ?? null,
    // Also true for the render before the fetch effect runs on a type switch.
    typeListLoading: typeListLoading || (enabled && Boolean(toolsetTypeParam) && !current),
    typeListRefreshing,
  };
}
