import React, { createContext, useContext, useReducer, ReactNode, useMemo, useCallback, useRef, useEffect } from 'react';
import { Connector } from '../types/types';
import { ConnectorApiService } from '../services/api';

// State interface
interface ConnectorState {
  activeConnectors: Connector[];
  inactiveConnectors: Connector[];
  loading: boolean;
  error: string | null;
  lastFetched: number | null;
}

// Action types
type ConnectorAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SET_ACTIVE_CONNECTORS'; payload: Connector[] }
  | { type: 'SET_INACTIVE_CONNECTORS'; payload: Connector[] }
  | { type: 'SET_CONNECTORS'; payload: { active: Connector[]; inactive: Connector[] } }
  | { type: 'UPDATE_CONNECTOR'; payload: { name: string; updates: Partial<Connector> } }
  | { type: 'RESET' };

// Initial state
const initialState: ConnectorState = {
  activeConnectors: [],
  inactiveConnectors: [],
  loading: false,
  error: null,
  lastFetched: null,
};

// Reducer
function connectorReducer(state: ConnectorState, action: ConnectorAction): ConnectorState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: action.payload };
    
    case 'SET_ERROR':
      return { ...state, error: action.payload, loading: false };
    
    case 'SET_ACTIVE_CONNECTORS':
      return { 
        ...state, 
        activeConnectors: action.payload, 
        loading: false, 
        error: null,
        lastFetched: Date.now()
      };
    
    case 'SET_INACTIVE_CONNECTORS':
      return { 
        ...state, 
        inactiveConnectors: action.payload, 
        loading: false, 
        error: null,
        lastFetched: Date.now()
      };
    
    case 'SET_CONNECTORS':
      return {
        ...state,
        activeConnectors: action.payload.active,
        inactiveConnectors: action.payload.inactive,
        loading: false,
        error: null,
        lastFetched: Date.now()
      };
    
    case 'UPDATE_CONNECTOR': {
      const { name, updates } = action.payload;
      return {
        ...state,
        activeConnectors: state.activeConnectors.map(connector =>
          connector.name === name ? { ...connector, ...updates } : connector
        ),
        inactiveConnectors: state.inactiveConnectors.map(connector =>
          connector.name === name ? { ...connector, ...updates } : connector
        ),
      };
    }
    
    case 'RESET':
      return initialState;
    
    default:
      return state;
  }
}

// Context interface
interface ConnectorContextType {
  state: ConnectorState;
  dispatch: React.Dispatch<ConnectorAction>;
  refreshConnectors: () => Promise<void>;
  updateConnector: (name: string, updates: Partial<Connector>) => void;
  clearError: () => void;
}

// Create context
const ConnectorContext = createContext<ConnectorContextType | undefined>(undefined);

// Provider component
interface ConnectorProviderProps {
  children: ReactNode;
}

export const ConnectorProvider: React.FC<ConnectorProviderProps> = ({ children }) => {
  const [state, dispatch] = useReducer(connectorReducer, initialState);

  // Prevent concurrent/duplicate fetches
  const isFetchingRef = useRef(false);
  const hasMountedFetchedRef = useRef(false);

  const refreshConnectors = useCallback(async () => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;

    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });

    try {
      const [active, inactive] = await Promise.all([
        ConnectorApiService.getActiveConnectors(),
        ConnectorApiService.getInactiveConnectors(),
      ]);
      dispatch({ type: 'SET_CONNECTORS', payload: { active, inactive } });
    } catch (error) {
      dispatch({ type: 'SET_ERROR', payload: error instanceof Error ? error.message : 'Failed to fetch connectors' });
    } finally {
      isFetchingRef.current = false;
    }
  }, []);

  // Initial fetch once on mount
  useEffect(() => {
    if (hasMountedFetchedRef.current) return;
    hasMountedFetchedRef.current = true;
    refreshConnectors();
  }, [refreshConnectors]);

  const updateConnector = useCallback((name: string, updates: Partial<Connector>) => {
    dispatch({ type: 'UPDATE_CONNECTOR', payload: { name, updates } });
  }, []);

  const clearError = useCallback(() => {
    dispatch({ type: 'SET_ERROR', payload: null });
  }, []);

  const value: ConnectorContextType = useMemo(() => ({
    state,
    dispatch,
    refreshConnectors,
    updateConnector,
    clearError,
  }), [state, refreshConnectors, updateConnector, clearError]);

  return (
    <ConnectorContext.Provider value={value}>
      {children}
    </ConnectorContext.Provider>
  );
};

// Hook to use the context
export const useConnectorContext = (): ConnectorContextType => {
  const context = useContext(ConnectorContext);
  if (context === undefined) {
    throw new Error('useConnectorContext must be used within a ConnectorProvider');
  }
  return context;
};
