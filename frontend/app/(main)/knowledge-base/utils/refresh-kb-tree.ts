import { useAuthStore } from '@/lib/store/auth-store';
import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { SIDEBAR_PAGINATION_PAGE_SIZE } from '../constants';
import { categorizeNodes } from './tree-builder';
import { isKbCollectionsHubApp } from './all-records-transformer';
import { getCollectionsHubBootstrapFromToken } from './collections-hub-app';
import { fetchAllKbAppContainerChildren } from './fetch-all-kb-hub-children';

/**
 * Refreshes the Collections sidebar tree by re-fetching the KB app's children.
 *
 * If the KB app node is already in the store, children are fetched and the tree
 * is rebuilt immediately. An optional `afterRefresh` callback runs after (e.g.
 * `reMergeCachedChildrenIntoTree`).
 *
 * If the KB app node isn't loaded yet (race on first load), the org-scoped hub
 * `knowledgeBase_<orgId>` is resolved from the JWT and merged into `appNodes`
 * without dropping other root apps. If that fails, root apps are re-fetched from
 * navigation (All Records compatibility).
 */
export async function refreshKbTree(afterRefresh?: () => void): Promise<void> {
  const {
    appNodes,
    setNodes,
    setCategorizedNodes,
    cacheAppChildren,
    setAppNodes,
    setAppChildPagination,
    setAppRootListPagination,
  } = useKnowledgeBaseStore.getState();

  let kbApp = appNodes.find((n) => isKbCollectionsHubApp(n));

  if (!kbApp) {
    const boot = getCollectionsHubBootstrapFromToken(useAuthStore.getState().accessToken);
    if (boot.ok) {
      const nonKb = appNodes.filter((n) => !isKbCollectionsHubApp(n));
      setAppNodes([boot.app, ...nonKb]);
      if (nonKb.length === 0) {
        setAppRootListPagination(null);
      }
      kbApp = boot.app;
    } else {
      const response = await KnowledgeHubApi.getNavigationNodes({
        page: 1,
        limit: SIDEBAR_PAGINATION_PAGE_SIZE,
        include: 'counts',
        sortBy: 'updatedAt',
        sortOrder: 'desc',
      });
      const appItems = response.items.filter((n) => n.nodeType === 'app');
      const kbApps = appItems.filter((n) => isKbCollectionsHubApp(n));
      const connectorApps = appItems.filter((n) => !isKbCollectionsHubApp(n));
      setAppNodes([...kbApps, ...connectorApps]);
      const p = response.pagination;
      setAppRootListPagination(
        p
          ? {
              hasNext: p.hasNext,
              nextPage: p.hasNext ? p.page + 1 : p.page,
            }
          : null
      );
      kbApp = useKnowledgeBaseStore.getState().appNodes.find((n) => isKbCollectionsHubApp(n));
      if (!kbApp) {
        return;
      }
    }
  }

  const mergedItems = await fetchAllKbAppContainerChildren(kbApp.id);

  cacheAppChildren(kbApp.id, mergedItems);
  setNodes(mergedItems);
  setAppChildPagination(kbApp.id, { hasNext: false, nextPage: 1 });

  const { nodeChildrenCache: freshNodeChildren, addNodes } = useKnowledgeBaseStore.getState();
  const cachedSubfolderNodes = Array.from(freshNodeChildren.values()).flat();
  if (cachedSubfolderNodes.length > 0) {
    addNodes(cachedSubfolderNodes);
  }

  const categorized = categorizeNodes(mergedItems, `apps/${kbApp.id}`);
  setCategorizedNodes(categorized);
  afterRefresh?.();
}
