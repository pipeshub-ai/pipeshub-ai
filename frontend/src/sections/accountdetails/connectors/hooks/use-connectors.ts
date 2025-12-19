/**
 * useConnectors Hook
 *
 * Hook for managing connector instances with caching.
 * Provides access to all connector instances with filtering and utilities.
 */

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

  // Fetch all connector instances via Provider method
  const fetchAllConnectors = useCallback(async () => {
    await contextRefreshConnectors();
  }, [contextRefreshConnectors]);

  // Refresh connectors (force fetch)
  const refreshConnectors = useCallback(async () => {
    await contextRefreshConnectors();
  }, [contextRefreshConnectors]);

  // Auto-fetch on mount and when data is stale
  useEffect(() => {
    if (state.activeConnectors.length === 0 && state.inactiveConnectors.length === 0) {
      // Initial load
      fetchAllConnectors();
    } else if (isStale) {
      // Refresh stale data
      fetchAllConnectors();
    }
  }, [fetchAllConnectors, isStale, state.activeConnectors.length, state.inactiveConnectors.length]);

  // Get all connectors combined
  const allConnectors = useMemo(
    () => [...state.activeConnectors, ...state.inactiveConnectors],
    [state.activeConnectors, state.inactiveConnectors]
  );

  // Get connectors by status
  const getConnectorsByStatus = useCallback(
    (isActive: boolean) => (isActive ? state.activeConnectors : state.inactiveConnectors),
    [state.activeConnectors, state.inactiveConnectors]
  );

  // Get connector by connectorId
  const getConnectorByKey = useCallback(
    (connectorId: string) => allConnectors.find((connector) => connector._key === connectorId),
    [allConnectors]
  );

  // Get connector by name (for backward compatibility)
  const getConnectorByName = useCallback(
    (name: string) => allConnectors.find((connector) => connector.name === name),
    [allConnectors]
  );

  // Get connector by type
  const getConnectorByType = useCallback(
    (type: string) => allConnectors.find((connector) => connector.type === type),
    [allConnectors]
  );

  // Check if connector is active by key
  const isConnectorActive = useCallback(
    (connectorId: string) => {
      const connector = getConnectorByKey(connectorId);
      return connector?.isActive || false;
    },
    [getConnectorByKey]
  );

  // Check if connector is configured by key
  const isConnectorConfigured = useCallback(
    (connectorId: string) => {
      const connector = getConnectorByKey(connectorId);
      return connector?.isConfigured || false;
    },
    [getConnectorByKey]
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
    fetchAllConnectors,

    // Utilities
    getConnectorsByStatus,
    getConnectorByKey,
    getConnectorByName,
    getConnectorByType,
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
