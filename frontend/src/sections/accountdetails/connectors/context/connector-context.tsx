/**
 * Connector Context
 *
 * Global state management for connector instances.
 * Provides centralized access to connector data across the application.
 */

import React, { createContext, useContext, useReducer, useCallback, useMemo } from 'react';
import { Connector } from '../types/types';
import { ConnectorApiService } from '../services/api';

interface ConnectorState {
  activeConnectors: Connector[];
  inactiveConnectors: Connector[];
  loading: boolean;
  error: string | null;
  lastFetched: number | null;
}

type ConnectorAction =
  | { type: 'FETCH_START' }
  | {
      type: 'FETCH_SUCCESS';
      payload: { active: Connector[]; inactive: Connector[] };
    }
  | { type: 'FETCH_ERROR'; payload: string }
  | { type: 'UPDATE_CONNECTOR'; payload: Connector }
  | { type: 'DELETE_CONNECTOR'; payload: string };

interface ConnectorContextType {
  state: ConnectorState;
  dispatch: React.Dispatch<ConnectorAction>;
  refreshConnectors: () => Promise<void>;
  updateConnector: (connector: Connector) => void;
  deleteConnector: (connectorId: string) => void;
}

const ConnectorContext = createContext<ConnectorContextType | undefined>(undefined);

const initialState: ConnectorState = {
  activeConnectors: [],
  inactiveConnectors: [],
  loading: false,
  error: null,
  lastFetched: null,
};

const connectorReducer = (state: ConnectorState, action: ConnectorAction): ConnectorState => {
  switch (action.type) {
    case 'FETCH_START':
      return { ...state, loading: true, error: null };

    case 'FETCH_SUCCESS':
      return {
        ...state,
        loading: false,
        activeConnectors: action.payload.active,
        inactiveConnectors: action.payload.inactive,
        lastFetched: Date.now(),
        error: null,
      };

    case 'FETCH_ERROR':
      return { ...state, loading: false, error: action.payload };

    case 'UPDATE_CONNECTOR': {
      const updatedConnector = action.payload;
      const updateList = (list: Connector[]) =>
        list.map((c) => (c._key === updatedConnector._key ? updatedConnector : c));

      return {
        ...state,
        activeConnectors: updateList(state.activeConnectors),
        inactiveConnectors: updateList(state.inactiveConnectors),
      };
    }

    case 'DELETE_CONNECTOR': {
      const connectorId = action.payload;
      return {
        ...state,
        activeConnectors: state.activeConnectors.filter((c) => c._key !== connectorId),
        inactiveConnectors: state.inactiveConnectors.filter((c) => c._key !== connectorId),
      };
    }

    default:
      return state;
  }
};

export const ConnectorProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, dispatch] = useReducer(connectorReducer, initialState);

  const refreshConnectors = useCallback(async () => {
    dispatch({ type: 'FETCH_START' });

    try {
      const [active, inactive] = await Promise.all([
        ConnectorApiService.getActiveConnectorInstances(),
        ConnectorApiService.getInactiveConnectorInstances(),
      ]);

      dispatch({ type: 'FETCH_SUCCESS', payload: { active, inactive } });
    } catch (error) {
      console.error('Error fetching connectors:', error);
      dispatch({ type: 'FETCH_ERROR', payload: 'Failed to fetch connectors' });
    }
  }, []);

  const updateConnector = useCallback((connector: Connector) => {
    dispatch({ type: 'UPDATE_CONNECTOR', payload: connector });
  }, []);

  const deleteConnector = useCallback((connectorId: string) => {
    dispatch({ type: 'DELETE_CONNECTOR', payload: connectorId });
  }, []);

  return (
    <ConnectorContext.Provider
      value={useMemo(
        () => ({ state, dispatch, refreshConnectors, updateConnector, deleteConnector }),
        [state, dispatch, refreshConnectors, updateConnector, deleteConnector]
      )}
    >
      {children}
    </ConnectorContext.Provider>
  );
};

export const useConnectorContext = () => {
  const context = useContext(ConnectorContext);
  if (!context) {
    throw new Error('useConnectorContext must be used within ConnectorProvider');
  }
  return context;
};
