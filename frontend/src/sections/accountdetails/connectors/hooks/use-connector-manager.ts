/**
 * useConnectorManager Hook
 *
 * Hook for managing a specific connector instance.
 * Handles fetching instance data, authentication, toggling, and configuration.
 */

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useAccountType } from 'src/hooks/use-account-type';
import { Connector, ConnectorConfig, ConnectorToggleType } from '../types/types';
import { ConnectorApiService } from '../services/api';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';

interface UseConnectorManagerReturn {
  // State
  connector: Connector | null;
  connectorConfig: ConnectorConfig | null;
  loading: boolean;
  error: string | null;
  success: boolean;
  successMessage: string;
  isAuthenticated: boolean;
  configDialogOpen: boolean;
  filterOptions: any | null;
  showFilterDialog: boolean;
  isEnablingWithFilters: boolean;

  // Actions
  handleToggleConnector: (enabled: boolean, type: ConnectorToggleType) => Promise<void>;
  handleAuthenticate: () => Promise<void>;
  handleConfigureClick: () => void;
  handleConfigClose: () => void;
  handleConfigSuccess: () => void;
  handleRefresh: () => void;
  handleDeleteInstance: () => Promise<void>;
  handleRenameInstance: (newName: string) => Promise<void>;
  handleFilterSelection: (filters: any) => Promise<void>;
  handleFilterDialogClose: () => void;
  setError: (error: string | null) => void;
  setSuccess: (success: boolean) => void;
  setSuccessMessage: (message: string) => void;
}

export const useConnectorManager = (): UseConnectorManagerReturn => {
  const { connectorId } = useParams<{ connectorId: string }>();

  // State
  const [connector, setConnector] = useState<Connector | null>(null);
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [filterOptions, setFilterOptions] = useState<any | null>(null);
  const [showFilterDialog, setShowFilterDialog] = useState(false);
  const [isEnablingWithFilters, setIsEnablingWithFilters] = useState(false);
  const { isBusiness } = useAccountType();
  // Check authentication status
  const isConnectorAuthenticated = useCallback(
    (connectorParam: Connector, config: any): boolean => {
      const authType = (connectorParam.authType || '').toUpperCase();

      if (authType === 'OAUTH') {
        return config?.isAuthenticated || false;
      }
      return !!connectorParam.isConfigured;
    },
    []
  );

  // Fetch connector data
  const fetchConnectorData = useCallback(async () => {
    if (!connectorId) {
      setError('Connector key is missing');
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Fetch connector instance by connectorId
      const instance = await ConnectorApiService.getConnectorInstance(connectorId);

      if (!instance) {
        setError(`Connector instance not found`);
        return;
      }

      setConnector(instance);

      // Fetch connector configuration
      try {
        const config = await ConnectorApiService.getConnectorInstanceConfig(connectorId);
        setConnectorConfig(config);
        setIsAuthenticated(isConnectorAuthenticated(instance, config));
      } catch (configError) {
        console.warn('Could not fetch connector config:', configError);
        // Continue without config - connector might not be configured yet
      }
    } catch (err) {
      console.error('Error fetching connector data:', err);
      setError('Failed to load connector information');
    } finally {
      setLoading(false);
    }
  }, [connectorId, isConnectorAuthenticated]);

  // Handle connector toggle (enable/disable)
  const handleToggleConnector = useCallback(
    async (enabled: boolean, type: ConnectorToggleType) => {
      if (!connector || !connectorId) return;

      try {
        // When enabling, check if there are filter options to present first
        // if (enabled) {
        //   try {
        //     const response = await ConnectorApiService.getConnectorInstanceFilterOptions(connectorId);
        //     const options = response?.filterOptions;
        //     const hasAnyOptions =
        //       !!options &&
        //       Object.values(options).some((v: any) => (Array.isArray(v) ? v.length > 0 : !!v));
        //     if (hasAnyOptions) {
        //       setFilterOptions(options);
        //       setIsEnablingWithFilters(true);
        //       setShowFilterDialog(true);
        //       return; // show dialog instead of immediate toggle
        //     }
        //   } catch (fetchFiltersError) {
        //     // If fetching filter options fails, proceed to toggle without filters
        //     console.warn(
        //       'Could not fetch filter options, proceeding to toggle:',
        //       fetchFiltersError
        //     );
        //   }
        // }

        const wasActive = !!connector.isActive;
        const selectedStrategy = String(
          connectorConfig?.config?.sync?.selectedStrategy || ''
        ).toUpperCase();
        const scheduledCfg = (connectorConfig?.config?.sync?.scheduledConfig || {}) as any;

        const successResponse = await ConnectorApiService.toggleConnectorInstance(connectorId, type);

        if (successResponse) {
          if (type === 'sync') {
            setConnector((prev) => (prev ? { ...prev, isActive: enabled } : null));
          } else if (type === 'agent') {
            setConnector((prev) => (prev ? { ...prev, isAgentActive: enabled } : null));
          }
          const action = enabled ? 'enabled' : 'disabled';
          setSuccessMessage(`${connector.name} ${type} ${action} successfully`);
          setSuccess(true);
          setTimeout(() => setSuccess(false), 4000);

          // Scheduling behavior tied to enabling/active transitions
          try {
            if (enabled && !wasActive) {
              // First-time enable (or enabling from inactive): if strategy is SCHEDULED, schedule now
              const hasRequiredSchedule =
                scheduledCfg && (scheduledCfg.intervalMinutes || scheduledCfg.cronExpression);
              if (selectedStrategy === 'SCHEDULED' && hasRequiredSchedule) {
                const cron = buildCronFromSchedule({
                  startTime: scheduledCfg.startTime,
                  intervalMinutes: scheduledCfg.intervalMinutes,
                  timezone: (scheduledCfg.timezone || 'UTC').toUpperCase(),
                });
                await CrawlingManagerApi.schedule(connector.type.toLowerCase(), connectorId, {
                  scheduleConfig: {
                    scheduleType: 'custom',
                    isEnabled: true,
                    timezone: (scheduledCfg.timezone || 'UTC').toUpperCase(),
                    cronExpression: cron,
                  },
                  priority: 5,
                  maxRetries: 3,
                  timeout: 300000,
                });
              }
            } else if (!enabled && wasActive) {
              // Disabling: remove any existing schedule
              try {
                await CrawlingManagerApi.remove(connector.type.toLowerCase(), connectorId);
              } catch (removeError) {
                console.error('Failed to remove schedule on disable:', removeError);
              }
            }
          } catch (scheduleError) {
            console.error('Scheduling operation failed:', scheduleError);
          }
        } else {
          setError(`Failed to ${enabled ? 'enable' : 'disable'} connector`);
        }
      } catch (err) {
        console.error('Error toggling connector:', err);
        setError(`Failed to ${enabled ? 'enable' : 'disable'} connector`);
      }
    },
    [connector, connectorId, connectorConfig, setSuccess, setSuccessMessage, setError]
  );

  const handleFilterSelection = useCallback(
    async (filters: any) => {
      if (!connectorId || !connector) return;
      try {
        setLoading(true);
        await ConnectorApiService.saveConnectorInstanceFilters(connectorId, filters);
        // After saving filters, enable the connector
        const successResponse = await ConnectorApiService.toggleConnectorInstance(connectorId, 'sync');
        if (successResponse) {
          setConnector((prev) => (prev ? { ...prev, isActive: true } : null));
          setSuccessMessage(`${connector.name} enabled successfully`);
          setSuccess(true);
          setTimeout(() => setSuccess(false), 4000);
        } else {
          setError('Failed to enable connector');
        }
      } catch (err) {
        console.error('Error saving filters / enabling connector:', err);
        setError('Failed to save filters or enable connector');
      } finally {
        setShowFilterDialog(false);
        setIsEnablingWithFilters(false);
        setLoading(false);
      }
    },
    [connectorId, connector]
  );

  const handleFilterDialogClose = useCallback(() => {
    setShowFilterDialog(false);
    setIsEnablingWithFilters(false);
  }, []);

  // Handle configuration dialog
  const handleConfigureClick = useCallback(() => {
    setConfigDialogOpen(true);
  }, []);

  const handleConfigClose = useCallback(() => {
    setConfigDialogOpen(false);
  }, []);

  const handleConfigSuccess = useCallback(() => {
    setConfigDialogOpen(false);
    setSuccessMessage(`${connector?.name} configured successfully`);
    setSuccess(true);

    // Refresh connector data
    fetchConnectorData();

    // Clear success message after 4 seconds
    setTimeout(() => setSuccess(false), 4000);
  }, [connector, fetchConnectorData]);

  // Handle refresh
  const handleRefresh = useCallback(() => {
    fetchConnectorData();
  }, [fetchConnectorData]);

  // Handle authentication (only for OAuth)
  const handleAuthenticate = useCallback(async () => {
    if (!connector || !connectorId) return;

    try {
      setLoading(true);

      // Check if it's OAuth connector
      if ((connector.authType || '').toUpperCase() === 'OAUTH') {
        // Get OAuth authorization URL using connectorId
        const { authorizationUrl } =
          await ConnectorApiService.getOAuthAuthorizationUrl(connectorId);

        // Open OAuth in a new tab and focus it
        const oauthTab = window.open(authorizationUrl, '_blank');
        oauthTab?.focus();

        // Listen for OAuth success message from callback page
        const handleOAuthMessage = async (event: MessageEvent) => {
          if (event.origin !== window.location.origin) return;

          if (event.data.type === 'OAUTH_SUCCESS' && event.data.connectorId === connectorId) {
            try {
              // OAuth completed successfully
              const refreshed = await ConnectorApiService.getConnectorInstanceConfig(connectorId);
              setConnectorConfig(refreshed);
              setIsAuthenticated(true);

              // Show success message
              setSuccessMessage('Authentication successful');
              setSuccess(true);
              setTimeout(() => setSuccess(false), 4000);

              // Clean up
              window.removeEventListener('message', handleOAuthMessage);
            } catch (oauthError) {
              console.error('Error handling OAuth success:', oauthError);
              setError('Failed to complete authentication');
            }
          }
        };

        window.addEventListener('message', handleOAuthMessage);

        // Clean up listener if window is closed manually
        const checkClosed = setInterval(() => {
          if (oauthTab && oauthTab.closed) {
            window.removeEventListener('message', handleOAuthMessage);
            clearInterval(checkClosed);
          }
        }, 1000);

        // Clean up after 5 minutes
        setTimeout(() => {
          window.removeEventListener('message', handleOAuthMessage);
          clearInterval(checkClosed);
        }, 300000);
      }
    } catch (authError) {
      console.error('Authentication error:', authError);
      setError('Authentication failed');
    } finally {
      setLoading(false);
    }
  }, [connector, connectorId]);

  // Handle delete instance
  const handleDeleteInstance = useCallback(async () => {
    if (!connectorId) return;

    try {
      setLoading(true);
      await ConnectorApiService.deleteConnectorInstance(connectorId);
      setLoading(false);
      setSuccessMessage('Connector instance deleted successfully');
      setSuccess(true);

      // Navigate back to connectors list after a short delay
      setTimeout(() => {
        window.location.href = isBusiness
          ? '/account/company-settings/settings/connector'
          : '/account/individual/settings/connector';
      }, 1500);
    } catch (err) {
      console.error('Error deleting connector instance:', err);
      setError('Failed to delete connector instance');
    } finally {
      setLoading(false);
    }
  }, [connectorId, isBusiness]);

  // Handle rename instance
  const handleRenameInstance = useCallback(
    async (newName: string) => {
      if (!connectorId || !newName.trim()) return;
      try {
        setLoading(true);
        const { connector: updated } = await ConnectorApiService.updateConnectorInstanceName(
          connectorId,
          newName.trim()
        );
        setConnector((prev) => (prev ? { ...prev, name: updated.name } : prev));
        setSuccessMessage('Instance name updated');
        setSuccess(true);
        setTimeout(() => setSuccess(false), 3000);
      } catch (err) {
        console.error('Error renaming connector instance:', err);
        setError('Failed to update instance name');
      } finally {
        setLoading(false);
      }
    },
    [connectorId]
  );

  // Initialize
  useEffect(() => {
    fetchConnectorData();
  }, [fetchConnectorData]);

  // Handle OAuth success/error from URL parameters
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const oauthSuccess = urlParams.get('oauth_success');
    const oauthError = urlParams.get('oauth_error');

    if (oauthSuccess === 'true' && connectorId) {
      const handleOAuthSuccess = async () => {
        try {
          const refreshed = await ConnectorApiService.getConnectorInstanceConfig(connectorId);
          setConnectorConfig(refreshed);
          setIsAuthenticated(true);

          setSuccessMessage('Authentication successful');
          setSuccess(true);
          setTimeout(() => setSuccess(false), 4000);
        } catch (oauthSuccessError) {
          console.error('Error handling OAuth success:', oauthSuccessError);
          setError('Failed to complete authentication');
        }
      };

      handleOAuthSuccess();

      // Clean up URL parameters
      const newUrl = window.location.pathname;
      window.history.replaceState({}, document.title, newUrl);
    } else if (oauthError && connector) {
      setError(`OAuth authentication failed: ${oauthError}`);

      // Clean up URL parameters
      const newUrl = window.location.pathname;
      window.history.replaceState({}, document.title, newUrl);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    // State
    connector,
    connectorConfig,
    loading,
    error,
    success,
    successMessage,
    isAuthenticated,
    configDialogOpen,
    filterOptions,
    showFilterDialog,
    isEnablingWithFilters,

    // Actions
    handleToggleConnector,
    handleAuthenticate,
    handleConfigureClick,
    handleConfigClose,
    handleConfigSuccess,
    handleRefresh,
    handleDeleteInstance,
    handleRenameInstance,
    handleFilterSelection,
    handleFilterDialogClose,
    setError,
    setSuccess,
    setSuccessMessage,
  };
};
