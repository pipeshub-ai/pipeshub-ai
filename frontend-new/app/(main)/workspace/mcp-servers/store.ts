import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { devtools } from 'zustand/middleware';
import type {
  MCPServerTemplate,
  MCPServerInstance,
  MCPServerTool,
  PaginationInfo,
  TabValue,
  FilterType,
} from './types';

// ============================================================================
// Store shape
// ============================================================================

interface McpServersState {
  // ── Data ────────────────────────────────────────────────────────────────
  catalogItems: MCPServerTemplate[];
  myServers: MCPServerInstance[];

  // ── Pagination ───────────────────────────────────────────────────────────
  catalogPagination: PaginationInfo | null;
  myServersPagination: PaginationInfo | null;

  // ── UI ──────────────────────────────────────────────────────────────────
  activeTab: TabValue;
  searchQuery: string;
  filterType: FilterType;
  isLoadingCatalog: boolean;
  isLoadingMyServers: boolean;
  error: string | null;

  // ── Config Dialog ────────────────────────────────────────────────────────
  isConfigDialogOpen: boolean;
  /** Existing instance being managed (manage mode) */
  selectedInstance: MCPServerInstance | null;
  /** Template being configured (create from template mode) */
  selectedTemplate: MCPServerTemplate | null;

  // ── Custom Server Dialog ─────────────────────────────────────────────────
  isCustomDialogOpen: boolean;

  // ── Tool discovery ───────────────────────────────────────────────────────
  discoveredTools: MCPServerTool[];
  isDiscoveringTools: boolean;

  // ── Actions ──────────────────────────────────────────────────────────────
  setCatalogItems: (items: MCPServerTemplate[]) => void;
  setCatalogPagination: (pagination: PaginationInfo | null) => void;
  setMyServers: (servers: MCPServerInstance[]) => void;
  appendMyServers: (servers: MCPServerInstance[]) => void;
  setMyServersPagination: (pagination: PaginationInfo | null) => void;
  setActiveTab: (tab: TabValue) => void;
  setSearchQuery: (query: string) => void;
  setFilterType: (filter: FilterType) => void;
  setIsLoadingCatalog: (loading: boolean) => void;
  setIsLoadingMyServers: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // Dialog actions
  openConfigDialog: (
    opts: { instance: MCPServerInstance } | { template: MCPServerTemplate }
  ) => void;
  closeConfigDialog: () => void;
  openCustomDialog: () => void;
  closeCustomDialog: () => void;

  // Tool discovery actions
  setDiscoveredTools: (tools: MCPServerTool[]) => void;
  setIsDiscoveringTools: (loading: boolean) => void;

  // Update a single server in the myServers list (after auth/manage changes)
  updateMyServer: (updated: MCPServerInstance) => void;
  removeMyServer: (instanceId: string) => void;

  reset: () => void;
}

// ============================================================================
// Initial state
// ============================================================================

const initialState = {
  catalogItems: [] as MCPServerTemplate[],
  myServers: [] as MCPServerInstance[],
  catalogPagination: null as PaginationInfo | null,
  myServersPagination: null as PaginationInfo | null,
  activeTab: 'my-servers' as TabValue,
  searchQuery: '',
  filterType: 'all' as FilterType,
  isLoadingCatalog: false,
  isLoadingMyServers: false,
  error: null as string | null,
  isConfigDialogOpen: false,
  selectedInstance: null as MCPServerInstance | null,
  selectedTemplate: null as MCPServerTemplate | null,
  isCustomDialogOpen: false,
  discoveredTools: [] as MCPServerTool[],
  isDiscoveringTools: false,
};

// ============================================================================
// Store
// ============================================================================

export const useMcpServersStore = create<McpServersState>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setCatalogItems: (items) =>
        set((s) => {
          s.catalogItems = items;
        }),

      setCatalogPagination: (pagination) =>
        set((s) => {
          s.catalogPagination = pagination;
        }),

      setMyServers: (servers) =>
        set((s) => {
          s.myServers = servers;
        }),

      appendMyServers: (servers) =>
        set((s) => {
          s.myServers = [...s.myServers, ...servers];
        }),

      setMyServersPagination: (pagination) =>
        set((s) => {
          s.myServersPagination = pagination;
        }),

      setActiveTab: (tab) =>
        set((s) => {
          s.activeTab = tab;
        }),

      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),

      setFilterType: (filter) =>
        set((s) => {
          s.filterType = filter;
        }),

      setIsLoadingCatalog: (loading) =>
        set((s) => {
          s.isLoadingCatalog = loading;
        }),

      setIsLoadingMyServers: (loading) =>
        set((s) => {
          s.isLoadingMyServers = loading;
        }),

      setError: (error) =>
        set((s) => {
          s.error = error;
        }),

      openConfigDialog: (opts) =>
        set((s) => {
          s.isConfigDialogOpen = true;
          s.discoveredTools = [];
          if ('instance' in opts) {
            s.selectedInstance = opts.instance;
            s.selectedTemplate = null;
          } else {
            s.selectedTemplate = opts.template;
            s.selectedInstance = null;
          }
        }),

      closeConfigDialog: () =>
        set((s) => {
          s.isConfigDialogOpen = false;
          s.selectedInstance = null;
          s.selectedTemplate = null;
          s.discoveredTools = [];
          s.isDiscoveringTools = false;
        }),

      openCustomDialog: () =>
        set((s) => {
          s.isCustomDialogOpen = true;
        }),

      closeCustomDialog: () =>
        set((s) => {
          s.isCustomDialogOpen = false;
        }),

      setDiscoveredTools: (tools) =>
        set((s) => {
          s.discoveredTools = tools;
        }),

      setIsDiscoveringTools: (loading) =>
        set((s) => {
          s.isDiscoveringTools = loading;
        }),

      updateMyServer: (updated) =>
        set((s) => {
          const idx = s.myServers.findIndex(
            (srv) => srv.instanceId === updated.instanceId
          );
          if (idx >= 0) {
            s.myServers[idx] = updated;
          }
          // Also update selectedInstance if open
          if (
            s.selectedInstance?.instanceId === updated.instanceId
          ) {
            s.selectedInstance = updated;
          }
        }),

      removeMyServer: (instanceId) =>
        set((s) => {
          s.myServers = s.myServers.filter(
            (srv) => srv.instanceId !== instanceId
          );
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'mcp-servers-store' }
  )
);
