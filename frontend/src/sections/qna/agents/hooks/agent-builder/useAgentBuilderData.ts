// src/sections/qna/agents/hooks/useFlowBuilderData.ts
import { useState, useCallback, useEffect, useRef } from 'react';
import type { Agent } from 'src/types/agent';
import type { Connector } from 'src/sections/accountdetails/connectors/types/types';
import { ConnectorApiService } from 'src/sections/accountdetails/connectors/services/api';
import ToolsetApiService from 'src/services/toolset-api';
import type { UseAgentBuilderDataReturn, AgentBuilderError } from '../../types/agent';
import AgentApiService from '../../services/api';

export const useAgentBuilderData = (editingAgent?: Agent | { _key: string } | null): UseAgentBuilderDataReturn => {
  const [availableTools, setAvailableTools] = useState<any[]>([]);
  const [availableModels, setAvailableModels] = useState<any[]>([]);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<any[]>([]);
  const [activeAgentConnectors, setActiveAgentConnectors] = useState<Connector[]>([]);
  const [configuredConnectors, setConfiguredConnectors] = useState<Connector[]>([]);
  const [connectorRegistry, setConnectorRegistry] = useState<any[]>([]);
  const [toolsets, setToolsets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | AgentBuilderError | null>(null);

  // Use refs to prevent duplicate API calls (React StrictMode, re-renders)
  const isLoadingRef = useRef(false);
  const hasLoadedRef = useRef(false);
  const agentKeyRef = useRef<string | undefined>(editingAgent?._key);

  // Load agent details when editing
  const loadAgentDetails = useCallback(async (agentKey: string) => {
    try {
      setError(null);
      const agentDetails = await AgentApiService.getAgent(agentKey);
      setLoadedAgent(agentDetails);
      return agentDetails;
    } catch (err) {
      setError('Failed to load agent details');
      console.error('Error loading agent details:', err);
      return null;
    }
  }, []);

  // Load available resources from APIs - ALL IN ONE CALL
  useEffect(() => {
    // Prevent duplicate calls
    if (isLoadingRef.current || hasLoadedRef.current) {
      return;
    }

    const loadResources = async () => {
      isLoadingRef.current = true;
      
      try {
        setLoading(true);
        setError(null);

        // Load ALL resources in parallel for maximum efficiency
        const [
          modelsResponse, 
          kbResponse, 
          activeAgentConnectorsResponse,
          configuredConnectorsResponse,
          connectorRegistryResponse,
          registryToolsetsResponse,
          configuredToolsetsResponse
        ] = await Promise.all([
          AgentApiService.getAvailableModels(),
          AgentApiService.getKnowledgeBases(),
          ConnectorApiService.getActiveAgentConnectorInstances(1, 100, ''),
          ConnectorApiService.getConfiguredConnectorInstances(undefined, 1, 100, ''),
          ConnectorApiService.getConnectorRegistry(undefined, 1, 100, ''),
          ToolsetApiService.getRegistryToolsets({ includeTools: true, limit: 100 }),
          ToolsetApiService.getConfiguredToolsets(),
        ]);

        setAvailableTools([]); // Deprecated - tools are now from toolsets
        const models = Array.isArray(modelsResponse) ? modelsResponse : [];
        setAvailableModels(models);
        setAvailableKnowledgeBases(kbResponse?.knowledgeBases || []);
        setActiveAgentConnectors(activeAgentConnectorsResponse?.connectors || []);
        const connectorsArray = configuredConnectorsResponse?.connectors;
        setConfiguredConnectors(Array.isArray(connectorsArray) ? connectorsArray : []);
        setConnectorRegistry(connectorRegistryResponse?.connectors || []);
        
        // Merge registry toolsets with configuration status
        const configuredMap = new Map(
          (configuredToolsetsResponse?.toolsets || []).map((t: any) => [
            t.name?.toLowerCase() || '',
            {
              isConfigured: true,
              isAuthenticated: t.isAuthenticated || false,
            },
          ])
        );
        
        const toolsetsWithStatus = (registryToolsetsResponse?.toolsets || []).map((toolset: any) => {
          const status = configuredMap.get(toolset.name.toLowerCase()) as { isConfigured: boolean; isAuthenticated: boolean } | undefined;
          return {
            ...toolset,
            isConfigured: status?.isConfigured ?? false,
            isAuthenticated: status?.isAuthenticated ?? false,
          };
        });
        
        setToolsets(toolsetsWithStatus);
        hasLoadedRef.current = true;
        
        // If editing an agent, load the agent details after basic resources
        if (editingAgent?._key) {
          await loadAgentDetails(editingAgent._key);
        }
      } catch (err) {
        setError('Failed to load resources');
        console.error('Error loading resources:', err);
      } finally {
        setLoading(false);
        isLoadingRef.current = false;
      }
    };

    loadResources();
  }, [editingAgent?._key, loadAgentDetails]);

  // Handle agent key changes (switching between agents)
  useEffect(() => {
    if (editingAgent?._key !== agentKeyRef.current) {
      agentKeyRef.current = editingAgent?._key;
      
      // If switching to a different agent, load the new agent details
      if (editingAgent?._key && hasLoadedRef.current) {
        loadAgentDetails(editingAgent._key);
      }
      
      // Reset loaded agent if switching away
      if (!editingAgent?._key) {
        setLoadedAgent(null);
      }
    }
  }, [editingAgent?._key, loadAgentDetails]);

  return {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    activeAgentConnectors,
    configuredConnectors,
    connectorRegistry,
    toolsets,
    loading,
    loadedAgent,
    error,
    setError,
  };
};
