import { useEffect, useState } from 'react';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export function useAgentBuilderState(initialName = '') {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [nodeToDelete, setNodeToDelete] = useState<string | null>(null);
  const [edgeDeleteDialogOpen, setEdgeDeleteDialogOpen] = useState(false);
  const [edgeToDelete, setEdgeToDelete] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const isMobile = useIsMobile();
  // The palette would cover the canvas at phone width, so it starts closed there.
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);
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
    isMobile,
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
