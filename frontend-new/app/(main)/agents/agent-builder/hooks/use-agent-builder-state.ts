import { useState } from 'react';

export function useAgentBuilderState(initialName = '') {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [nodeToDelete, setNodeToDelete] = useState<string | null>(null);
  const [edgeDeleteDialogOpen, setEdgeDeleteDialogOpen] = useState(false);
  const [edgeToDelete, setEdgeToDelete] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [agentName, setAgentName] = useState(initialName);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  return {
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
}
