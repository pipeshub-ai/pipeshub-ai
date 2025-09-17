import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';

interface UseConnectorManagerReturn {
  // State
  connector: Connector | null;
  connectorConfig: ConnectorConfig | null;
  loading: boolean;
  error: string | null;
  success: boolean;
  successMessage: string;
  isAuthenticated: boolean;
  filterOptions: any;
  showFilterDialog: boolean;
  isEnablingWithFilters: boolean;
  configDialogOpen: boolean;

  // Actions
  handleToggleConnector: (enabled: boolean) => Promise<void>;
  handleAuthenticate: () => Promise<void>;
  handleConfigureClick: () => void;
  handleConfigClose: () => void;
  handleConfigSuccess: () => void;
  handleRefresh: () => void;
  handleFilterSelection: (selectedFilters: any) => Promise<void>;
  handleFilterDialogClose: () => void;
  setError: (error: string | null) => void;
  setSuccess: (success: boolean) => void;
  setSuccessMessage: (message: string) => void;
}

export const useConnectorManager = (): UseConnectorManagerReturn => {
  const { connectorName } = useParams<{ connectorName: string }>();

  // State
  const [connector, setConnector] = useState<Connector | null>(null);
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [filterOptions, setFilterOptions] = useState<any>(null);
  const [showFilterDialog, setShowFilterDialog] = useState(false);
  const [isEnablingWithFilters, setIsEnablingWithFilters] = useState(false);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);

  // Simplified helper function to check authentication status
  const isConnectorAuthenticated = useCallback((connectorParam: Connector, config: any): boolean => {
    const authType = (connectorParam.authType || '').toUpperCase();
    
    if (authType === 'OAUTH') {
      const creds = config?.config?.credentials;
      return !!(creds && creds.access_token);
    }
    if (authType === 'OAUTH_ADMIN_CONSENT') {
      const authConfig = config?.config?.auth;
      const hasAdminEmail = authConfig?.adminEmail;
      const hasServiceAccountCredentials = authConfig?.client_id && authConfig?.project_id && authConfig?.type === 'service_account';
      return !!(hasAdminEmail && hasServiceAccountCredentials);
    }
    return !!connectorParam.isConfigured;
  }, []);

  // Fetch connector data
  const fetchConnectorData = useCallback(async () => {
    if (!connectorName) {
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Fetch connector info
      const connectors = await ConnectorApiService.getConnectors();
      const foundConnector = connectors.find(
        (c) => c.name.toLowerCase() === connectorName.toLowerCase()
      );

      if (!foundConnector) {
        setError(`Connector "${connectorName}" not found`);
        return;
      }

      setConnector(foundConnector);

      // Fetch connector configuration
      try {
        const config = await ConnectorApiService.getConnectorConfig(connectorName);
        setConnectorConfig(config);

        // Use simplified authentication check
        setIsAuthenticated(isConnectorAuthenticated(foundConnector, config));
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
  }, [connectorName, isConnectorAuthenticated]);

  // Handle connector toggle (enable/disable)
  const handleToggleConnector = useCallback(
    async (enabled: boolean) => {
      if (!connector) return;

      // TODO: Re-enable filter dialog functionality in the future
      // For now, skip filter dialog and enable/disable directly
      
      // If enabling, fetch filter options in background (for future use)
    //   if (enabled) {
    //     try {
    //       const { filterOptions: fetchedFilterOptions } = await ConnectorApiService.getConnectorFilterOptions(connector.name);
    //       setFilterOptions(fetchedFilterOptions);
    //       // Note: Not showing dialog for now, but keeping the data for future use
    //     } catch (err) {
    //       console.error('Failed to fetch filter options:', err);
    //       // Continue with enable flow even if filter fetching fails
    //     }
    //   }

      // Proceed with toggle
      const successResponse = await ConnectorApiService.toggleConnector(connector.name);

      if (successResponse) {
        // Update local state
        setConnector((prev) => (prev ? { ...prev, isActive: enabled } : null));

        const action = enabled ? 'enabled' : 'disabled';
        setSuccessMessage(`${connector.name} ${action} successfully`);
        setSuccess(true);

        // Clear success message after 4 seconds
        setTimeout(() => setSuccess(false), 4000);
      } else {
        setError(`Failed to ${enabled ? 'enable' : 'disable'} connector`);
      }
    },
    [connector]
  );

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
    if (!connector) return;

    try {
      setLoading(true);
      
      // Check if it's OAuth connector
      if ((connector.authType || '').toUpperCase() === 'OAUTH') {
        // Get OAuth authorization URL
        const { authorizationUrl } = await ConnectorApiService.getOAuthAuthorizationUrl(connector.name);
        
        // Open OAuth in a new tab and focus it
        const oauthTab = window.open(authorizationUrl, '_blank');
        oauthTab?.focus();

        // Listen for OAuth success message from callback page
        const handleOAuthMessage = async (event: MessageEvent) => {
          if (event.origin !== window.location.origin) return;
          
          if (event.data.type === 'OAUTH_SUCCESS' && event.data.connector === connector.name) {
            try {
              // OAuth completed successfully
              const refreshed = await ConnectorApiService.getConnectorConfig(connector.name);
              setConnectorConfig(refreshed);
              setIsAuthenticated(true);
              
            //   // Get filter options in background (for future use)
            //   try {
            //     const { filterOptions: fetchedFilterOptions } = await ConnectorApiService.getConnectorFilterOptions(connector.name);
            //     setFilterOptions(fetchedFilterOptions);
            //     // Note: Not showing dialog for now, but keeping the data for future use
            //   } catch (filterError) {
            //     console.error('Failed to get filter options:', filterError);
            //     // Continue with success flow even if filter options fail
            //   }
              
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
  }, [connector]);

  // Handle filter selection
  const handleFilterSelection = useCallback(async (selectedFilters: any) => {
    // Update connector config with selected filters
    if (connectorConfig) {
      const updatedConfig = {
        ...connectorConfig,
        config: {
          ...connectorConfig.config,
          filters: {
            ...connectorConfig.config.filters,
            values: selectedFilters
          }
        }
      };
      
      try {
        // Save the updated config
        await ConnectorApiService.updateConnectorConfig(connector!.name, updatedConfig.config);
        setConnectorConfig(updatedConfig);
        
        // Now enable the connector
        const successResponse = await ConnectorApiService.toggleConnector(connector!.name);
        
        if (successResponse) {
          // Update local state
          setConnector((prev) => (prev ? { ...prev, isActive: true } : null));
          setShowFilterDialog(false);
          setIsEnablingWithFilters(false);
          setSuccessMessage(`${connector!.name} enabled and filters configured successfully`);
          setSuccess(true);
          setTimeout(() => setSuccess(false), 4000);
        } else {
          setError('Failed to enable connector after configuring filters');
          setIsEnablingWithFilters(false);
        }
      } catch (saveError) {
        console.error('Error saving filters or enabling connector:', saveError);
        setError('Failed to save filter configuration or enable connector');
        setIsEnablingWithFilters(false);
      }
    }
  }, [connector, connectorConfig]);

  // Handle filter dialog close
  const handleFilterDialogClose = useCallback(() => {
    setShowFilterDialog(false);
    setFilterOptions(null);
    setIsEnablingWithFilters(false);
  }, []);

  // Initialize
  useEffect(() => {
    fetchConnectorData();
  }, [fetchConnectorData]);

  // Handle OAuth success/error from URL parameters
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const oauthSuccess = urlParams.get('oauth_success');
    const oauthError = urlParams.get('oauth_error');
 
    // Get connector name from URL path (e.g., /account/company-settings/settings/connector/gmail)
    const pathParts = window.location.pathname.split('/');
    const connectorIndex = pathParts.findIndex(part => part === 'connector');
    const urlConnectorName = connectorIndex !== -1 && connectorIndex + 1 < pathParts.length 
      ? pathParts[connectorIndex + 1] 
      : null;


    if (oauthSuccess === 'true' && urlConnectorName) {
      // OAuth was successful, refresh the connector data and show filter dialog
      const handleOAuthSuccess = async () => {
        try {
          const refreshed = await ConnectorApiService.getConnectorConfig(urlConnectorName);
          setConnectorConfig(refreshed);
          setIsAuthenticated(true);
          
          // Get filter options in background (for future use)
        //   try {
        //     const { filterOptions: fetchedFilterOptions } = await ConnectorApiService.getConnectorFilterOptions(urlConnectorName);
        //     setFilterOptions(fetchedFilterOptions);
        //     // Note: Not showing dialog for now, but keeping the data for future use
        //   } catch (filterError) {
        //     console.error('Failed to get filter options:', filterError);
        //     // Continue with success flow even if filter options fail
        //   }
          
          // Show success message
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
      // OAuth failed, show error
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
    filterOptions,
    showFilterDialog,
    isEnablingWithFilters,
    configDialogOpen,

    // Actions
    handleToggleConnector,
    handleAuthenticate,
    handleConfigureClick,
    handleConfigClose,
    handleConfigSuccess,
    handleRefresh,
    handleFilterSelection,
    handleFilterDialogClose,
    setError,
    setSuccess,
    setSuccessMessage,
  };
};
