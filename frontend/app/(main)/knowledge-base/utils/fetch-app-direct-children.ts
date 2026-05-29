import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { useKnowledgeBaseStore } from '../store';
import { buildConnectorAppSidebarTree, categorizeNodes } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';

/** expandedSections key for an app row in All Records sidebar */
export function appSectionKey(appId: string): string {
  return `app:${appId}`;
}

/**
 * Fetches the first page of direct children for a hub app node and updates store
 * caches/trees. Idempotent when children are already cached.
 */
export async function fetchAppDirectChildren(appId: string): Promise<void> {
  const state = useKnowledgeBaseStore.getState();
  if (state.appChildrenCache.has(appId)) return;

  const app = state.appNodes.find((a) => a.id === appId);
  if (!app) return;

  const {
    cacheAppChildren,
    setAppChildPagination,
    setAppLoading,
    setNodes,
    setCategorizedNodes,
    setConnectorAppTree,
    addNodes,
  } = useKnowledgeBaseStore.getState();

  setAppLoading(appId, true);
  try {
    const response = await KnowledgeHubApi.getNodeChildren('app', appId, {
      onlyContainers: true,
      page: 1,
      limit: SIDEBAR_PAGINATION_PAGE_SIZE,
      sortBy: 'name',
      sortOrder: 'asc',
    });

    cacheAppChildren(appId, response.items);

    const pag = response.pagination;
    setAppChildPagination(
      appId,
      pag
        ? {
            hasNext: pag.hasNext,
            nextPage: pag.hasNext ? pag.page + 1 : pag.page,
          }
        : { hasNext: false, nextPage: 1 }
    );

    const isKbApp = isKbCollectionsHubApp(app);
    if (isKbApp) {
      setNodes(response.items);
      const categorized = categorizeNodes(response.items, `apps/${appId}`);
      setCategorizedNodes(categorized);
    } else {
      addNodes(response.items);
      setConnectorAppTree(appId, buildConnectorAppSidebarTree(appId, response.items));
    }
  } catch (error) {
    console.error(`Error fetching children for app ${app.name}:`, error);
    throw error;
  } finally {
    setAppLoading(appId, false);
  }
}
