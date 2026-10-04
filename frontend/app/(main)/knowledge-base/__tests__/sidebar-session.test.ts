import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useAuthStore } from '@/lib/store/auth-store';
import { useKnowledgeBaseStore } from '../store';
import { resetKnowledgeBaseSession } from '../utils/sidebar-session';
import { openFolderChildren } from '../utils/folder-children';
import { refreshKbTree } from '../utils/refresh-kb-tree';
import { fetchAppDirectChildren } from '../utils/fetch-app-direct-children';
import { loadMoreAppChildPage } from '../utils/sidebar-paginated-fetch';
import { collection, hubNode, hubResponse } from './kb-page-harness';

const getNavigationNodes = vi.hoisted(() => vi.fn());
const getNodeChildren = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({
  KnowledgeHubApi: { getNavigationNodes, getNodeChildren },
  forgetPendingNodeChildrenRequests: () => {},
}));

const ENGINEERING = collection('kb-eng', 'Engineering');
const DESIGNS = hubNode({ id: 'folder-designs', name: 'Designs', nodeType: 'folder', parentId: 'kb-eng' });

function signIn(userId: string) {
  useAuthStore.setState({ isAuthenticated: true, accessToken: 'token', user: { id: userId } });
}

function held<T>() {
  let release: (value: T) => void = () => {};
  const promise = new Promise<T>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

beforeEach(() => {
  resetKnowledgeBaseSession();
  getNavigationNodes.mockReset();
  getNodeChildren.mockReset();
  signIn('user-a');
  const kb = useKnowledgeBaseStore.getState();
  kb.setAppNodes([ENGINEERING]);
  kb.setNodes([ENGINEERING]);
  kb.setCategorizedNodes({ shared: [], private: [] });
});

describe('knowledge base state across sign-out', () => {
  it('forgets the previous sidebar when the user signs out', () => {
    const kb = useKnowledgeBaseStore.getState();
    kb.cacheNodeChildren('kb-eng', [DESIGNS]);
    kb.setNodeChildrenPagination('kb-eng', { hasNext: true, nextPage: 2, nodeType: 'app' });

    useAuthStore.getState().logout();

    const after = useKnowledgeBaseStore.getState();
    expect(after.appNodes).toEqual([]);
    expect(after.nodes).toEqual([]);
    expect(after.categorizedNodes).toBeNull();
    expect(after.nodeChildrenCache.size).toBe(0);
    expect(after.nodeChildrenPagination.size).toBe(0);
  });

  it('forgets the previous sidebar when a different user signs in', () => {
    useKnowledgeBaseStore.getState().cacheNodeChildren('kb-eng', [DESIGNS]);

    signIn('user-b');

    expect(useKnowledgeBaseStore.getState().appNodes).toEqual([]);
    expect(useKnowledgeBaseStore.getState().nodeChildrenCache.size).toBe(0);
  });

  it('keeps the sidebar when the same user refreshes their session', () => {
    signIn('user-a');

    expect(useKnowledgeBaseStore.getState().appNodes.map((n) => n.id)).toEqual(['kb-eng']);
  });

  it('drops a folder list that arrives after sign-out', async () => {
    const response = held<ReturnType<typeof hubResponse>>();
    getNodeChildren.mockReturnValue(response.promise);

    const opening = openFolderChildren('kb-eng', 'app');
    useAuthStore.getState().logout();
    signIn('user-b');
    response.release(hubResponse([DESIGNS]));
    await opening;

    expect(useKnowledgeBaseStore.getState().nodeChildrenCache.has('kb-eng')).toBe(false);
  });

  it('drops a collections refresh that arrives after sign-out', async () => {
    const response = held<ReturnType<typeof hubResponse>>();
    getNavigationNodes.mockReturnValue(response.promise);

    const refreshing = refreshKbTree();
    useAuthStore.getState().logout();
    response.release(hubResponse([collection('kb-old', 'Previous user')]));
    await refreshing;

    expect(useKnowledgeBaseStore.getState().appNodes).toEqual([]);
    expect(useKnowledgeBaseStore.getState().nodes).toEqual([]);
  });

  it('drops an All Records app listing that arrives after sign-out', async () => {
    const response = held<ReturnType<typeof hubResponse>>();
    getNodeChildren.mockReturnValue(response.promise);

    const fetching = fetchAppDirectChildren('kb-eng');
    useAuthStore.getState().logout();
    response.release(hubResponse([DESIGNS]));
    await fetching;

    expect(useKnowledgeBaseStore.getState().appChildrenCache.size).toBe(0);
  });

  it('drops an All Records "load more" page that arrives after sign-out, even after the next user loads', async () => {
    const old = held<ReturnType<typeof hubResponse>>();
    const kb = useKnowledgeBaseStore.getState();
    kb.cacheAppChildren('kb-eng', [DESIGNS]);
    kb.setAppChildPagination('kb-eng', { hasNext: true, nextPage: 2 });
    getNodeChildren.mockReturnValueOnce(old.promise);

    const loadingMore = loadMoreAppChildPage('kb-eng');
    useAuthStore.getState().logout();
    signIn('user-b');
    const next = useKnowledgeBaseStore.getState();
    next.setAppNodes([collection('kb-new', 'Next user')]);
    next.cacheAppChildren('kb-new', [hubNode({ id: 'folder-next', name: 'Next', nodeType: 'folder', parentId: 'kb-new' })]);
    old.release(hubResponse([hubNode({ id: 'folder-old', name: 'Old', nodeType: 'folder', parentId: 'kb-eng' })]));
    await loadingMore;

    const after = useKnowledgeBaseStore.getState();
    expect(after.appChildrenCache.has('kb-eng')).toBe(false);
    expect(after.appChildrenCache.get('kb-new')?.map((n) => n.id)).toEqual(['folder-next']);
    expect(after.connectorAppTrees.has('kb-eng')).toBe(false);
  });
});
