import { useEffect, useCallback, useMemo } from 'react';
import { useConnectorContext } from '../context/connector-context';

// Cache duration in milliseconds (5 minutes)
const CACHE_DURATION = 5 * 60 * 1000;

export const useConnectors = () => {
  const { state, dispatch, refreshConnectors: contextRefreshConnectors } = useConnectorContext();

  // Check if data is stale
  const isStale = useMemo(() => {
    if (!state.lastFetched) return true;
    return Date.now() - state.lastFetched > CACHE_DURATION;
  }, [state.lastFetched]);

  // The hook no longer fetches by itself; it delegates to provider-level refresh
  const fetchActiveConnectors = useCallback(async () => {
    await contextRefreshConnectors();
  }, [contextRefreshConnectors]);

  const fetchInactiveConnectors = useCallback(async () => {
    await contextRefreshConnectors();
  }, [contextRefreshConnectors]);

  const fetchAllConnectors = useCallback(async () => {
    await contextRefreshConnectors();
  }, [contextRefreshConnectors]);

  // Refresh connectors (force fetch)
  const refreshConnectors = useCallback(async () => {
    await fetchAllConnectors();
  }, [fetchAllConnectors]);

  // Auto-fetch is handled in the Provider; only optionally refresh if explicitly asked
  useEffect(() => {
    // No-op to avoid multiple consumers triggering parallel fetches
  }, []);

  // Get all connectors combined
  const allConnectors = useMemo(() => 
    [...state.activeConnectors, ...state.inactiveConnectors]
  , [state.activeConnectors, state.inactiveConnectors]);

  // Get connectors by status
  const getConnectorsByStatus = useCallback(
    (isActive: boolean) => (isActive ? state.activeConnectors : state.inactiveConnectors),
    [state.activeConnectors, state.inactiveConnectors]
  );

  // Get connector by name
  const getConnectorByName = useCallback(
    (name: string) => allConnectors.find((connector) => connector.name === name),
    [allConnectors]
  );

  // Check if connector is active
  const isConnectorActive = useCallback(
    (name: string) => {
      const connector = getConnectorByName(name);
      return connector?.isActive || false;
    },
    [getConnectorByName]
  );

  // Check if connector is configured
  const isConnectorConfigured = useCallback(
    (name: string) => {
      const connector = getConnectorByName(name);
      return connector?.isConfigured || false;
    },
    [getConnectorByName]
  );

  return {
    // State
    activeConnectors: state.activeConnectors,
    inactiveConnectors: state.inactiveConnectors,
    allConnectors,
    loading: state.loading,
    error: state.error,
    isStale,

    // Actions
    refreshConnectors,
    fetchActiveConnectors,
    fetchInactiveConnectors,
    fetchAllConnectors,

    // Utilities
    getConnectorsByStatus,
    getConnectorByName,
    isConnectorActive,
    isConnectorConfigured,
  };
};

// Hook for just active connectors
export const useActiveConnectors = () => {
  const { activeConnectors, loading, error, refreshConnectors } = useConnectors();
  return { activeConnectors, loading, error, refreshConnectors };
};

// Hook for just inactive connectors
export const useInactiveConnectors = () => {
  const { inactiveConnectors, loading, error, refreshConnectors } = useConnectors();
  return { inactiveConnectors, loading, error, refreshConnectors };
};
