import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { devtools } from 'zustand/middleware';
import type {
  Connector,
  ConnectorScope,
  TeamFilterTab,
  PersonalFilterTab,
  ConnectorConfig,
  ConnectorSchemaResponse,
  PanelFormData,
  PanelTab,
  PanelView,
  SyncStrategy,
  AuthCardState,
  ConnectorInstance,
  InstancePanelTab,
  ConnectorStatsResponse,
} from './types';
import { mergeConfigWithSchema, initializeFormData } from './utils/config-merge';
import { evaluateConditionalDisplay } from './utils/conditional-display';
import {
  isNoneAuthType,
  isOAuthType,
  isConnectorConfigAuthenticated,
} from './utils/auth-helpers';

// ========================================
// Store shape
// ========================================

interface ConnectorsState {
  // ── Data ──────────────────────────────────────────────────────
  /** Connectors from the registry endpoint (all available). */
  registryConnectors: Connector[];
  /** Connectors from the active/configured endpoint. */
  activeConnectors: Connector[];

  // ── UI ────────────────────────────────────────────────────────
  searchQuery: string;
  teamFilterTab: TeamFilterTab;
  personalFilterTab: PersonalFilterTab;
  isLoading: boolean;
  error: string | null;

  // ── Panel state ───────────────────────────────────────────────
  isPanelOpen: boolean;
  panelConnector: Connector | null;
  panelConnectorId: string | null;
  panelActiveTab: PanelTab;
  panelView: PanelView;
  /** Bumped after OAuth completes so Authorize tab remounts with fresh config. */
  oauthAuthorizeUiEpoch: number;

  // ── Schema + Config ───────────────────────────────────────────
  connectorSchema: ConnectorSchemaResponse['schema'] | null;
  connectorConfig: ConnectorConfig | null;
  isLoadingSchema: boolean;
  isLoadingConfig: boolean;
  schemaError: string | null;

  // ── Form state ────────────────────────────────────────────────
  formData: PanelFormData;
  formErrors: Record<string, string>;
  conditionalDisplay: Record<string, boolean>;

  // ── Auth state ────────────────────────────────────────────────
  selectedAuthType: string;
  authState: AuthCardState | 'authenticating';
  isAuthTypeImmutable: boolean;

  // ── Create mode ───────────────────────────────────────────────
  instanceName: string;
  instanceNameError: string | null;
  selectedScope: 'personal' | 'team';

  // ── Records selection ─────────────────────────────────────────
  selectedRecords: string[];
  availableRecords: { id: string; name: string }[];
  isLoadingRecords: boolean;

  // ── Save state ────────────────────────────────────────────────
  isSavingAuth: boolean;
  isSavingConfig: boolean;
  saveError: string | null;

  // ── Instance page state ───────────────────────────────────────
  /** Connector type instances list */
  instances: ConnectorInstance[];
  /** Per-instance config data keyed by connector _key */
  instanceConfigs: Record<string, ConnectorConfig>;
  /** Per-instance stats data keyed by connector _key */
  instanceStats: Record<string, ConnectorStatsResponse['data']>;
  /** Currently selected instance for management panel */
  selectedInstance: ConnectorInstance | null;
  /** Is management panel open */
  isInstancePanelOpen: boolean;
  /** Active tab in management panel */
  instancePanelTab: InstancePanelTab;
  /** Loading state for instances list */
  isLoadingInstances: boolean;
  /** The connector type info for the type page header */
  connectorTypeInfo: Connector | null;
  /** Show configuration success dialog */
  showConfigSuccessDialog: boolean;
  /** Newly created connector ID (for success dialog) */
  newlyConfiguredConnectorId: string | null;
  /** Bumped after connector list should refetch (create/save/delete). */
  catalogRefreshToken: number;
  /** IDs of instances we've optimistically removed; filtered out of list updates until the backend stops returning them. */
  deletedInstanceIds: string[];

  // ── Actions ───────────────────────────────────────────────────
  setRegistryConnectors: (connectors: Connector[]) => void;
  setActiveConnectors: (connectors: Connector[]) => void;
  setSearchQuery: (query: string) => void;
  setTeamFilterTab: (tab: TeamFilterTab) => void;
  setPersonalFilterTab: (tab: PersonalFilterTab) => void;
  setIsLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // Panel actions
  openPanel: (connector: Connector, connectorId?: string, scope?: ConnectorScope) => void;
  bumpCatalogRefresh: () => void;
  bumpOAuthAuthorizeUiEpoch: () => void;
  /**
   * After GET /config proves OAuth (or not), mirror `isAuthenticated` onto the open panel row
   * and matching catalog/instance rows so Authorize/Configure gates update before list refetch.
   */
  syncConnectorInstanceAuthFlags: (instanceId: string, authenticated: boolean) => void;
  closePanel: () => void;
  setPanelActiveTab: (tab: PanelTab) => void;
  setPanelView: (view: PanelView) => void;
  setSchemaAndConfig: (
    schema: ConnectorSchemaResponse['schema'],
    config?: ConnectorConfig
  ) => void;
  setAuthFormValue: (name: string, value: unknown) => void;
  setSyncFormValue: (key: string, value: unknown) => void;
  setFilterFormValue: (
    section: 'sync' | 'indexing',
    name: string,
    value: unknown
  ) => void;
  setSelectedAuthType: (authType: string) => void;
  setAuthState: (state: AuthCardState | 'authenticating') => void;
  setInstanceName: (name: string) => void;
  setInstanceNameError: (error: string | null) => void;
  setSelectedScope: (scope: 'personal' | 'team') => void;
  setSelectedRecords: (records: string[]) => void;
  setAvailableRecords: (records: { id: string; name: string }[]) => void;
  setIsLoadingSchema: (loading: boolean) => void;
  setIsLoadingConfig: (loading: boolean) => void;
  setSchemaError: (error: string | null) => void;
  setIsSavingAuth: (saving: boolean) => void;
  setIsSavingConfig: (saving: boolean) => void;
  setSaveError: (error: string | null) => void;
  /** Set or clear individual form error keys. Pass `null` to remove a key. */
  mergeFormErrors: (patch: Record<string, string | null | undefined>) => void;
  setIsLoadingRecords: (loading: boolean) => void;
  setSyncStrategy: (strategy: SyncStrategy) => void;
  setSyncInterval: (minutes: number) => void;
  reset: () => void;

  // Instance page actions
  setInstances: (instances: ConnectorInstance[]) => void;
  setInstanceConfig: (connectorId: string, config: ConnectorConfig) => void;
  setInstanceStats: (connectorId: string, stats: ConnectorStatsResponse['data']) => void;
  /** Merge one connector into active list + instance list + selected instance (no full refetch). */
  upsertConnectorInstance: (updated: Connector) => void;
  /** Drop cached config/stats for one instance (e.g. after delete starts). */
  removeConnectorInstanceCaches: (connectorId: string) => void;
  /** Remove an instance from active list + instance list + clear selection/panel. */
  removeConnectorInstance: (connectorId: string) => void;
  clearInstanceData: () => void;
  setSelectedInstance: (instance: ConnectorInstance | null) => void;
  openInstancePanel: (instance: ConnectorInstance) => void;
  closeInstancePanel: () => void;
  setInstancePanelTab: (tab: InstancePanelTab) => void;
  setIsLoadingInstances: (loading: boolean) => void;
  setConnectorTypeInfo: (connector: Connector | null) => void;
  setShowConfigSuccessDialog: (show: boolean) => void;
  setNewlyConfiguredConnectorId: (id: string | null) => void;
  /** Update the name of a connector instance in all relevant store slices. */
  renameConnectorInstance: (connectorId: string, newName: string) => void;
}

// ========================================
// Default form data
// ========================================

const defaultFormData: PanelFormData = {
  auth: {},
  sync: {
    selectedStrategy: 'MANUAL',
    scheduledConfig: { intervalMinutes: 60, timezone: 'UTC' },
    customValues: {},
  },
  filters: {
    sync: {},
    indexing: {},
  },
};

// ========================================
// Initial state
// ========================================

const initialState = {
  // Data
  registryConnectors: [] as Connector[],
  activeConnectors: [] as Connector[],

  // UI
  searchQuery: '',
  teamFilterTab: 'all' as TeamFilterTab,
  personalFilterTab: 'all' as PersonalFilterTab,
  isLoading: false,
  error: null as string | null,

  // Panel
  isPanelOpen: false,
  panelConnector: null as Connector | null,
  panelConnectorId: null as string | null,
  panelActiveTab: 'authenticate' as PanelTab,
  panelView: 'tabs' as PanelView,
  oauthAuthorizeUiEpoch: 0,

  // Schema + Config
  connectorSchema: null as ConnectorSchemaResponse['schema'] | null,
  connectorConfig: null as ConnectorConfig | null,
  isLoadingSchema: false,
  isLoadingConfig: false,
  schemaError: null as string | null,

  // Form
  formData: { ...defaultFormData },
  formErrors: {} as Record<string, string>,
  conditionalDisplay: {} as Record<string, boolean>,

  // Auth
  selectedAuthType: '',
  authState: 'empty' as AuthCardState | 'authenticating',
  isAuthTypeImmutable: false,

  // Create mode
  instanceName: '',
  instanceNameError: null as string | null,
  selectedScope: 'team' as 'personal' | 'team',

  // Records
  selectedRecords: [] as string[],
  availableRecords: [] as { id: string; name: string }[],
  isLoadingRecords: false,

  // Save
  isSavingAuth: false,
  isSavingConfig: false,
  saveError: null as string | null,

  // Instance page
  instances: [] as ConnectorInstance[],
  instanceConfigs: {} as Record<string, ConnectorConfig>,
  instanceStats: {} as Record<string, ConnectorStatsResponse['data']>,
  selectedInstance: null as ConnectorInstance | null,
  isInstancePanelOpen: false,
  instancePanelTab: 'overview' as InstancePanelTab,
  isLoadingInstances: false,
  connectorTypeInfo: null as Connector | null,
  showConfigSuccessDialog: false,
  newlyConfiguredConnectorId: null as string | null,
  catalogRefreshToken: 0,
  deletedInstanceIds: [] as string[],
};

// Panel-specific fields to reset when closing
const panelResetState = {
  isPanelOpen: false,
  panelConnector: null as Connector | null,
  panelConnectorId: null as string | null,
  panelActiveTab: 'authenticate' as PanelTab,
  panelView: 'tabs' as PanelView,
  oauthAuthorizeUiEpoch: 0,
  connectorSchema: null as ConnectorSchemaResponse['schema'] | null,
  connectorConfig: null as ConnectorConfig | null,
  isLoadingSchema: false,
  isLoadingConfig: false,
  schemaError: null as string | null,
  formData: { ...defaultFormData },
  formErrors: {} as Record<string, string>,
  conditionalDisplay: {} as Record<string, boolean>,
  selectedAuthType: '',
  authState: 'empty' as AuthCardState | 'authenticating',
  isAuthTypeImmutable: false,
  instanceName: '',
  instanceNameError: null as string | null,
  /** Intentionally omitted: closing the panel must not reset catalog scope (personal vs team). */
  selectedRecords: [] as string[],
  availableRecords: [] as { id: string; name: string }[],
  isLoadingRecords: false,
  isSavingAuth: false,
  isSavingConfig: false,
  saveError: null as string | null,
};

// ========================================
// Store
// ========================================

export const useConnectorsStore = create<ConnectorsState>()(
  devtools(
    immer((set, _get) => ({
      ...initialState,

      // ── Existing actions ──

      setRegistryConnectors: (connectors) =>
        set((s) => {
          const blocked = new Set(s.deletedInstanceIds);
          s.registryConnectors = blocked.size
            ? connectors.filter((c) => !c._key || !blocked.has(c._key))
            : connectors;
        }),

      setActiveConnectors: (connectors) =>
        set((s) => {
          const blocked = new Set(s.deletedInstanceIds);
          s.activeConnectors = blocked.size
            ? connectors.filter((c) => !c._key || !blocked.has(c._key))
            : connectors;
        }),

      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),

      setTeamFilterTab: (tab) =>
        set((s) => {
          s.teamFilterTab = tab;
        }),

      setPersonalFilterTab: (tab) =>
        set((s) => {
          s.personalFilterTab = tab;
        }),

      setIsLoading: (loading) =>
        set((s) => {
          s.isLoading = loading;
        }),

      setError: (error) =>
        set((s) => {
          s.error = error;
        }),

      // ── Panel actions ──

      openPanel: (connector, connectorId, scope) =>
        set((s) => {
          s.isPanelOpen = true;
          s.panelConnector = connector;
          s.panelConnectorId = connectorId ?? null;
          if (scope) {
            s.selectedScope = scope;
          }
          // Open tab: authenticated → Configure; OAuth pending consent → Authorize; else → Authenticate.
          const listAuthType = connector.authType ?? '';
          const rowAuthenticated = isConnectorConfigAuthenticated(connector);
          // Legacy guard: Gmail/Drive Workspace team connectors were migrated from OAUTH→CUSTOM.
          // Old DB rows still carry authType:"OAUTH" but the schema no longer supports it, so
          // opening on the Authorize tab would show an unusable OAuth consent button.
          const LEGACY_WORKSPACE_TEAM_TYPES = ['Gmail Workspace', 'Drive Workspace'];
          const isLegacyWorkspaceOAuth =
            LEGACY_WORKSPACE_TEAM_TYPES.includes(connector.type ?? '') &&
            scope === 'team' &&
            isOAuthType(listAuthType);
          if (
            connectorId &&
            (rowAuthenticated ||
              (isNoneAuthType(listAuthType) && connector.isConfigured === true))
          ) {
            s.panelActiveTab = 'configure';
          } else if (connectorId && isOAuthType(listAuthType) && !rowAuthenticated && !isLegacyWorkspaceOAuth) {
            s.panelActiveTab = 'authorize';
          } else {
            s.panelActiveTab = 'authenticate';
          }
          s.panelView = 'tabs';
          s.isAuthTypeImmutable = !!connectorId;

          // Existing instance: drop cached schema/config immediately so we never use another
          // instance's `isAuthenticated` before GET /config returns (fixes wrong Authorize tab).
          if (connectorId) {
            s.selectedAuthType = connector.authType ?? '';
            s.connectorSchema = null;
            s.connectorConfig = null;
            s.isLoadingSchema = true;
            s.isLoadingConfig = true;
          }

          // New instance: clear any previous panel schema/config/form so we never
          // flash or save against another instance's state.
          if (!connectorId) {
            s.connectorSchema = null;
            s.connectorConfig = null;
            s.isLoadingSchema = false;
            s.isLoadingConfig = false;
            s.schemaError = null;
            s.formData = { ...defaultFormData };
            s.formErrors = {};
            s.conditionalDisplay = {};
            s.selectedAuthType = '';
            s.authState = 'empty';
            s.instanceName = '';
            s.instanceNameError = null;
            s.selectedRecords = [];
            s.availableRecords = [];
            s.isLoadingRecords = false;
            s.isSavingAuth = false;
            s.isSavingConfig = false;
            s.saveError = null;
          }
        }),

      bumpCatalogRefresh: () =>
        set((s) => {
          s.catalogRefreshToken += 1;
        }),

      bumpOAuthAuthorizeUiEpoch: () =>
        set((s) => {
          s.oauthAuthorizeUiEpoch += 1;
        }),

      syncConnectorInstanceAuthFlags: (instanceId, authenticated) =>
        set((s) => {
          if (!instanceId) return;
          if (s.panelConnectorId === instanceId && s.panelConnector) {
            s.panelConnector.isAuthenticated = authenticated;
          }
          const acIdx = s.activeConnectors.findIndex((c) => c._key === instanceId);
          if (acIdx >= 0) {
            s.activeConnectors[acIdx].isAuthenticated = authenticated;
          }
          const inIdx = s.instances.findIndex((i) => i._key === instanceId);
          if (inIdx >= 0) {
            s.instances[inIdx].isAuthenticated = authenticated;
          }
          if (s.selectedInstance?._key === instanceId) {
            s.selectedInstance.isAuthenticated = authenticated;
          }
        }),

      closePanel: () =>
        set((s) => {
          Object.assign(s, panelResetState);
        }),

      setPanelActiveTab: (tab) =>
        set((s) => {
          s.panelActiveTab = tab;
        }),

      setPanelView: (view) =>
        set((s) => {
          s.panelView = view;
        }),

      setSchemaAndConfig: (schema, config) =>
        set((s) => {
          s.connectorSchema = schema;
          s.connectorConfig = config ?? null;

          const merged = mergeConfigWithSchema(config ?? null, schema);
          const configAuthType = config?.authType || '';
          const supportedTypes = schema.auth?.supportedAuthTypes ?? [];
          // Stored authType may be from a migration (e.g. OAUTH→CUSTOM). If the schema no
          // longer lists it, fall through to the schema default.
          const schemaSupportsStored =
            !configAuthType || supportedTypes.includes(configAuthType);
          const authType =
            (schemaSupportsStored ? configAuthType : '') ||
            supportedTypes[0] ||
            '';

          s.selectedAuthType = authType;
          const formData = initializeFormData(merged, authType);
          s.formData = formData;

          // Evaluate conditional display for auth
          const conditionalDisplay = evaluateConditionalDisplay(
            schema.auth?.conditionalDisplay,
            formData.auth
          );
          s.conditionalDisplay = conditionalDisplay;

          // Set auth state based on existing config
          if (isConnectorConfigAuthenticated(config)) {
            s.authState = 'success';
          } else if (isNoneAuthType(authType)) {
            s.authState = 'success';
          } else {
            s.authState = 'empty';
          }

          // If the panel was waiting on OAuth consent but auth type is no longer OAuth
          // (schema migration), reset to Authenticate tab so the fields are visible.
          if (s.panelActiveTab === 'authorize' && !isOAuthType(authType)) {
            s.panelActiveTab = 'authenticate';
          }
        }),

      setAuthFormValue: (name, value) =>
        set((s) => {
          if (value === undefined) {
            delete s.formData.auth[name];
          } else {
            s.formData.auth[name] = value;
          }
          // Clear error for this field
          delete s.formErrors[name];
          // Re-evaluate conditional display
          const schema = s.connectorSchema;
          if (schema?.auth?.conditionalDisplay) {
            const newDisplay = evaluateConditionalDisplay(
              schema.auth.conditionalDisplay,
              s.formData.auth
            );
            s.conditionalDisplay = newDisplay;
          }
        }),

      setSyncFormValue: (key, value) =>
        set((s) => {
          s.formData.sync.customValues[key] = value;
          delete s.formErrors[key];
        }),

      setFilterFormValue: (section, name, value) =>
        set((s) => {
          if (value === undefined) {
            delete s.formData.filters[section][name];
          } else {
            s.formData.filters[section][name] = value;
          }
        }),

      setSelectedAuthType: (authType) =>
        set((s) => {
          if (s.isAuthTypeImmutable) return;
          s.selectedAuthType = authType;

          // Re-initialize auth form data for this auth type
          const schema = s.connectorSchema;
          if (schema) {
            const merged = mergeConfigWithSchema(null, schema);
            const formData = initializeFormData(merged, authType);
            s.formData.auth = formData.auth;

            // Re-evaluate conditional display
            if (schema.auth?.conditionalDisplay) {
              s.conditionalDisplay = evaluateConditionalDisplay(
                schema.auth.conditionalDisplay,
                formData.auth
              );
            }
          }
        }),

      setAuthState: (state) =>
        set((s) => {
          s.authState = state;
        }),

      setInstanceName: (name) =>
        set((s) => {
          s.instanceName = name;
          s.instanceNameError = null;
        }),

      setInstanceNameError: (error) =>
        set((s) => {
          s.instanceNameError = error;
        }),

      setSelectedScope: (scope) =>
        set((s) => {
          s.selectedScope = scope;
        }),

      setSelectedRecords: (records) =>
        set((s) => {
          s.selectedRecords = records;
        }),

      setAvailableRecords: (records) =>
        set((s) => {
          s.availableRecords = records;
        }),

      setIsLoadingSchema: (loading) =>
        set((s) => {
          s.isLoadingSchema = loading;
        }),

      setIsLoadingConfig: (loading) =>
        set((s) => {
          s.isLoadingConfig = loading;
        }),

      setSchemaError: (error) =>
        set((s) => {
          s.schemaError = error;
        }),

      setIsSavingAuth: (saving) =>
        set((s) => {
          s.isSavingAuth = saving;
        }),

      setIsSavingConfig: (saving) =>
        set((s) => {
          s.isSavingConfig = saving;
        }),

      setSaveError: (error) =>
        set((s) => {
          s.saveError = error;
        }),

      mergeFormErrors: (patch) =>
        set((s) => {
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === undefined || v === '') {
              delete s.formErrors[k];
            } else {
              s.formErrors[k] = v;
            }
          }
        }),

      setIsLoadingRecords: (loading) =>
        set((s) => {
          s.isLoadingRecords = loading;
        }),

      setSyncStrategy: (strategy) =>
        set((s) => {
          s.formData.sync.selectedStrategy = strategy;
        }),

      setSyncInterval: (minutes) =>
        set((s) => {
          s.formData.sync.scheduledConfig.intervalMinutes = minutes;
        }),

      // ── Instance page actions ──

      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
          const sid = s.selectedInstance?._key;
          if (sid) {
            const updated = instances.find((i) => i._key === sid);
            if (updated) s.selectedInstance = updated;
          }
        }),

      setInstanceConfig: (connectorId, config) =>
        set((s) => {
          s.instanceConfigs[connectorId] = config;
        }),

      setInstanceStats: (connectorId, stats) =>
        set((s) => {
          s.instanceStats[connectorId] = stats;
        }),

      upsertConnectorInstance: (updated) =>
        set((s) => {
          const id = updated._key;
          if (!id) return;

          const mergeRow = <T extends Connector>(row: T): T => ({ ...row, ...updated } as T);

          const acIdx = s.activeConnectors.findIndex((c) => c._key === id);
          if (acIdx >= 0) {
            s.activeConnectors[acIdx] = mergeRow(s.activeConnectors[acIdx]);
          } else {
            s.activeConnectors.push({ ...updated });
          }

          const inIdx = s.instances.findIndex((c) => c._key === id);
          if (inIdx >= 0) {
            s.instances[inIdx] = mergeRow(s.instances[inIdx] as ConnectorInstance);
          }

          if (s.selectedInstance?._key === id) {
            s.selectedInstance = mergeRow(s.selectedInstance as ConnectorInstance);
          }
        }),

      removeConnectorInstanceCaches: (connectorId) =>
        set((s) => {
          if (!connectorId) return;
          delete s.instanceConfigs[connectorId];
          delete s.instanceStats[connectorId];
        }),

      removeConnectorInstance: (connectorId) =>
        set((s) => {
          if (!connectorId) return;
          s.activeConnectors = s.activeConnectors.filter((c) => c._key !== connectorId);
          s.instances = s.instances.filter((i) => i._key !== connectorId);
          s.registryConnectors = s.registryConnectors.filter((c) => c._key !== connectorId);
          delete s.instanceConfigs[connectorId];
          delete s.instanceStats[connectorId];
          if (!s.deletedInstanceIds.includes(connectorId)) {
            s.deletedInstanceIds.push(connectorId);
          }
          if (s.selectedInstance?._key === connectorId) {
            s.selectedInstance = null;
            s.isInstancePanelOpen = false;
            s.instancePanelTab = 'overview';
          }
        }),

      clearInstanceData: () =>
        set((s) => {
          s.instances = [];
          s.instanceConfigs = {};
          s.instanceStats = {};
        }),

      setSelectedInstance: (instance) =>
        set((s) => {
          s.selectedInstance = instance;
        }),

      openInstancePanel: (instance) =>
        set((s) => {
          s.selectedInstance = instance;
          s.isInstancePanelOpen = true;
          s.instancePanelTab = 'overview';
        }),

      closeInstancePanel: () =>
        set((s) => {
          s.isInstancePanelOpen = false;
          s.selectedInstance = null;
          s.instancePanelTab = 'overview';
        }),

      setInstancePanelTab: (tab) =>
        set((s) => {
          s.instancePanelTab = tab;
        }),

      setIsLoadingInstances: (loading) =>
        set((s) => {
          s.isLoadingInstances = loading;
        }),

      setConnectorTypeInfo: (connector) =>
        set((s) => {
          s.connectorTypeInfo = connector;
        }),

      setShowConfigSuccessDialog: (show) =>
        set((s) => {
          s.showConfigSuccessDialog = show;
        }),

      setNewlyConfiguredConnectorId: (id) =>
        set((s) => {
          s.newlyConfiguredConnectorId = id;
        }),

      renameConnectorInstance: (connectorId, newName) =>
        set((s) => {
          if (!connectorId || !newName?.trim()) return;
          const acIdx = s.activeConnectors.findIndex((c) => c._key === connectorId);
          if (acIdx >= 0) {
            s.activeConnectors[acIdx].name = newName;
          }
          const inIdx = s.instances.findIndex((i) => i._key === connectorId);
          if (inIdx >= 0) {
            s.instances[inIdx].name = newName;
          }
          if (s.selectedInstance?._key === connectorId) {
            s.selectedInstance.name = newName;
          }
          if (s.panelConnector?._key === connectorId) {
            s.panelConnector.name = newName;
          }
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'connectors-store' }
  )
);
