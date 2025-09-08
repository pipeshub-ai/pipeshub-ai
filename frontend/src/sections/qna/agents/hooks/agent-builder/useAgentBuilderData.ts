// src/sections/qna/agents/hooks/useFlowBuilderData.ts
import { useState, useCallback, useEffect } from 'react';
import type { Agent } from 'src/types/agent';
import AgentApiService from '../../services/api';
import type { UseAgentBuilderDataReturn } from '../../types/agent';

export const useAgentBuilderData = (editingAgent?: Agent | { _key: string } | null): UseAgentBuilderDataReturn => {
  const [availableTools, setAvailableTools] = useState<any[]>([]);
  const [availableModels, setAvailableModels] = useState<any[]>([]);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadedAgent, setLoadedAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  // Load available resources from APIs
  const loadResources = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Load basic resources first
      const [toolsResponse, modelsResponse, kbResponse] = await Promise.all([
        AgentApiService.getAvailableTools(),
        AgentApiService.getAvailableModels(),
        AgentApiService.getKnowledgeBases(),
      ]);

      setAvailableTools(toolsResponse || []);
      const models = Array.isArray(modelsResponse) ? modelsResponse : [];
      setAvailableModels(models);
      setAvailableKnowledgeBases(kbResponse?.knowledgeBases || []);

      // If editing an agent, load the agent details after basic resources
      if (editingAgent?._key) {
        await loadAgentDetails(editingAgent._key);
      }
    } catch (err) {
      setError('Failed to load resources');
      console.error('Error loading resources:', err);
    } finally {
      setLoading(false);
    }
  }, [editingAgent?._key, loadAgentDetails]);

  useEffect(() => {
    loadResources();
  }, [loadResources]);

  // Reset loaded agent when switching between different agents
  useEffect(() => {
    if (editingAgent?._key && loadedAgent && editingAgent._key !== loadedAgent._key) {
      // Only reset if we're switching to a different agent
      setLoadedAgent(null);
    }
  }, [editingAgent?._key, loadedAgent]);

  return {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    loading,
    loadedAgent,
    error,
    setError,
  };
};
