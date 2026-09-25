import React, { useSyncExternalStore } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { useUploadStore } from '@/lib/store/upload-store';
import { useAuthStore } from '@/lib/store/auth-store';
import { useKnowledgeBaseStore } from '../store';
import { resetKnowledgeBaseSession } from '../utils/sidebar-session';
import KnowledgeBasePage from '../page';
import KnowledgeBaseSidebarSlot from '../../@sidebar/knowledge-base/page';
import { loadMoreNodeChildrenPage, loadMoreRootAppList } from '../utils/sidebar-paginated-fetch';
import { mergeChildrenIntoTree } from '../utils/tree-builder';
import type { EnhancedFolderTreeNode } from '../types';
import {
  collection,
  createNavigation,
  folderResponse,
  hubChildrenFake,
  hubNode,
  hubResponse,
  openRowMenu,
  queryRow,
  installBrowserShims,
  renderInTheme,
  row,
} from './kb-page-harness';

// Every test here mounts the whole page (some with the sidebar and dozens of
// rows); on a busy machine under coverage they run past Vitest's 5 s default.
vi.setConfig({ testTimeout: 20_000 });

const nav = vi.hoisted(() => ({ current: null as ReturnType<typeof createNavigation> | null }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => useSyncExternalStore(nav.current!.subscribe, nav.current!.getParams),
  usePathname: () => '/knowledge-base',
}));

const api = vi.hoisted(() => ({
  hub: {
    getNavigationNodes: vi.fn(),
    loadFolderData: vi.fn(),
    getAllRootItems: vi.fn(),
    getNodeChildren: vi.fn(),
  },
  kb: {
    getUploadLimits: vi.fn(),
    createKnowledgeBase: vi.fn(),
    createFolder: vi.fn(),
    renameKnowledgeBase: vi.fn(),
    renameNode: vi.fn(),
    renameRecord: vi.fn(),
    renameFolder: vi.fn(),
    deleteNode: vi.fn(),
    streamUpload: vi.fn(),
    getRecordDetails: vi.fn(),
    streamRecord: vi.fn(),
    streamDownloadRecord: vi.fn(),
    reindexItem: vi.fn(),
    reindexRecordGroup: vi.fn(),
    reindexKnowledgeBase: vi.fn(),
    moveItem: vi.fn(),
    replaceRecord: vi.fn(),
    bulkReindex: vi.fn(),
    bulkDelete: vi.fn(),
    getCollectionStats: vi.fn(),
  },
}));

vi.mock('../api', () => ({
  KnowledgeHubApi: api.hub,
  KnowledgeBaseApi: api.kb,
  forgetPendingNodeChildrenRequests: () => {},
}));

const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => ({
  useUserPermission: (key: string) => !permissions.denied.has(key),
  PermissionLockIcon: () => <span>Locked</span>,
  usePermissionDeniedDialog: () => ({
    openDenied: () => {},
    guard: <A extends unknown[], R>(_allowed: boolean, fn: (...args: A) => R) => fn,
    dialog: null,
  }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ label }: { label?: string }) => <div role="status">{label ?? 'Loading'}</div>,
}));

vi.mock('@/app/components/file-preview', () => ({
  FilePreviewSidebar: ({
    file,
    isLoading,
    error,
    onOpenChange,
  }: {
    file: { name: string; url: string };
    isLoading?: boolean;
    error?: string;
    onOpenChange: (open: boolean) => void;
  }) => (
    <section aria-label="File preview">
      <h2>{file.name}</h2>
      {isLoading && <p>Loading preview</p>}
      {error && <p role="alert">{error}</p>}
      {file.url && <p>Showing {file.url}</p>}
      <button onClick={() => onOpenChange(false)}>Close preview</button>
    </section>
  ),
  FilePreviewFullscreen: () => null,
}));

vi.mock('@/app/components/share', () => ({
  ShareHeaderGroup: ({ onShareClick }: { onShareClick: () => void }) => (
    <button onClick={onShareClick}>Share</button>
  ),
  ShareSidebar: ({ open }: { open: boolean }) => (open ? <aside aria-label="Share collection" /> : null),
}));

const share = vi.hoisted(() => ({ getSharedMembers: vi.fn() }));
vi.mock('../share-adapter', () => ({
  createKBShareAdapter: () => ({ getSharedMembers: share.getSharedMembers }),
}));

vi.mock('../components/collection-stats-panel', () => ({ CollectionStatsPanel: () => null }));

const upload = vi.hoisted(() => ({ items: [] as unknown[] }));
vi.mock('../components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components')>();
  return {
    ...actual,
    UploadDataSidebar: ({ open, onSave }: { open: boolean; onSave: (items: never[]) => void }) =>
      open ? <button onClick={() => onSave(upload.items as never[])}>Upload chosen files</button> : null,
    MoveFolderSidebar: ({
      open,
      onMove,
      onExpand,
    }: {
      open: boolean;
      onMove: (parentId: string) => void;
      onExpand?: (nodeId: string) => Promise<void>;
    }) =>
      open ? (
        <div>
          <button onClick={() => onMove('folder-archive')}>Move into Archive</button>
          <button onClick={() => void onExpand?.('kb-eng')}>Open Engineering in the move dialog</button>
        </div>
      ) : null,
    ReplaceFileDialog: ({
      open,
      item,
      onReplace,
    }: {
      open: boolean;
      item: { name: string } | null;
      onReplace: (item: unknown, file: File) => void;
    }) =>
      open && item ? (
        <button onClick={() => onReplace(item, new File(['v2'], 'spec-v2.pdf'))}>Replace with spec-v2.pdf</button>
      ) : null,
  };
});

beforeEach(() => {
  installBrowserShims();
  nav.current = createNavigation();
  router.push.mockImplementation((url: string) => nav.current!.setUrl(url));
  router.replace.mockImplementation((url: string) => nav.current!.setUrl(url));
  permissions.denied.clear();
  // Loads a previous test left in flight must not leak into this one.
  resetKnowledgeBaseSession();
  useKnowledgeBaseStore.setState(useKnowledgeBaseStore.getInitialState(), true);
  useToastStore.setState({ toasts: [] });
  useUploadStore.setState(useUploadStore.getInitialState(), true);
  api.kb.getUploadLimits.mockResolvedValue({ maxFileSizeBytes: 5 * 1024 * 1024 });
  api.hub.getNodeChildren.mockResolvedValue(hubResponse([]));
  share.getSharedMembers.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function sidebarIds() {
  const tree = useKnowledgeBaseStore.getState().categorizedNodes;
  return [...(tree?.shared ?? []), ...(tree?.private ?? [])].map((n) => n.id);
}

function engineeringChildIds() {
  const tree = useKnowledgeBaseStore.getState().categorizedNodes;
  const node = [...(tree?.shared ?? []), ...(tree?.private ?? [])].find((n) => n.id === 'kb-eng');
  return (node?.children ?? []).map((c) => c.id);
}

function childIdsOf(id: string) {
  const tree = useKnowledgeBaseStore.getState().categorizedNodes;
  const find = (nodes: EnhancedFolderTreeNode[]): EnhancedFolderTreeNode | undefined => {
    for (const n of nodes) {
      if (n.id === id) return n;
      const hit = find(n.children as EnhancedFolderTreeNode[]);
      if (hit) return hit;
    }
    return undefined;
  };
  return (find([...(tree?.shared ?? []), ...(tree?.private ?? [])])?.children ?? []).map((c) => c.id);
}

function toastTexts() {
  return useToastStore.getState().toasts.map((t) => [t.title, t.description].filter(Boolean).join(' — '));
}

const ENGINEERING = collection('kb-eng', 'Engineering');
const SALES = collection('kb-sales', 'Sales');

const DESIGNS = hubNode({ id: 'folder-designs', name: 'Designs', nodeType: 'folder', parentId: 'kb-eng', hasChildren: true });
const SPEC = hubNode({
  id: 'rec-spec',
  name: 'spec.pdf',
  nodeType: 'record',
  parentId: 'kb-eng',
  mimeType: 'application/pdf',
  extension: 'pdf',
  sizeInBytes: 2048,
  indexingStatus: 'COMPLETED',
});
const NOTES = hubNode({
  id: 'rec-notes',
  name: 'notes.txt',
  nodeType: 'record',
  parentId: 'kb-eng',
  mimeType: 'text/plain',
  extension: 'txt',
  indexingStatus: 'FAILED',
});

const ENGINEERING_TRAIL = [{ id: 'kb-eng', name: 'Engineering', nodeType: 'app' }];

function engineeringContents(items = [DESIGNS, SPEC, NOTES]) {
  return folderResponse({ id: 'kb-eng', name: 'Engineering', nodeType: 'app' }, ENGINEERING_TRAIL, items);
}

function withCollections(list = [ENGINEERING, SALES]) {
  api.hub.getNavigationNodes.mockResolvedValue(hubResponse(list));
}

function openAt(url: string) {
  nav.current!.reset(url);
  return renderInTheme(<KnowledgeBasePage />);
}

function currentUrl() {
  return nav.current!.getParams().toString();
}

async function openEngineering(items = [DESIGNS, SPEC, NOTES]) {
  withCollections();
  api.hub.loadFolderData.mockResolvedValue(engineeringContents(items));
  openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
  if (items.length > 0) await screen.findByRole('row', { name: items[0].name });
  else await screen.findByText('Engineering is empty');
}

function typeInto(element: HTMLElement, value: string) {
  fireEvent.change(element, { target: { value } });
}

async function chooseFromNewMenu(label: string) {
  const trigger = screen.getByTestId('new-dropdown-trigger');
  await act(async () => {
    fireEvent.keyDown(trigger, { key: 'Enter' });
  });
  fireEvent.click(await screen.findByRole('menuitem', { name: new RegExp(`${label}$`) }));
}

async function chooseRowAction(rowName: string, action: string) {
  const menu = await openRowMenu(rowName);
  fireEvent.click(within(menu).getByRole('menuitem', { name: new RegExp(`${action}$`) }));
}

describe('Knowledge base page — collections list', () => {
  it('lists every collection the user has when none is open', async () => {
    withCollections();
    openAt('/knowledge-base');

    expect(await screen.findByRole('row', { name: 'Engineering' })).toBeTruthy();
    expect(row('Sales')).toBeTruthy();
  });

  it('opens a collection when its row is clicked', async () => {
    withCollections();
    api.hub.loadFolderData.mockResolvedValue(engineeringContents());
    openAt('/knowledge-base');

    fireEvent.click(await screen.findByRole('row', { name: 'Engineering' }));

    expect(await screen.findByRole('row', { name: 'spec.pdf' })).toBeTruthy();
    expect(currentUrl()).toContain('nodeId=kb-eng');
    expect(api.hub.loadFolderData).toHaveBeenCalledWith('app', 'kb-eng', expect.any(Object), expect.any(Object));
  });

  it('invites a user with no collections to create one, then opens the new collection', async () => {
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([]));
    api.kb.createKnowledgeBase.mockResolvedValue({ id: 'kb-new', name: 'Handbook' });
    openAt('/knowledge-base');

    expect(await screen.findByText('No collections available')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Create Collection/ }));

    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByPlaceholderText('eg: Engineering'), '  Handbook  ');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(api.kb.createKnowledgeBase).toHaveBeenCalledWith('Handbook', ''));
    await waitFor(() => expect(currentUrl()).toContain('nodeId=kb-new'));
  });

  it('hides the create button from someone who may not create collections', async () => {
    permissions.denied.add('createCollection');
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([]));
    openAt('/knowledge-base');

    expect(await screen.findByText('No collections available')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Create Collection/ })).toBeNull();
  });

  it('tells the user when the collections could not be loaded', async () => {
    api.hub.getNavigationNodes.mockRejectedValue(new Error('offline'));
    openAt('/knowledge-base');

    await waitFor(() =>
      expect(toastTexts()).toContain('Failed to load Collections — Could not load collections. Please refresh the page.'),
    );
  });
});

describe('Knowledge base page — inside a collection', () => {
  it('shows the collection name in the breadcrumb and every item in it', async () => {
    await openEngineering();

    expect(screen.getAllByText('Engineering').length).toBeGreaterThan(0);
    expect(row('Designs')).toBeTruthy();
    expect(row('spec.pdf')).toBeTruthy();
    expect(row('notes.txt')).toBeTruthy();
  });

  it('opens a folder when clicked and goes back through the breadcrumb', async () => {
    await openEngineering();
    api.hub.loadFolderData.mockResolvedValue(
      folderResponse(
        { id: 'folder-designs', name: 'Designs', nodeType: 'folder' },
        [...ENGINEERING_TRAIL, { id: 'folder-designs', name: 'Designs', nodeType: 'folder' }],
        [hubNode({ id: 'rec-logo', name: 'logo.png', parentId: 'folder-designs' })],
      ),
    );

    fireEvent.click(row('Designs'));
    expect(await screen.findByRole('row', { name: 'logo.png' })).toBeTruthy();
    expect(currentUrl()).toContain('nodeId=folder-designs');

    api.hub.loadFolderData.mockResolvedValue(engineeringContents());
    fireEvent.click(screen.getByText('Engineering'));
    expect(await screen.findByRole('row', { name: 'spec.pdf' })).toBeTruthy();
    expect(currentUrl()).toContain('nodeId=kb-eng');
  });

  it('offers to add files to an empty collection', async () => {
    await openEngineering([]);

    expect(screen.getByText('Add your files or folders')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Create Folder/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Upload/ })).toBeTruthy();
  });

  it('creates a folder in the open collection from the New menu', async () => {
    await openEngineering();
    api.kb.createFolder.mockResolvedValue({ id: 'folder-new' });

    await chooseFromNewMenu('New Folder');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByPlaceholderText('eg: Engineering'), 'Roadmaps');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(api.kb.createFolder).toHaveBeenCalledWith('kb-eng', 'Roadmaps', '', null));
    await waitFor(() => expect(toastTexts()).toContain('Folder created successfully — "Roadmaps" has been created'));
  });

  it('renames a file in place and keeps its extension', async () => {
    await openEngineering();
    api.kb.renameRecord.mockResolvedValue({});

    fireEvent.click(within(row('spec.pdf')).getByText('spec.pdf'));
    const input = within(row('spec.pdf')).getByRole('textbox');
    typeInto(input, 'requirements');
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.kb.renameRecord).toHaveBeenCalledWith('rec-spec', 'requirements.pdf'));
    await waitFor(() => expect(toastTexts()).toContain('Renamed successfully — Renamed to "requirements.pdf"'));
  });

  it('renames the open collection from its breadcrumb', async () => {
    await openEngineering();
    api.kb.renameKnowledgeBase.mockResolvedValue({});

    fireEvent.click(screen.getByText('Engineering'));
    const input = await screen.findByDisplayValue('Engineering');
    typeInto(input, 'Platform');
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.kb.renameKnowledgeBase).toHaveBeenCalledWith('kb-eng', 'Platform'));
  });

  it('deletes a file only after the user types the confirmation word', async () => {
    await openEngineering();
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.loadFolderData.mockResolvedValue(engineeringContents([DESIGNS, NOTES]));
      return {};
    });

    await chooseRowAction('spec.pdf', 'Delete');
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Delete' });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);

    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(api.kb.deleteNode).toHaveBeenCalledWith({ nodeId: 'rec-spec', nodeType: 'record', rootKbId: 'kb-eng' }),
    );
    await waitFor(() => expect(queryRow('spec.pdf')).toBeNull());
  });

  it('searches inside the collection and clears the search on close', async () => {
    await openEngineering();
    api.hub.loadFolderData.mockClear();

    fireEvent.click(screen.getByRole('button', { name: /Find/ }));
    typeInto(screen.getByPlaceholderText('eg: Sales Docs'), 'spec');

    await waitFor(() =>
      expect(api.hub.loadFolderData).toHaveBeenCalledWith(
        'app',
        'kb-eng',
        expect.objectContaining({ q: 'spec' }),
        expect.any(Object),
      ),
    );
    expect(await screen.findByText('Results')).toBeTruthy();
  });

  it('reloads the collection when Refresh is clicked', async () => {
    await openEngineering();
    api.hub.loadFolderData.mockClear();

    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));

    await waitFor(() => expect(api.hub.loadFolderData).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('row', { name: 'spec.pdf' })).toBeTruthy();
  });

  it('sends a user whose access was removed back to the collections list', async () => {
    withCollections();
    api.hub.loadFolderData.mockRejectedValue({ type: 'AUTHORIZATION_ERROR', message: 'No access', statusCode: 403 });
    openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');

    await waitFor(() => expect(toastTexts()).toContain('You no longer have access to this collection'));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/knowledge-base'));
  });

  it('returns to the collections list when the open collection no longer exists', async () => {
    withCollections();
    api.hub.loadFolderData.mockRejectedValue({ type: 'NOT_FOUND', message: 'Gone', statusCode: 404 });
    openAt('/knowledge-base?nodeType=app&nodeId=kb-gone');

    await waitFor(() => expect(currentUrl()).not.toContain('kb-gone'));
    expect(await screen.findByRole('row', { name: 'Engineering' })).toBeTruthy();
  });
});

describe('Knowledge base page — failures the user must be able to recover from', () => {
  it('shows a readable message with a Retry when the collections list fails, and Retry loads it', async () => {
    let tableCallFails = true;
    api.hub.getNavigationNodes.mockImplementation(async (params: { nodeTypes?: string }) => {
      if (params?.nodeTypes === 'app' && tableCallFails) {
        throw { type: 'SERVER_ERROR', message: "KeyError: 'origin'", statusCode: 500 };
      }
      return hubResponse([ENGINEERING, SALES]);
    });
    openAt('/knowledge-base');

    const retry = await screen.findByRole('button', { name: /Retry/ });
    expect(screen.queryByText(/KeyError/)).toBeNull();
    expect(screen.getByText("We couldn't load your collections. Check your connection, then select Retry.")).toBeTruthy();

    tableCallFails = false;
    fireEvent.click(retry);
    expect(await screen.findByRole('row', { name: 'Engineering' })).toBeTruthy();
  });

  it('keeps the new-folder dialog open and explains a failure in plain words', async () => {
    await openEngineering();
    api.kb.createFolder.mockRejectedValue({ type: 'SERVER_ERROR', message: "KeyError: 'parentId'", statusCode: 500 });

    await chooseFromNewMenu('New Folder');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByPlaceholderText('eg: Engineering'), 'Roadmaps');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(toastTexts()).toContain(
        "Failed to create folder — We couldn't create it. Check the name and try again.",
      ),
    );
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it("passes on the server's own explanation when a rename is refused", async () => {
    await openEngineering();
    api.kb.renameRecord.mockRejectedValue({
      type: 'CONFLICT',
      message: 'A file with this name already exists in this folder.',
      statusCode: 409,
    });

    fireEvent.click(within(row('spec.pdf')).getByText('spec.pdf'));
    const input = within(row('spec.pdf')).getByRole('textbox');
    typeInto(input, 'notes');
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(toastTexts()).toContain('Failed to rename — A file with this name already exists in this folder.'),
    );
  });

  it('explains a failed rename in plain words when the server gives no readable reason', async () => {
    await openEngineering();
    api.kb.renameRecord.mockRejectedValue(new Error('Request failed with status code 500'));

    fireEvent.click(within(row('spec.pdf')).getByText('spec.pdf'));
    const input = within(row('spec.pdf')).getByRole('textbox');
    typeInto(input, 'requirements');
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(toastTexts()).toContain("Failed to rename — We couldn't rename it. Please try again in a moment."),
    );
  });

  it('explains a failed download in plain words', async () => {
    await openEngineering();
    api.kb.streamDownloadRecord.mockRejectedValue(new Error('read ECONNRESET'));

    await chooseRowAction('spec.pdf', 'Download');

    await waitFor(() =>
      expect(toastTexts()).toContain(
        "Failed to download — We couldn't download the file. Check your connection and try again.",
      ),
    );
  });

  it('explains a failed move in plain words', async () => {
    await openEngineering();
    api.kb.moveItem.mockRejectedValue(new Error('Error publishing to Kafka topic records'));

    await chooseRowAction('spec.pdf', 'Move');
    fireEvent.click(await screen.findByRole('button', { name: 'Move into Archive' }));

    await waitFor(() =>
      expect(toastTexts()).toContain("Failed to move item — We couldn't move it. Please try again in a moment."),
    );
  });

  it('explains a failed replace in plain words', async () => {
    await openEngineering();
    api.kb.replaceRecord.mockRejectedValue(new Error('Traceback (most recent call last): ...'));

    await chooseRowAction('spec.pdf', 'Replace');
    fireEvent.click(await screen.findByRole('button', { name: 'Replace with spec-v2.pdf' }));

    await waitFor(() =>
      expect(toastTexts()).toContain(
        "Failed to replace file — We couldn't replace the file. Please try again in a moment.",
      ),
    );
  });

  it('after the last collection is deleted, says there are no collections left', async () => {
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING]));
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockResolvedValue(hubResponse([]));
      return {};
    });
    openAt('/knowledge-base');

    await screen.findByRole('row', { name: 'Engineering' });
    await chooseRowAction('Engineering', 'Delete');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.kb.deleteNode).toHaveBeenCalledWith(expect.objectContaining({ nodeId: 'kb-eng', nodeType: 'app' })));
    expect(await screen.findByText('No collections available')).toBeTruthy();
    expect(useKnowledgeBaseStore.getState().categorizedNodes?.private ?? []).toEqual([]);
  });

  it('after the last collection is deleted, says so even when connectors fill the first page of the list', async () => {
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING]));
    const connectors = Array.from({ length: 20 }, (_, i) =>
      hubNode({ id: `app-${i}`, name: `Connector ${i}`, nodeType: 'app', origin: 'CONNECTOR', connector: 'DRIVE' }),
    );
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockImplementation(async ({ page }: { page: number }) =>
        hubResponse(page === 1 ? connectors : [], {
          pagination: { page, limit: 20, totalItems: 20, totalPages: 1, hasNext: page === 1, hasPrev: page > 1 },
        }),
      );
      return {};
    });
    openAt('/knowledge-base');

    await screen.findByRole('row', { name: 'Engineering' });
    await chooseRowAction('Engineering', 'Delete');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText('No collections available')).toBeTruthy();
    expect(useKnowledgeBaseStore.getState().categorizedNodes?.private ?? []).toEqual([]);
  });

  it('takes the only collection out of the sidebar as soon as it is deleted', async () => {
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING]));
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockReturnValue(new Promise(() => {}));
      return {};
    });
    openAt('/knowledge-base');

    await screen.findByRole('row', { name: 'Engineering' });
    await chooseRowAction('Engineering', 'Delete');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(sidebarIds()).toEqual([]));
    expect(useKnowledgeBaseStore.getState().nodes).toEqual([]);
  });

  it('keeps a deleted collection out of the sidebar when reloading the list fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING]));
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockImplementation(async ({ page }: { page: number }) => {
        if (page === 2) throw new Error('offline');
        return hubResponse([], {
          pagination: { page, limit: 20, totalItems: 40, totalPages: 2, hasNext: true, hasPrev: false },
        });
      });
      return {};
    });
    openAt('/knowledge-base');

    await screen.findByRole('row', { name: 'Engineering' });
    await chooseRowAction('Engineering', 'Delete');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.hub.getNavigationNodes).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })));
    await waitFor(() => expect(sidebarIds()).toEqual([]));
    const state = useKnowledgeBaseStore.getState();
    expect(state.nodes.map((n) => n.id)).toEqual([]);
    expect(state.appNodes.map((n) => n.id)).toEqual([]);
  });

  it('says the collection was deleted, not that deleting failed, when only the list reload fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    withCollections([ENGINEERING]);
    openAt('/knowledge-base');
    await screen.findByRole('row', { name: 'Engineering' });
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockRejectedValue(new Error('offline'));
      return {};
    });

    act(() =>
      useKnowledgeBaseStore.setState({
        pendingSidebarAction: { type: 'delete', nodeId: 'kb-eng', nodeName: 'Engineering', nodeType: 'app' },
      }),
    );
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete/ }));

    await waitFor(() =>
      expect(toastTexts()).toContain(
        "Couldn't update the list — The collection was deleted, but the list didn't refresh. Refresh the page to see the latest list.",
      ),
    );
    expect(toastTexts()).toContain('"Engineering" deleted successfully');
    expect(toastTexts().some((t) => t.startsWith('Failed to delete'))).toBe(false);
  });

  it.each([
    {
      outcome: 'the list reload fails after the delete',
      setup: () => api.kb.deleteNode.mockImplementation(async () => {
        api.hub.getNavigationNodes.mockRejectedValue(new Error('offline'));
        return {};
      }),
      message: "Couldn't update the list — The file was deleted, but the list didn't refresh. Refresh the page to see the latest list.",
    },
    {
      outcome: 'the delete itself fails',
      setup: () => api.kb.deleteNode.mockRejectedValue(new Error('')),
      message: 'Failed to delete file',
    },
  ])('calls a deleted record a file when $outcome', async ({ setup, message }) => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await openEngineering();
    setup();

    act(() =>
      useKnowledgeBaseStore.setState({
        pendingSidebarAction: { type: 'delete', nodeId: 'rec-spec', nodeName: 'spec.pdf', nodeType: 'record', rootKbId: 'kb-eng' },
      }),
    );
    const dialog = await screen.findByRole('dialog');
    const confirmBox = within(dialog).queryByRole('textbox');
    if (confirmBox) typeInto(confirmBox, 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete/ }));

    await waitFor(() => expect(toastTexts()).toContain(message));
    expect(toastTexts().some((t) => t.includes('collection'))).toBe(false);
  });

  it('keeps an open folder open when "load more" brings in another collection', async () => {
    api.hub.getNavigationNodes.mockImplementation(async ({ page }: { page?: number }) =>
      (page ?? 1) === 1
        ? hubResponse([ENGINEERING], {
            pagination: { page: 1, limit: 20, totalItems: 21, totalPages: 2, hasNext: true, hasPrev: false },
          })
        : hubResponse([SALES], {
            pagination: { page: 2, limit: 20, totalItems: 21, totalPages: 2, hasNext: false, hasPrev: true },
          }),
    );
    openAt('/knowledge-base');
    await screen.findByRole('row', { name: 'Engineering' });
    await waitFor(() => expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: true, nextPage: 2 }));
    act(() => {
      const kb = useKnowledgeBaseStore.getState();
      kb.cacheNodeChildren('kb-eng', [DESIGNS]);
      kb.toggleFolderExpanded('kb-eng');
      kb.reMergeCachedChildrenIntoTree();
    });
    expect(engineeringChildIds()).toEqual(['folder-designs']);

    await act(async () => {
      await loadMoreRootAppList();
    });

    expect(sidebarIds().sort()).toEqual(['kb-eng', 'kb-sales']);
    expect(engineeringChildIds()).toEqual(['folder-designs']);
  });

  it.each([
    { sharing: 'private', engineering: ENGINEERING },
    { sharing: 'shared', engineering: collection('kb-eng', 'Engineering', { sharingStatus: 'shared' }) },
  ])('keeps a folder open inside a folder when "load more" runs ($sharing collection)', async ({ engineering }) => {
    const MOCKUPS = hubNode({ id: 'folder-mockups', name: 'Mockups', nodeType: 'folder', parentId: 'folder-designs' });
    api.hub.getNavigationNodes.mockImplementation(async ({ page }: { page?: number }) =>
      (page ?? 1) === 1
        ? hubResponse([engineering], {
            pagination: { page: 1, limit: 20, totalItems: 21, totalPages: 2, hasNext: true, hasPrev: false },
          })
        : hubResponse([SALES], {
            pagination: { page: 2, limit: 20, totalItems: 21, totalPages: 2, hasNext: false, hasPrev: true },
          }),
    );
    openAt('/knowledge-base');
    await screen.findByRole('row', { name: 'Engineering' });
    await waitFor(() => expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: true, nextPage: 2 }));
    act(() => {
      const kb = useKnowledgeBaseStore.getState();
      kb.cacheNodeChildren('kb-eng', [DESIGNS]);
      kb.toggleFolderExpanded('kb-eng');
      kb.addNodes([DESIGNS]);
      kb.cacheNodeChildren('folder-designs', [MOCKUPS]);
      kb.toggleFolderExpanded('folder-designs');
      kb.addNodes([MOCKUPS]);
      const tree = useKnowledgeBaseStore.getState().categorizedNodes!;
      const section = engineering.sharingStatus === 'shared' ? 'shared' : 'private';
      const withDesigns = mergeChildrenIntoTree(tree[section], 'kb-eng', [DESIGNS]);
      kb.setCategorizedNodes({ ...tree, [section]: mergeChildrenIntoTree(withDesigns, 'folder-designs', [MOCKUPS]) });
    });
    expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']);

    await act(async () => {
      await loadMoreRootAppList();
    });

    expect(sidebarIds().sort()).toEqual(['kb-eng', 'kb-sales']);
    expect(engineeringChildIds()).toEqual(['folder-designs']);
    expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']);
    expect(useKnowledgeBaseStore.getState().nodes.map((n) => n.id)).toContain('folder-designs');
  });

  it('keeps a collection created while the first load of the list was still in flight', async () => {
    const heldFirstLoads: Array<(value: unknown) => void> = [];
    let created = false;
    const NEW_KB = collection('kb-new', 'Handbook');
    api.hub.getNavigationNodes.mockImplementation(() => {
      if (created) return Promise.resolve(hubResponse([NEW_KB, ENGINEERING]));
      return new Promise((resolve) => heldFirstLoads.push(resolve));
    });
    api.kb.createKnowledgeBase.mockImplementation(async () => {
      created = true;
      return { id: 'kb-new', name: 'Handbook' };
    });
    openAt('/knowledge-base');
    await waitFor(() => expect(heldFirstLoads.length).toBeGreaterThan(0));

    act(() => useKnowledgeBaseStore.setState({ pendingSidebarAction: { type: 'create-collection' } }));
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByPlaceholderText('eg: Engineering'), 'Handbook');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(sidebarIds().sort()).toEqual(['kb-eng', 'kb-new']));

    await act(async () => {
      for (const resolve of heldFirstLoads) resolve(hubResponse([ENGINEERING]));
    });

    expect(sidebarIds().sort()).toEqual(['kb-eng', 'kb-new']);
    expect(useKnowledgeBaseStore.getState().appNodes.map((n) => n.id).sort()).toEqual(['kb-eng', 'kb-new']);
  });

  it('takes a deleted collection out of the sidebar without waiting for the list to reload', async () => {
    api.hub.getNavigationNodes.mockResolvedValue(hubResponse([ENGINEERING, SALES]));
    api.kb.deleteNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockReturnValue(new Promise(() => {}));
      return {};
    });
    openAt('/knowledge-base');

    await screen.findByRole('row', { name: 'Engineering' });
    await chooseRowAction('Engineering', 'Delete');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      const tree = useKnowledgeBaseStore.getState().categorizedNodes;
      expect([...(tree?.shared ?? []), ...(tree?.private ?? [])].map((n) => n.id)).toEqual(['kb-sales']);
    });
  });
});

describe('Knowledge base page — working with files', () => {
  it('opens a preview of a file when it is clicked', async () => {
    await openEngineering();
    api.kb.getRecordDetails.mockResolvedValue({ record: { recordName: 'spec.pdf', mimeType: 'application/pdf', sizeInBytes: 2048 } });
    api.kb.streamRecord.mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));

    fireEvent.click(row('spec.pdf'));

    const preview = await screen.findByRole('region', { name: 'File preview' });
    expect(within(preview).getByText('spec.pdf')).toBeTruthy();
    expect(await within(preview).findByText('Showing blob:preview')).toBeTruthy();
    expect(api.kb.streamRecord).toHaveBeenCalledWith('rec-spec', undefined);

    fireEvent.click(within(preview).getByRole('button', { name: 'Close preview' }));
    expect(screen.queryByRole('region', { name: 'File preview' })).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });

  it('does not download a file the viewer cannot render', async () => {
    await openEngineering();
    api.kb.getRecordDetails.mockResolvedValue({ record: { recordName: 'spec.pdf', previewRenderable: false } });

    fireEvent.click(row('spec.pdf'));

    const preview = await screen.findByRole('region', { name: 'File preview' });
    await waitFor(() => expect(within(preview).queryByText('Loading preview')).toBeNull());
    expect(api.kb.streamRecord).not.toHaveBeenCalled();
  });

  it('explains in plain words when a preview cannot be opened', async () => {
    await openEngineering();
    api.kb.getRecordDetails.mockRejectedValue(new Error('Request failed with status code 500'));

    fireEvent.click(row('spec.pdf'));

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      "We couldn't open a preview of this file. Try downloading it instead, or try again in a moment.",
    );
  });

  it('downloads a file from its menu', async () => {
    await openEngineering();
    api.kb.streamDownloadRecord.mockResolvedValue(undefined);

    await chooseRowAction('spec.pdf', 'Download');

    await waitFor(() => expect(api.kb.streamDownloadRecord).toHaveBeenCalledWith('rec-spec', 'spec.pdf'));
  });

  it('retries indexing a file that failed to index', async () => {
    await openEngineering();
    api.kb.reindexItem.mockResolvedValue({});

    await chooseRowAction('notes.txt', 'Retry indexing');

    await waitFor(() => expect(api.kb.reindexItem).toHaveBeenCalledWith('rec-notes', 0, undefined));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.variant === 'success')).toBe(true));
  });

  it('asks before force-reindexing a file that already indexed', async () => {
    await openEngineering();
    api.kb.reindexItem.mockResolvedValue({});

    await chooseRowAction('spec.pdf', 'Force reindex');
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Start force reindex?')).toBeTruthy();
    expect(api.kb.reindexItem).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(api.kb.reindexItem).toHaveBeenCalledWith('rec-spec', 0, undefined));
  });

  it('offers to try again when reindexing could not start', async () => {
    await openEngineering();
    api.kb.reindexItem.mockRejectedValueOnce(new Error('Error publishing to Kafka topic records')).mockResolvedValue({});

    await chooseRowAction('notes.txt', 'Retry indexing');

    await waitFor(() =>
      expect(toastTexts()).toContain("We couldn't start reindexing. Please try again in a moment."),
    );
    const failed = useToastStore.getState().toasts.find((t) => t.variant === 'error');
    act(() => failed?.action?.onClick());
    await waitFor(() => expect(api.kb.reindexItem).toHaveBeenCalledTimes(2));
  });

  it('reindexes everything inside a folder', async () => {
    await openEngineering();
    api.kb.reindexItem.mockResolvedValue({});

    await chooseRowAction('Designs', 'Index all');

    await waitFor(() => expect(api.kb.reindexItem).toHaveBeenCalledWith('folder-designs', 100, undefined));
  });

  it('moves a file to another folder', async () => {
    await openEngineering();
    api.kb.moveItem.mockResolvedValue({});

    await chooseRowAction('spec.pdf', 'Move');
    fireEvent.click(await screen.findByRole('button', { name: 'Move into Archive' }));

    await waitFor(() => expect(api.kb.moveItem).toHaveBeenCalledWith('kb-eng', 'rec-spec', 'folder-archive'));
    await waitFor(() => expect(toastTexts()).toContain('Item moved successfully — "spec.pdf" has been moved'));
  });

  it('replaces a file with a new version', async () => {
    await openEngineering();
    api.kb.replaceRecord.mockResolvedValue({});

    await chooseRowAction('spec.pdf', 'Replace');
    fireEvent.click(await screen.findByRole('button', { name: 'Replace with spec-v2.pdf' }));

    await waitFor(() =>
      expect(api.kb.replaceRecord).toHaveBeenCalledWith('rec-spec', expect.any(File), 'spec.pdf', expect.any(Function)),
    );
    await waitFor(() =>
      expect(toastTexts()).toContain('File replaced successfully — "spec.pdf" has been replaced with "spec-v2.pdf"'),
    );
  });

  it('switches between list and grid layouts', async () => {
    await openEngineering();

    fireEvent.click(screen.getAllByRole('radio')[0]);

    expect(await screen.findByRole('gridcell', { name: 'spec.pdf' })).toBeTruthy();
    expect(queryRow('spec.pdf')).toBeNull();
  });

  it('loads the next page of a large collection', async () => {
    withCollections();
    api.hub.loadFolderData.mockImplementation(async (_type: string, _id: string, params: { page: number }) => {
      const page = engineeringContents(params.page === 1 ? [DESIGNS, SPEC] : [NOTES]);
      page.pagination = {
        page: params.page,
        limit: 50,
        totalItems: 120,
        totalPages: 3,
        hasNext: params.page < 3,
        hasPrev: params.page > 1,
      };
      return page;
    });
    openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await screen.findByRole('row', { name: 'spec.pdf' });
    expect(screen.getByText('Showing 1-50 of 120 Items')).toBeTruthy();

    fireEvent.click(screen.getByText('Next'));

    expect(await screen.findByRole('row', { name: 'notes.txt' })).toBeTruthy();
    expect(screen.getByText('Showing 51-100 of 120 Items')).toBeTruthy();
    expect(currentUrl()).toContain('page=2');
  });
});

describe('Knowledge base page — selecting several items', () => {
  async function selectSpecAndNotes() {
    await openEngineering();
    fireEvent.click(within(row('spec.pdf')).getByRole('checkbox'));
    fireEvent.click(within(row('notes.txt')).getByRole('checkbox'));
    expect(await screen.findByText('2 Items Selected')).toBeTruthy();
  }

  it('deletes the selected files after confirmation', async () => {
    await selectSpecAndNotes();
    api.kb.bulkDelete.mockResolvedValue([
      { status: 'fulfilled', value: {} },
      { status: 'fulfilled', value: {} },
    ]);

    const bar = screen.getByText('2 Items Selected').parentElement!;
    fireEvent.click(within(bar).getByRole('button', { name: /Delete/ }));
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete/ }));

    await waitFor(() =>
      expect(api.kb.bulkDelete).toHaveBeenCalledWith([
        { id: 'rec-spec', name: 'spec.pdf', nodeType: 'record', kbId: 'kb-eng' },
        { id: 'rec-notes', name: 'notes.txt', nodeType: 'record', kbId: 'kb-eng' },
      ]),
    );
    await waitFor(() => expect(toastTexts()).toContain('Successfully deleted 2 items'));
  });

  it('reindexes the selected files together', async () => {
    await selectSpecAndNotes();
    api.kb.bulkReindex.mockResolvedValue([
      { status: 'fulfilled', value: {} },
      { status: 'rejected', reason: new Error('x') },
    ]);

    const bar = screen.getByText('2 Items Selected').parentElement!;
    fireEvent.click(within(bar).getByRole('button', { name: /Re-index/ }));

    await waitFor(() => expect(toastTexts()).toContain('Reindexed 1 items, 1 failed'));
  });

  it('clears the selection', async () => {
    await selectSpecAndNotes();

    const bar = screen.getByText('2 Items Selected').parentElement!;
    fireEvent.click(within(bar).getByRole('checkbox'));

    await waitFor(() => expect(screen.queryByText('2 Items Selected')).toBeNull());
  });
});

describe('Knowledge base page — uploading', () => {
  function fileOfSize(name: string, bytes: number) {
    const file = new File(['x'], name, { type: 'text/plain' });
    Object.defineProperty(file, 'size', { value: bytes });
    return file;
  }

  it('uploads files into the open collection and rejects one over the size limit without sending it', async () => {
    await openEngineering();
    const small = fileOfSize('readme.txt', 10);
    const huge = fileOfSize('dump.bin', 50 * 1024 * 1024);
    upload.items = [
      { id: 'u1', name: small.name, size: small.size, type: 'file', file: small },
      { id: 'u2', name: huge.name, size: huge.size, type: 'file', file: huge },
    ];
    api.kb.streamUpload.mockImplementation(
      async (_kb: string, _folder: string | null, _files: File[], _meta: unknown, opts: { onEvent: (e: { event: string; data?: unknown }) => void }) => {
        opts.onEvent({ event: 'file:succeeded', data: { filePath: 'readme.txt' } });
        opts.onEvent({ event: 'done' });
      },
    );
    api.hub.loadFolderData.mockClear();

    await chooseFromNewMenu('Upload Data');
    fireEvent.click(await screen.findByRole('button', { name: 'Upload chosen files' }));

    await waitFor(() => expect(api.kb.streamUpload).toHaveBeenCalledTimes(1));
    const [kbId, folderId, files] = api.kb.streamUpload.mock.calls[0];
    expect([kbId, folderId, (files as File[]).map((f) => f.name)]).toEqual(['kb-eng', null, ['readme.txt']]);

    await waitFor(() => {
      const byName = Object.fromEntries(useUploadStore.getState().items.map((i) => [i.name, i]));
      expect(byName['readme.txt'].status).toBe('completed');
      expect(byName['dump.bin'].status).toBe('failed');
      expect(byName['dump.bin'].errors?.[0]).toContain('larger than the 5 MB limit');
    });
    await waitFor(() => expect(api.hub.loadFolderData).toHaveBeenCalled());
  });

  it('marks every file failed when the upload connection drops', async () => {
    await openEngineering();
    const a = fileOfSize('a.txt', 10);
    upload.items = [{ id: 'u1', name: a.name, size: a.size, type: 'file', file: a }];
    api.kb.streamUpload.mockImplementation(
      async (_kb: string, _folder: string | null, _files: File[], _meta: unknown, opts: { onError: (e: Error) => void }) => {
        opts.onError(new Error('The upload was interrupted. Check your connection and upload again.'));
      },
    );

    await chooseFromNewMenu('Upload Data');
    fireEvent.click(await screen.findByRole('button', { name: 'Upload chosen files' }));

    await waitFor(() => {
      const item = useUploadStore.getState().items.find((i) => i.name === 'a.txt');
      expect(item?.status).toBe('failed');
      expect(item?.errors).toContain('The upload was interrupted. Check your connection and upload again.');
    });
  });

  it('uploads a folder so its files keep their paths, and fails files whose folder could not be created', async () => {
    await openEngineering();
    const files = ['one.txt', 'two.txt'].map((n) => fileOfSize(n, 10));
    upload.items = [
      {
        id: 'u1',
        name: 'Reports',
        size: 20,
        type: 'folder',
        filesWithPaths: files.map((file) => ({ file, relativePath: `q1/${file.name}` })),
      },
    ];
    api.kb.streamUpload.mockRejectedValue(new Error('Folder could not be created. Try again.'));

    await chooseFromNewMenu('Upload Data');
    fireEvent.click(await screen.findByRole('button', { name: 'Upload chosen files' }));

    await waitFor(() => expect(api.kb.streamUpload).toHaveBeenCalled());
    const meta = api.kb.streamUpload.mock.calls[0][3] as { file_path: string }[];
    expect(meta.map((m) => m.file_path)).toEqual(['Reports/q1/one.txt', 'Reports/q1/two.txt']);
    await waitFor(() => {
      const failed = useUploadStore.getState().items.filter((i) => i.status === 'failed');
      expect(failed.map((i) => i.name).sort()).toEqual(['one.txt', 'two.txt']);
    });
  });
});

describe('Knowledge base page — folders and sharing', () => {
  it('creates a sub-folder inside the open folder', async () => {
    withCollections();
    api.hub.loadFolderData.mockResolvedValue(
      folderResponse(
        { id: 'folder-designs', name: 'Designs', nodeType: 'folder' },
        [...ENGINEERING_TRAIL, { id: 'folder-designs', name: 'Designs', nodeType: 'folder' }],
        [hubNode({ id: 'rec-logo', name: 'logo.png', parentId: 'folder-designs' })],
      ),
    );
    api.kb.createFolder.mockResolvedValue({});
    openAt('/knowledge-base?nodeType=folder&nodeId=folder-designs');
    await screen.findByRole('row', { name: 'logo.png' });

    await chooseFromNewMenu('New Folder');
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByPlaceholderText('eg: Engineering'), 'Icons');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(api.kb.createFolder).toHaveBeenCalledWith('kb-eng', 'Icons', '', 'folder-designs'));
  });

  it('lets the owner of a collection open its sharing panel', async () => {
    await openEngineering();
    useKnowledgeBaseStore.getState().setNodes([ENGINEERING]);

    fireEvent.click(await screen.findByRole('button', { name: 'Share' }));

    expect(await screen.findByRole('complementary', { name: 'Share collection' })).toBeTruthy();
  });

  it('shows sharing as locked for an owner without sharing permission', async () => {
    permissions.denied.add('shareCollection');
    await openEngineering();
    useKnowledgeBaseStore.getState().setNodes([ENGINEERING]);

    fireEvent.click(await screen.findByRole('button', { name: 'Share' }));

    expect(screen.getAllByText('Locked').length).toBeGreaterThan(0);
    expect(screen.queryByRole('complementary', { name: 'Share collection' })).toBeNull();
  });

  it('opens the create-collection dialog when asked from the sidebar', async () => {
    withCollections();
    openAt('/knowledge-base');
    await screen.findByRole('row', { name: 'Engineering' });

    act(() => useKnowledgeBaseStore.setState({ pendingSidebarAction: { type: 'create-collection' } }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getAllByText('Create Collection').length).toBeGreaterThan(0);
  });

  it('deletes a folder when asked from the sidebar', async () => {
    await openEngineering();
    api.kb.deleteNode.mockResolvedValue({});

    act(() =>
      useKnowledgeBaseStore.setState({
        pendingSidebarAction: { type: 'delete', nodeId: 'folder-designs', nodeName: 'Designs', nodeType: 'folder', rootKbId: 'kb-eng' },
      }),
    );
    const dialog = await screen.findByRole('dialog');
    typeInto(within(dialog).getByRole('textbox'), 'DELETE');
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete/ }));

    await waitFor(() =>
      expect(api.kb.deleteNode).toHaveBeenCalledWith({ nodeId: 'folder-designs', nodeType: 'folder', rootKbId: 'kb-eng' }),
    );
    await waitFor(() => expect(toastTexts()).toContain('"Designs" deleted successfully'));
  });
});

describe('Knowledge base page — All Records', () => {
  const CONNECTOR_FILE = hubNode({
    id: 'rec-drive',
    name: 'Budget.xlsx',
    origin: 'CONNECTOR',
    connector: 'GOOGLE_DRIVE',
    indexingStatus: 'COMPLETED',
  });

  it('lists records from every source', async () => {
    withCollections();
    api.hub.getAllRootItems.mockResolvedValue(hubResponse([CONNECTOR_FILE]));
    openAt('/knowledge-base?view=all-records');

    expect(await screen.findByRole('row', { name: 'Budget.xlsx' })).toBeTruthy();
    expect(screen.getByText('All Records')).toBeTruthy();
  });

  it('shows an error with a Retry that reloads the records', async () => {
    withCollections();
    api.hub.getAllRootItems.mockRejectedValueOnce(new Error('offline')).mockResolvedValue(hubResponse([CONNECTOR_FILE]));
    openAt('/knowledge-base?view=all-records');

    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }));

    expect(await screen.findByRole('row', { name: 'Budget.xlsx' })).toBeTruthy();
  });

  it('opens a connector folder from the list', async () => {
    withCollections();
    const driveFolder = hubNode({ id: 'rg-drive', name: 'Shared Drive', nodeType: 'recordGroup', origin: 'CONNECTOR', hasChildren: true });
    api.hub.getAllRootItems.mockResolvedValue(hubResponse([driveFolder]));
    api.hub.loadFolderData.mockResolvedValue(
      folderResponse({ id: 'rg-drive', name: 'Shared Drive', nodeType: 'folder' }, [{ id: 'rg-drive', name: 'Shared Drive', nodeType: 'recordGroup' }], [CONNECTOR_FILE]),
    );
    openAt('/knowledge-base?view=all-records');

    fireEvent.click(await screen.findByRole('row', { name: 'Shared Drive' }));

    expect(await screen.findByRole('row', { name: 'Budget.xlsx' })).toBeTruthy();
    expect(api.hub.loadFolderData).toHaveBeenCalledWith('recordGroup', 'rg-drive', expect.any(Object));
    expect(currentUrl()).toContain('view=all-records');
  });

  it('searches across all records', async () => {
    withCollections();
    api.hub.getAllRootItems.mockResolvedValue(hubResponse([CONNECTOR_FILE]));
    openAt('/knowledge-base?view=all-records');
    await screen.findByRole('row', { name: 'Budget.xlsx' });

    fireEvent.click(screen.getByRole('button', { name: /Find/ }));
    typeInto(screen.getByPlaceholderText('eg: Sales Docs'), 'budget');

    await waitFor(() =>
      expect(api.hub.getAllRootItems).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'budget' })),
    );

    const searchButtons = within(screen.getByPlaceholderText('eg: Sales Docs').parentElement!.parentElement!).getAllByRole('button');
    fireEvent.click(searchButtons[searchButtons.length - 1]);
    await waitFor(() => expect(api.hub.getAllRootItems).toHaveBeenLastCalledWith(expect.not.objectContaining({ q: 'budget' })));
  });
});


describe('Knowledge base sidebar — folders stay usable after the collection list changes', () => {
  const SPECS_DIR = hubNode({ id: 'folder-specs', name: 'Specs', nodeType: 'folder', parentId: 'kb-eng', hasChildren: true });
  const MOCKUPS = hubNode({ id: 'folder-mockups', name: 'Mockups', nodeType: 'folder', parentId: 'folder-designs' });
  const DRAFTS = hubNode({ id: 'folder-drafts', name: 'Drafts', nodeType: 'folder', parentId: 'folder-specs' });

  let sidebar: HTMLElement = document.body;

  function sidebarChevron(name: string): HTMLElement {
    for (const label of within(sidebar).getAllByText(name)) {
      let el: HTMLElement | null = label;
      for (let depth = 0; depth < 6 && el; depth += 1) {
        el = el.parentElement;
        const icon = el
          ? Array.from(el.querySelectorAll('span')).find((span) => ['chevron_right', 'expand_more'].includes(span.textContent ?? ''))
          : undefined;
        if (icon?.parentElement) return icon.parentElement;
      }
    }
    throw new Error(`no expand control next to "${name}" in the sidebar`);
  }

  async function renameInSidebar(from: string, to: string) {
    const item = within(sidebar).getByText(from).closest('[class*="rt-Flex"]')!.parentElement!;
    fireEvent.mouseEnter(item);
    const trigger = within(item).getAllByRole('button').at(-1)!;
    await act(async () => {
      trigger.focus();
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: /Rename$/ }));
    const input = within(sidebar).getByDisplayValue(from);
    fireEvent.change(input, { target: { value: to } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
  }

  function openEngineeringWithSidebar() {
    api.hub.loadFolderData.mockResolvedValue(engineeringContents([DESIGNS, SPECS_DIR]));
    nav.current!.reset('/knowledge-base?nodeType=app&nodeId=kb-eng');
    const view = renderInTheme(
      <>
        <div data-testid="sidebar-slot">
          <KnowledgeBaseSidebarSlot />
        </div>
        <KnowledgeBasePage />
      </>,
    );
    sidebar = within(view.container).getByTestId('sidebar-slot');
  }

  it('says a sidebar rename worked when only the list reload fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(async () => hubResponse([DESIGNS, SPECS_DIR]));
    api.kb.renameNode.mockImplementation(async () => {
      api.hub.getNavigationNodes.mockRejectedValue(new Error('offline'));
      return {};
    });
    openEngineeringWithSidebar();
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));

    await renameInSidebar('Specs', 'Specifications');

    await waitFor(() =>
      expect(toastTexts()).toContain(
        "Couldn't update the list — The folder was renamed, but the list didn't refresh. Refresh the page to see the latest list.",
      ),
    );
    expect(toastTexts()).toContain('Folder renamed successfully');
    expect(toastTexts()).not.toContain('Failed to rename');
  });

  function openWithSidebarAt(url: string) {
    nav.current!.reset(url);
    const view = renderInTheme(
      <>
        <div data-testid="sidebar-slot">
          <KnowledgeBaseSidebarSlot />
        </div>
        <KnowledgeBasePage />
      </>,
    );
    sidebar = within(view.container).getByTestId('sidebar-slot');
  }

  function numberedFolders(count: number, parentId: string, prefix = 'Folder') {
    return Array.from({ length: count }, (_, i) =>
      hubNode({
        id: `${parentId}-f${i}`,
        name: `${prefix} ${String(i).padStart(2, '0')}`,
        nodeType: 'folder',
        parentId,
        updatedAt: i,
      }),
    );
  }

  function childRequests(id: string) {
    return api.hub.getNodeChildren.mock.calls
      .filter(([, requested]) => requested === id)
      .map(([, , params]) => ({ page: params?.page, limit: params?.limit, sortBy: params?.sortBy }));
  }

  it('lists a collection opened from the page by name, 20 at a time, and loads the rest on request', async () => {
    const folders = numberedFolders(30, 'kb-eng');
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(hubChildrenFake(() => ({ 'kb-eng': folders })));
    api.hub.loadFolderData.mockResolvedValue(engineeringContents(folders.slice(0, 3)));
    openWithSidebarAt('/knowledge-base?nodeType=app&nodeId=kb-eng');

    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(folders.slice(0, 20).map((f) => f.id)));
    expect(childRequests('kb-eng')).toEqual([{ page: 1, limit: 20, sortBy: 'name' }]);
    expect(within(sidebar).getByRole('button', { name: /load more/i })).toBeTruthy();

    await act(async () => {
      await loadMoreNodeChildrenPage('kb-eng');
    });
    expect(childIdsOf('kb-eng')).toEqual(folders.map((f) => f.id));
  });

  it('keeps every folder shown and the renamed one in view after a sidebar rename', async () => {
    let renamed = false;
    const folders = numberedFolders(70, 'kb-eng');
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(
      hubChildrenFake(() => ({
        'kb-eng': folders.map((f) => (renamed && f.id === 'kb-eng-f5' ? { ...f, name: 'Zz archive' } : f)),
      })),
    );
    api.kb.renameNode.mockImplementation(async () => {
      renamed = true;
      return {};
    });
    api.hub.loadFolderData.mockResolvedValue(engineeringContents(folders.slice(0, 3)));
    openWithSidebarAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await waitFor(() => expect(childIdsOf('kb-eng')).toHaveLength(20));
    await act(async () => {
      await loadMoreNodeChildrenPage('kb-eng');
    });
    const shownBefore = childIdsOf('kb-eng');
    expect(shownBefore).toHaveLength(40);
    api.hub.getNodeChildren.mockClear();

    await renameInSidebar('Folder 05', 'Zz archive');

    await waitFor(() => expect(within(sidebar).getByText('Zz archive')).toBeTruthy());
    expect(childIdsOf('kb-eng')).toEqual(expect.arrayContaining(shownBefore));
    expect(childRequests('kb-eng').every((r) => r.limit === 20 && r.sortBy === 'name')).toBe(true);
    expect(useKnowledgeBaseStore.getState().nodeChildrenPagination.get('kb-eng')).toEqual(
      expect.objectContaining({ hasNext: false }),
    );
  });

  it('shows the next folder on the page\'s path even when it sorts past the first page', async () => {
    const designsChildren = numberedFolders(45, 'folder-designs', 'Draft');
    const deep = designsChildren[42];
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(
      hubChildrenFake(() => ({ 'kb-eng': [DESIGNS, SPECS_DIR], 'folder-designs': designsChildren })),
    );
    api.hub.loadFolderData.mockResolvedValue(
      folderResponse(
        { id: deep.id, name: deep.name, nodeType: 'folder' },
        [
          { id: 'kb-eng', name: 'Engineering', nodeType: 'app' },
          { id: 'folder-designs', name: 'Designs', nodeType: 'folder' },
          { id: deep.id, name: deep.name, nodeType: 'folder' },
        ],
        [],
      ),
    );
    openWithSidebarAt(`/knowledge-base?nodeType=folder&nodeId=${deep.id}`);

    await waitFor(() => expect(childIdsOf('folder-designs')).toContain(deep.id));
    expect(within(sidebar).getByText(deep.name)).toBeTruthy();
    const firstPages = childRequests('folder-designs').filter((r) => r.page === 1);
    expect(firstPages).toEqual([{ page: 1, limit: 20, sortBy: 'name' }]);
    expect(childRequests('folder-designs').every((r) => r.limit === 20 && r.sortBy === 'name')).toBe(true);
  });

  it('asks for a folder once when the page and the sidebar open it together', async () => {
    const drafts = numberedFolders(30, 'folder-designs', 'Draft');
    let release: () => void = () => {};
    const fake = hubChildrenFake(() => ({ 'kb-eng': [DESIGNS, SPECS_DIR], 'folder-designs': drafts }));
    let held = true;
    api.hub.getNodeChildren.mockImplementation(async (type: string, id: string, params: Record<string, unknown> = {}) => {
      if (id === 'folder-designs' && held) await new Promise<void>((resolve) => { release = resolve; });
      return fake(type, id, params);
    });
    withCollections([ENGINEERING]);
    api.hub.loadFolderData.mockResolvedValue(
      folderResponse(
        { id: 'folder-mockups', name: 'Mockups', nodeType: 'folder' },
        [
          { id: 'kb-eng', name: 'Engineering', nodeType: 'app' },
          { id: 'folder-designs', name: 'Designs', nodeType: 'folder' },
          { id: 'folder-mockups', name: 'Mockups', nodeType: 'folder' },
        ],
        [],
      ),
    );
    openWithSidebarAt('/knowledge-base?nodeType=folder&nodeId=folder-mockups');

    await waitFor(() => expect(childRequests('folder-designs')).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(childRequests('folder-designs')).toHaveLength(1);
    held = false;
    await act(async () => {
      release();
    });
    await waitFor(() => expect(childIdsOf('folder-designs')).toHaveLength(30));
    expect(childRequests('folder-designs')).toEqual([
      { page: 1, limit: 20, sortBy: 'name' },
      { page: 2, limit: 20, sortBy: 'name' },
    ]);
    expect(useKnowledgeBaseStore.getState().nodeChildrenPagination.get('folder-designs')).toEqual(
      expect.objectContaining({ hasNext: false }),
    );
  });

  it('keeps open subfolders in the sidebar when the move dialog opens their collection', async () => {
    const MOCKUPS = hubNode({ id: 'folder-mockups', name: 'Mockups', nodeType: 'folder', parentId: 'folder-designs' });
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(
      hubChildrenFake(() => ({ 'kb-eng': [DESIGNS, SPECS_DIR], 'folder-designs': [MOCKUPS] })),
    );
    api.hub.loadFolderData.mockResolvedValue(engineeringContents([DESIGNS, SPEC]));
    openWithSidebarAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await screen.findByRole('row', { name: 'spec.pdf' });
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    await act(async () => {
      fireEvent.click(sidebarChevron('Designs'));
    });
    await waitFor(() => expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']));

    await chooseRowAction('spec.pdf', 'Move');
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Open Engineering in the move dialog' }));
    });

    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']);
    expect(within(sidebar).getByText('Mockups')).toBeTruthy();
  });

  it('never shows a folder listing that arrives after sign-out, even once the next user has theirs', async () => {
    useAuthStore.setState({ isAuthenticated: true, accessToken: 'token', user: { id: 'user-a' } });
    let releaseOld: (value: unknown) => void = () => {};
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(hubChildrenFake(() => ({})));
    api.hub.loadFolderData.mockImplementationOnce(() => new Promise((resolve) => { releaseOld = resolve; }));
    openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await waitFor(() => expect(api.hub.loadFolderData).toHaveBeenCalledTimes(1));

    // The page goes away with sign-out; its request is still in flight.
    cleanup();
    act(() => useAuthStore.getState().logout());
    await act(async () => {
      releaseOld(engineeringContents([SPEC]));
    });
    expect(useKnowledgeBaseStore.getState().tableData).toBeNull();

    let releaseOldAgain: (value: unknown) => void = () => {};
    act(() => useAuthStore.setState({ isAuthenticated: true, accessToken: 'token', user: { id: 'user-a' } }));
    const callsBefore = api.hub.loadFolderData.mock.calls.length;
    api.hub.loadFolderData.mockImplementationOnce(() => new Promise((resolve) => { releaseOldAgain = resolve; }));
    openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await waitFor(() => expect(api.hub.loadFolderData.mock.calls.length).toBeGreaterThan(callsBefore));
    cleanup();
    act(() => useAuthStore.getState().logout());
    act(() => useAuthStore.setState({ isAuthenticated: true, accessToken: 'token', user: { id: 'user-b' } }));
    api.hub.loadFolderData.mockResolvedValue(engineeringContents([NOTES]));
    openAt('/knowledge-base?nodeType=app&nodeId=kb-eng');
    await screen.findByRole('row', { name: 'notes.txt' });

    await act(async () => {
      releaseOldAgain(engineeringContents([SPEC]));
    });

    expect(useKnowledgeBaseStore.getState().tableData?.items.map((i) => i.id)).toEqual(['rec-notes']);
    expect(screen.queryByRole('row', { name: 'spec.pdf' })).toBeNull();
    act(() => useAuthStore.getState().logout());
  });

  it('expands a collection from its chevron at the collections root', async () => {
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(hubChildrenFake(() => ({ 'kb-eng': [DESIGNS, SPECS_DIR] })));
    openWithSidebarAt('/knowledge-base');
    await screen.findByRole('row', { name: 'Engineering' });

    await act(async () => {
      fireEvent.click(sidebarChevron('Engineering'));
    });

    await waitFor(() => expect(within(sidebar).getByText('Designs')).toBeTruthy());
    expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']);
  });

  it('reloads a folder paged in the sidebar by name, page by page, and keeps its cursor', async () => {
    const drafts = Array.from({ length: 45 }, (_, i) =>
      hubNode({ id: `draft-${i}`, name: `Draft ${String(i).padStart(2, '0')}`, nodeType: 'folder', parentId: 'folder-designs', updatedAt: 45 - i }),
    );
    let renamed = false;
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(
      hubChildrenFake(() => ({
        'kb-eng': [DESIGNS, SPECS_DIR],
        'folder-designs': drafts.map((d) => (renamed && d.id === 'draft-3' ? { ...d, name: 'Draft 03 final' } : d)),
      })),
    );
    api.kb.renameNode.mockImplementation(async () => {
      renamed = true;
      return {};
    });
    openEngineeringWithSidebar();
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    await act(async () => {
      fireEvent.click(sidebarChevron('Designs'));
    });
    await waitFor(() => expect(childIdsOf('folder-designs')).toHaveLength(20));
    await act(async () => {
      await loadMoreNodeChildrenPage('folder-designs');
    });
    expect(childIdsOf('folder-designs')).toHaveLength(40);
    api.hub.getNodeChildren.mockClear();

    await renameInSidebar('Draft 03', 'Draft 03 final');

    await waitFor(() => expect(within(sidebar).getByText('Draft 03 final')).toBeTruthy());
    const reloads = api.hub.getNodeChildren.mock.calls.filter(([, id]) => id === 'folder-designs');
    expect(reloads.map(([, , params]) => [params.page, params.limit, params.sortBy])).toEqual([
      [1, 20, 'name'],
      [2, 20, 'name'],
    ]);
    expect(childIdsOf('folder-designs')).toHaveLength(40);
    expect(useKnowledgeBaseStore.getState().nodeChildrenPagination.get('folder-designs')).toEqual(
      expect.objectContaining({ hasNext: true, nextPage: 3 }),
    );
  });

  it('shows the new name of a folder renamed inside an open folder while the page shows the collection', async () => {
    const MOCKUPS = hubNode({ id: 'folder-mockups', name: 'Mockups', nodeType: 'folder', parentId: 'folder-designs' });
    let renamed = false;
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(async (_type: string, id: string) =>
      hubResponse(
        id === 'kb-eng'
          ? [DESIGNS, SPECS_DIR]
          : id === 'folder-designs'
            ? [renamed ? { ...MOCKUPS, name: 'Wireframes' } : MOCKUPS]
            : [],
      ),
    );
    api.kb.renameNode.mockImplementation(async () => {
      renamed = true;
      return {};
    });
    openEngineeringWithSidebar();
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    await act(async () => {
      fireEvent.click(sidebarChevron('Designs'));
    });
    await waitFor(() => expect(within(sidebar).getByText('Mockups')).toBeTruthy());

    await renameInSidebar('Mockups', 'Wireframes');

    await waitFor(() => expect(api.kb.renameNode).toHaveBeenCalled());
    await waitFor(() => expect(within(sidebar).queryByText('Mockups')).toBeNull());
    expect(within(sidebar).getByText('Wireframes')).toBeTruthy();
  });

  it('shows a folder renamed from the sidebar under its open collection, with its new name', async () => {
    const renamedSpecs = { ...SPECS_DIR, name: 'Specifications' };
    let renamed = false;
    withCollections([ENGINEERING]);
    api.hub.getNodeChildren.mockImplementation(async (_type: string, id: string) =>
      hubResponse(id === 'kb-eng' ? [DESIGNS, renamed ? renamedSpecs : SPECS_DIR] : []),
    );
    api.hub.loadFolderData.mockResolvedValue(engineeringContents([DESIGNS, SPECS_DIR]));
    api.kb.renameNode.mockImplementation(async () => {
      renamed = true;
      return {};
    });
    nav.current!.reset('/knowledge-base?nodeType=app&nodeId=kb-eng');
    const view = renderInTheme(
      <>
        <div data-testid="sidebar-slot">
          <KnowledgeBaseSidebarSlot />
        </div>
        <KnowledgeBasePage />
      </>,
    );
    sidebar = within(view.container).getByTestId('sidebar-slot');
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));

    await renameInSidebar('Specs', 'Specifications');

    await waitFor(() => expect(api.kb.renameNode).toHaveBeenCalled());
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    expect(within(sidebar).getByText('Specifications')).toBeTruthy();
  });

  it.each([
    { sharing: 'private', engineering: ENGINEERING },
    { sharing: 'shared', engineering: collection('kb-eng', 'Engineering', { sharingStatus: 'shared' }) },
  ])('opens folders at any depth before and after "load more" ($sharing collection)', async ({ engineering }) => {
    api.hub.getNavigationNodes.mockImplementation(async ({ page }: { page?: number }) =>
      (page ?? 1) === 1
        ? hubResponse([engineering], {
            pagination: { page: 1, limit: 20, totalItems: 21, totalPages: 2, hasNext: true, hasPrev: false },
          })
        : hubResponse([SALES], {
            pagination: { page: 2, limit: 20, totalItems: 21, totalPages: 2, hasNext: false, hasPrev: true },
          }),
    );
    const children: Record<string, typeof DESIGNS[]> = {
      'kb-eng': [DESIGNS, SPECS_DIR],
      'folder-designs': [MOCKUPS],
      'folder-specs': [DRAFTS],
    };
    api.hub.getNodeChildren.mockImplementation(async (_type: string, id: string) => hubResponse(children[id] ?? []));
    api.hub.loadFolderData.mockResolvedValue(engineeringContents([DESIGNS, SPECS_DIR]));
    nav.current!.reset('/knowledge-base?nodeType=app&nodeId=kb-eng');
    const view = renderInTheme(
      <>
        <div data-testid="sidebar-slot">
          <KnowledgeBaseSidebarSlot />
        </div>
        <KnowledgeBasePage />
      </>,
    );
    sidebar = within(view.container).getByTestId('sidebar-slot');
    await screen.findByRole('row', { name: 'Designs' });
    await waitFor(() => expect(childIdsOf('kb-eng')).toEqual(['folder-designs', 'folder-specs']));
    await waitFor(() => expect(useKnowledgeBaseStore.getState().appRootListPagination).toEqual({ hasNext: true, nextPage: 2 }));

    await act(async () => {
      fireEvent.click(sidebarChevron('Designs'));
    });
    await waitFor(() => expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']));

    await act(async () => {
      await loadMoreRootAppList();
    });
    expect(sidebarIds().sort()).toEqual(['kb-eng', 'kb-sales']);
    expect(childIdsOf('folder-designs')).toEqual(['folder-mockups']);
    expect(within(sidebar).getByText('Mockups')).toBeTruthy();
    expect(useKnowledgeBaseStore.getState().nodes.map((n) => n.id)).toContain('folder-designs');

    await act(async () => {
      fireEvent.click(sidebarChevron('Specs'));
    });
    await waitFor(() => expect(childIdsOf('folder-specs')).toEqual(['folder-drafts']));
    expect(within(sidebar).getByText('Drafts')).toBeTruthy();
  });
});
