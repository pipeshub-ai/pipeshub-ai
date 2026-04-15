import { useKnowledgeBaseStore } from '../store';
import { KnowledgeHubApi } from '../api';
import { categorizeNodes } from './tree-builder';

/**
 * Refreshes the Collections sidebar tree by re-fetching the KB app's children.
 *
 * If the KB app node is already in the store, children are fetched and the tree
 * is rebuilt immediately. An optional `afterRefresh` callback runs after (e.g.
 * `reMergeCachedChildrenIntoTree`).
 *
 * If the KB app node isn't loaded yet (race on first load), root nodes are
 * re-fetched and `setAppNodes` is called, which queues the children-fetch effect
 * to run on the next render cycle.
 */
export async function refreshKbTree(afterRefresh?: () => void): Promise<void> {
  const { appNodes, setNodes, setCategorizedNodes, cacheAppChildren, setAppNodes } =
    useKnowledgeBaseStore.getState();

  const kbApp = appNodes.find((n) => n.connector === 'KB');

  if (kbApp) {
    const response = await KnowledgeHubApi.getNodeChildren('app', kbApp.id, {
      page: 1,
      limit: 50,
    });
    cacheAppChildren(kbApp.id, response.items);
    setNodes(response.items);

    // Re-add previously cached subfolder nodes so reMergeCachedChildrenIntoTree
    // can still find parent nodes for any re-expanded subtrees. setNodes above
    // replaces state.nodes with only the KB top-level children, which would
    // otherwise cause the merge to silently skip expanded folders.
    const { nodeChildrenCache: freshNodeChildren, addNodes } = useKnowledgeBaseStore.getState();
    const cachedSubfolderNodes = Array.from(freshNodeChildren.values()).flat();
    if (cachedSubfolderNodes.length > 0) {
      addNodes(cachedSubfolderNodes);
    }

    const categorized = categorizeNodes(response.items, `apps/${kbApp.id}`);
    setCategorizedNodes(categorized);
    afterRefresh?.();
  } else {
    // Fallback: KB app not yet in store — reload root nodes so the children-fetch
    // effect will pick up the KB app and populate the tree on the next render.
    const response = await KnowledgeHubApi.getRootNodes({ page: 1, limit: 100 });
    const appItems = response.items.filter((n) => n.nodeType === 'app');
    const kbApps = appItems.filter((n) => n.connector === 'KB');
    const connectorApps = appItems.filter((n) => n.connector !== 'KB');
    setAppNodes([...kbApps, ...connectorApps]);
  }
}
