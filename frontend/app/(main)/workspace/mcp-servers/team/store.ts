'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { McpMyServerEntry, McpPersonalInstanceSummary, McpServerInstance, McpServerTemplate } from '../types';

type DeleteTarget = Pick<McpServerInstance, '_id' | 'name' | 'scope'>;

/**
 * The org's servers as the admin page edits them: the full records from the admin listing
 * (`listInstances`), with this admin's own connection status from `getMyMcpServers`. The edit
 * form must start from the full record; `getMyMcpServers` leaves out how a server is reached
 * whenever the caller isn't confirmed as an administrator.
 */
export function orgInstancesWithStatus(
  fullRecords: McpServerInstance[],
  withStatus: McpMyServerEntry[],
): McpMyServerEntry[] {
  const statusById = new Map(withStatus.map((entry) => [entry._id, entry]));
  return fullRecords
    .filter((instance) => instance.scope !== 'personal')
    .map((instance) => ({ isAuthenticated: false, tools: [], ...statusById.get(instance._id), ...instance }));
}

// ========================================
// State
// ========================================

interface ConfigPanelState {
  open: boolean;
  mode: 'create' | 'edit';
  /** Set in edit mode. */
  editingInstance: McpMyServerEntry | null;
  /** Set when creating from a catalog card. */
  prefillTemplate: McpServerTemplate | null;
}

interface McpTeamState {
  templates: McpServerTemplate[];
  /** From `GET /catalog`; false until loaded so STDIO is never offered by mistake. */
  customStdioAllowed: boolean;
  /**
   * Org instances merged with the *current admin's own* auth status + tools —
   * fetched via `getMyMcpServers` so the admin can authenticate/discover tools
   * for themselves the same way the personal page does.
   */
  instances: McpMyServerEntry[];
  /** Every user's personal instances, for review and removal. */
  userCreatedInstances: McpPersonalInstanceSummary[];

  isLoading: boolean;
  searchQuery: string;

  configPanel: ConfigPanelState;

  deleteTarget: DeleteTarget | null;
}

// ========================================
// Actions
// ========================================

interface McpTeamActions {
  setTemplates: (templates: McpServerTemplate[]) => void;
  setCustomStdioAllowed: (allowed: boolean) => void;
  setInstances: (instances: McpMyServerEntry[]) => void;
  setUserCreatedInstances: (instances: McpPersonalInstanceSummary[]) => void;
  setLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;

  openCreateFromTemplate: (template: McpServerTemplate) => void;
  openCreateCustom: () => void;
  openEditInstance: (instance: McpMyServerEntry) => void;
  closeConfigPanel: () => void;

  openDeleteDialog: (instance: DeleteTarget) => void;
  closeDeleteDialog: () => void;

  reset: () => void;
}

// ========================================
// Initial state
// ========================================

const initialConfigPanel: ConfigPanelState = {
  open: false,
  mode: 'create',
  editingInstance: null,
  prefillTemplate: null,
};

const initialState: McpTeamState = {
  templates: [],
  customStdioAllowed: false,
  instances: [],
  userCreatedInstances: [],
  isLoading: false,
  searchQuery: '',
  configPanel: initialConfigPanel,
  deleteTarget: null,
};

// ========================================
// Store
// ========================================

export const useMcpTeamStore = create<McpTeamState & McpTeamActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setTemplates: (templates) =>
        set((s) => {
          s.templates = templates;
        }),
      setCustomStdioAllowed: (allowed) =>
        set((s) => {
          s.customStdioAllowed = allowed;
        }),
      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
        }),
      setUserCreatedInstances: (instances) =>
        set((s) => {
          s.userCreatedInstances = instances;
        }),
      setLoading: (loading) =>
        set((s) => {
          s.isLoading = loading;
        }),
      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),

      openCreateFromTemplate: (template) =>
        set((s) => {
          s.configPanel = {
            open: true,
            mode: 'create',
            editingInstance: null,
            prefillTemplate: template,
          };
        }),
      openCreateCustom: () =>
        set((s) => {
          s.configPanel = { open: true, mode: 'create', editingInstance: null, prefillTemplate: null };
        }),
      openEditInstance: (instance) =>
        set((s) => {
          s.configPanel = { open: true, mode: 'edit', editingInstance: instance, prefillTemplate: null };
        }),
      closeConfigPanel: () =>
        set((s) => {
          s.configPanel = initialConfigPanel;
        }),

      openDeleteDialog: (instance) =>
        set((s) => {
          s.deleteTarget = instance;
        }),
      closeDeleteDialog: () =>
        set((s) => {
          s.deleteTarget = null;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'mcp-team-store' }
  )
);
