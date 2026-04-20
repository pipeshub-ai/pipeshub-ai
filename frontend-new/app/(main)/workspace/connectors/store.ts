import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { devtools } from 'zustand/middleware';
import type {
  Connector,
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
import { isNoneAuthType } from './utils/auth-helpers';

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

  // ── Actions ───────────────────────────────────────────────────
  setRegistryConnectors: (connectors: Connector[]) => void;
  setActiveConnectors: (connectors: Connector[]) => void;
  setSearchQuery: (query: string) => void;
  setTeamFilterTab: (tab: TeamFilterTab) => void;
  setPersonalFilterTab: (tab: PersonalFilterTab) => void;
  setIsLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // Panel actions
  openPanel: (connector: Connector, connectorId?: string) => void;
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
  setIsLoadingRecords: (loading: boolean) => void;
  setSyncStrategy: (strategy: SyncStrategy) => void;
  setSyncInterval: (minutes: number) => void;
  resetPanelState: () => void;
  reset: () => void;

  // Instance page actions
  setInstances: (instances: ConnectorInstance[]) => void;
  setInstanceConfig: (connectorId: string, config: ConnectorConfig) => void;
  setInstanceStats: (connectorId: string, stats: ConnectorStatsResponse['data']) => void;
  clearInstanceData: () => void;
  setSelectedInstance: (instance: ConnectorInstance | null) => void;
  openInstancePanel: (instance: ConnectorInstance) => void;
  closeInstancePanel: () => void;
  setInstancePanelTab: (tab: InstancePanelTab) => void;
  setIsLoadingInstances: (loading: boolean) => void;
  setConnectorTypeInfo: (connector: Connector | null) => void;
  setShowConfigSuccessDialog: (show: boolean) => void;
  setNewlyConfiguredConnectorId: (id: string | null) => void;
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
};

// Panel-specific fields to reset when closing
const panelResetState = {
  isPanelOpen: false,
  panelConnector: null as Connector | null,
  panelConnectorId: null as string | null,
  panelActiveTab: 'authenticate' as PanelTab,
  panelView: 'tabs' as PanelView,
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
  selectedScope: 'team' as 'personal' | 'team',
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
          s.registryConnectors = connectors;
        }),

      setActiveConnectors: (connectors) =>
        set((s) => {
          s.activeConnectors = connectors;
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

      openPanel: (connector, connectorId) =>
        set((s) => {
          s.isPanelOpen = true;
          s.panelConnector = connector;
          s.panelConnectorId = connectorId ?? null;
          // For existing connectors that are already authenticated/configured,
          // open directly to the configure tab
          s.panelActiveTab =
            connectorId && (connector.isAuthenticated || connector.isConfigured)
              ? 'configure'
              : 'authenticate';
          s.panelView = 'tabs';
          s.isAuthTypeImmutable = !!connectorId;
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
          const authType =
            config?.authType ||
            schema.auth?.supportedAuthTypes?.[0] ||
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
          if (config?.isAuthenticated) {
            s.authState = 'success';
          } else if (isNoneAuthType(authType)) {
            s.authState = 'success';
          } else {
            s.authState = 'empty';
          }
        }),

      setAuthFormValue: (name, value) =>
        set((s) => {
          s.formData.auth[name] = value;
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
          s.formData.filters[section][name] = value;
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

      resetPanelState: () =>
        set((s) => {
          Object.assign(s, panelResetState);
        }),

      // ── Instance page actions ──

      setInstances: (instances) =>
        set((s) => {
          s.instances = instances;
        }),

      setInstanceConfig: (connectorId, config) =>
        set((s) => {
          s.instanceConfigs[connectorId] = config;
        }),

      setInstanceStats: (connectorId, stats) =>
        set((s) => {
          s.instanceStats[connectorId] = stats;
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

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'connectors-store' }
  )
);
