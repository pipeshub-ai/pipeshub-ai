// src/sections/qna/agents/hooks/useFlowBuilderState.ts
import { useState } from 'react';
import type { Agent } from 'src/types/agent';
import type { UseAgentBuilderStateReturn } from '../../types/agent';

export const useAgentBuilderState = (editingAgent?: Agent | { _key: string } | null): UseAgentBuilderStateReturn => {
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [nodeToDelete, setNodeToDelete] = useState<string | null>(null);
  const [edgeDeleteDialogOpen, setEdgeDeleteDialogOpen] = useState(false);
  const [edgeToDelete, setEdgeToDelete] = useState<any>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [agentName, setAgentName] = useState(
    (editingAgent && 'name' in editingAgent) ? editingAgent.name : ''
  );
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  return {
    selectedNode,
    setSelectedNode,
    configDialogOpen,
    setConfigDialogOpen,
    deleteDialogOpen,
    setDeleteDialogOpen,
    nodeToDelete,
    setNodeToDelete,
    edgeDeleteDialogOpen,
    setEdgeDeleteDialogOpen,
    edgeToDelete,
    setEdgeToDelete,
    sidebarOpen,
    setSidebarOpen,
    agentName,
    setAgentName,
    saving,
    setSaving,
    deleting,
    setDeleting,
    success,
    setSuccess,
  };
};
