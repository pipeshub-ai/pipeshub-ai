import { useSearchParams } from 'react-router-dom';
import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'src/utils/axios';
import { ConnectorId } from 'src/sections/accountdetails/types/connector';
import { getConnectorById, getConnectorTitle, type ConnectorConfig } from '../components/connectors-list';

interface ConnectorStatusMap {
  [connectorId: string]: boolean;
}

interface UseConnectorManagerProps {
  connectorId: string;
  accountType?: 'individual' | 'business';
}

interface UseConnectorManagerReturn {
  // State
  isLoading: boolean;
  errorMessage: string | null;
  success: boolean;
  successMessage: string;
  configDialogOpen: boolean;
  currentConnector: string | null;
  checkingConfigs: boolean;
  lastConfigured: string | null;
  connectorStatus: ConnectorStatusMap;
  configuredStatus: ConnectorStatusMap;
  
  // Actions
  setConfigDialogOpen: (open: boolean) => void;
  setCurrentConnector: (connector: string | null) => void;
  setSuccess: (success: boolean) => void;
  setErrorMessage: (message: string | null) => void;
  
  // Methods
  fetchConnectorConfig: (connectorId: string) => Promise<any>;
  handleFileRemoved: (connectorId: string) => Promise<void>;
  handleToggleConnector: (connectorId: string, enabled: boolean) => Promise<void>;
  checkConnectorConfigurations: () => Promise<void>;
  getCleanRedirectUri: () => string;
  getConnectorTitle: (id: string) => string;
  getConnectorConfig: (id: string) => ConnectorConfig | undefined;
  clearSuccessAfterDelay: (delay?: number) => void;
}

export const useConnectorManager = ({ 
  connectorId, 
  accountType = 'business' 
}: UseConnectorManagerProps): UseConnectorManagerReturn => {
  const [searchParams] = useSearchParams();
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('Connector settings updated successfully');
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [currentConnector, setCurrentConnector] = useState<string | null>(null);

  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [lastConfigured, setLastConfigured] = useState<string | null>(null);
  const [connectorStatus, setConnectorStatus] = useState<ConnectorStatusMap>({});
  const [configuredStatus, setConfiguredStatus] = useState<ConnectorStatusMap>({});

  // Use refs to store the latest functions to avoid circular dependencies
  const checkConnectorConfigurationsRef = useRef<() => Promise<void>>();
  const clearSuccessAfterDelayRef = useRef<(delay?: number) => void>();
  // Function to clear success state after delay
  const clearSuccessAfterDelay = useCallback((delay: number = 6000) => {
    setTimeout(() => {
      setSuccess(false);
    }, delay);
  }, []);

  // Store the latest version in ref
  clearSuccessAfterDelayRef.current = clearSuccessAfterDelay;

  // Get connector configuration
  const getConnectorConfig = useCallback((id: string) => 
    getConnectorById(id), []);

  // Get clean redirect URI
  const getCleanRedirectUri = useCallback(() => {
    const url = new URL(window.location.href);
    url.hash = '';
    url.search = '';
    return url.toString();
  }, []);

  // Fetch connector config
  const fetchConnectorConfig = useCallback(async (id: string) => {
    const connector = getConnectorConfig(id);
    const endpoint = connector?.apiEndpoint || '/api/v1/connectors/config';
    
    try {
      const response = await axios.get(endpoint, {
        params: {
          service: id,
        },
      });
      console.log("connector config response", response.data);
      return response.data;
    } catch (err: any) {
      if (err.response?.status === 204) {
        return null;
      }
      setErrorMessage(`Failed to fetch ${id} connector configuration. ${err.message}`);
      return null;
    }
  }, [getConnectorConfig]);

  // Check if a connector is configured based on its config type and response data
  const isConnectorConfigured = useCallback((connector: ConnectorConfig, result: any): boolean => {
    if (!result || !connector) return false;

    // For Google Workspace (OAuth-based, doesn't require credentials)
    if (connector.id === ConnectorId.GOOGLE_WORKSPACE) {
      // Check if we have any configuration data at all
      return Object.keys(result).length > 0 && (
        result.googleClientId || 
        result.googleClientSecret || 
        result.accessToken ||
        result.refreshToken
      );
    }

    // For connectors that require credentials (like OneDrive, SharePoint, Atlassian)
    if (connector.requiresCredentials) {
      return !!(result.clientId || result.clientSecret || result.tenantId || result.accessToken);
    }

    // For other connectors, check if we have any configuration data
    return Object.keys(result).length > 0;
  }, []);

  // Handle file removal
  const handleFileRemoved = useCallback(async (id: string) => {
    setConfiguredStatus((prev) => ({
      ...prev,
      [id]: false,
    }));

    if (connectorStatus[id]) {
      try {
        await axios.post(`/api/v1/connectors/disable`, null, {
          params: {
            service: id,
          },
        });

        setConnectorStatus((prev) => ({
          ...prev,
          [id]: false,
        }));

        setSuccessMessage(`${getConnectorTitle(id)} disabled successfully`);
        setSuccess(true);
      } catch (disableError: any) {
        setErrorMessage(`Failed to disable ${getConnectorTitle(id)}: ${disableError.message}`);
      }
    }
  }, [connectorStatus]);

  // Check connector configurations
  const checkConnectorConfigurations = useCallback(async () => {
    console.log('checkConnectorConfigurations: Starting with connectorId:', connectorId);
    setCheckingConfigs(true);
    try {
      const connector = getConnectorConfig(connectorId);
      console.log("connector config", connector);
      if (!connector) {
        console.log('checkConnectorConfigurations: No connector found, returning');
        setCheckingConfigs(false);
        return;
      }

      const result = await fetchConnectorConfig(connectorId);
      console.log("config result", result);
      const isConfigured = isConnectorConfigured(connector, result);
      console.log("isConfigured", isConfigured);
      setConfiguredStatus((prev) => ({
        ...prev,
        [connectorId]: isConfigured,
      }));
    } catch (err) {
      console.error('Error checking connector configurations:', err);
    } finally {
      setCheckingConfigs(false);
    }
  }, [connectorId, getConnectorConfig, fetchConnectorConfig, isConnectorConfigured]);

  // Store the latest version in ref
  checkConnectorConfigurationsRef.current = checkConnectorConfigurations;

  // Handle connector toggle
  const handleToggleConnector = useCallback(async (id: string, enabled: boolean) => {
    setIsLoading(true);
    setErrorMessage(null);

    try {
      const endpoint = enabled ? '/api/v1/connectors/enable' : '/api/v1/connectors/disable';
      await axios.post(endpoint, null, {
        params: {
          service: id,
        },
      });

      setConnectorStatus((prev) => ({
        ...prev,
        [id]: enabled,
      }));

      const action = enabled ? 'enabled' : 'disabled';
      setSuccessMessage(`${getConnectorTitle(id)} ${action} successfully`);
      setSuccess(true);
      
      // Clear success state after 6 seconds
      if (clearSuccessAfterDelayRef.current) {
        clearSuccessAfterDelayRef.current(6000);
      }
      
      // Check configurations immediately after toggle
      if (checkConnectorConfigurationsRef.current) {
        await checkConnectorConfigurationsRef.current();
      }
    } catch (error: any) {
      setErrorMessage(`Failed to ${enabled ? 'enable' : 'disable'} ${getConnectorTitle(id)}: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Initialize connector status
  useEffect(() => {
    console.log('useConnectorManager: Initializing with connectorId:', connectorId);
    const initializeStatus = async () => {
      try {
        console.log('useConnectorManager: Fetching connector status...');
        // Fetch current connector status
        const response = await axios.get('/api/v1/connectors/status');
        const { data } = response;
        console.log('useConnectorManager: Status response:', data);

        const statusMap: ConnectorStatusMap = {};
        if (data) {
          data.forEach((connector: any) => {
            statusMap[connector.id] = Boolean(connector.isEnabled);
          });
        }

        setConnectorStatus(statusMap);
        console.log('useConnectorManager: Set connector status:', statusMap);
        
        // Check configurations after status is loaded
        console.log('useConnectorManager: Checking configurations...');
        if (checkConnectorConfigurationsRef.current) {
          await checkConnectorConfigurationsRef.current();
        }
      } catch (err: any) {
        console.error('Failed to fetch connector status:', err);
        setErrorMessage(`Failed to load connector status: ${err.message}`);
      }
    };

    initializeStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectorId]);

  // Handle OAuth callback
  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');
    const error = searchParams.get('error');

    if (code && state && !error) {
      setLastConfigured(state);
      setConfigDialogOpen(false);
      setSuccessMessage(`${getConnectorTitle(state)} configured successfully`);
      setSuccess(true);
      
      // Clear success state after 6 seconds
      if (clearSuccessAfterDelayRef.current) {
        clearSuccessAfterDelayRef.current(6000);
      }
      
      // Refresh configurations immediately
      if (checkConnectorConfigurationsRef.current) {
        checkConnectorConfigurationsRef.current();
      }
    } else if (error) {
      setErrorMessage(`OAuth error: ${error}`);
    }
  }, [searchParams]);

  return {
    // State
    isLoading,
    errorMessage,
    success,
    successMessage,
    configDialogOpen,
    currentConnector,
    checkingConfigs,
    lastConfigured,
    connectorStatus,
    configuredStatus,
    
    // Actions
    setConfigDialogOpen,
    setCurrentConnector,
    setSuccess,
    setErrorMessage,
    
    // Methods
    fetchConnectorConfig,
    handleFileRemoved,
    handleToggleConnector,
    checkConnectorConfigurations,
    getCleanRedirectUri,
    getConnectorTitle,
    getConnectorConfig,
    clearSuccessAfterDelay,
  };
};
