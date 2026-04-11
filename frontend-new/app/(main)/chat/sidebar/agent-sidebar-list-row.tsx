'use client';

import React, { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { toast } from '@/lib/store/toast-store';
import { SidebarItem } from './sidebar-item';
import { AgentSidebarItemMenu } from './agent-sidebar-item-menu';
import { DeleteAgentDialog } from './dialogs';

export interface AgentSidebarListRowProps {
  agent: AgentListRecord;
  label: string;
  isActive: boolean;
  /** Opens the agent in chat (row click). */
  onSelect: () => void;
  /** e.g. close mobile drawer before navigating to edit. */
  onBeforeNavigate?: () => void;
  /** Optional left icon (e.g. smart_toy in the full agents panel). */
  icon?: React.ReactNode;
  /** Called after a successful API delete (remove from list, reload, redirect, etc.). */
  onDeleted: (agentId: string) => void;
}

/**
 * One agent row in a chat sidebar with hover, meatball (edit/delete), and typed DELETE confirmation.
 */
export function AgentSidebarListRow({
  agent,
  label,
  isActive,
  onSelect,
  onBeforeNavigate,
  icon,
  onDeleted,
}: AgentSidebarListRowProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const id = agent.id || agent._key;
  const canEdit = Boolean(id && agent.can_edit);
  const canDelete = Boolean(id && agent.can_delete);
  const showMenu = canEdit || canDelete;

  const [rowHovered, setRowHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const goEdit = useCallback(() => {
    if (!id) return;
    onBeforeNavigate?.();
    router.push(`/agents/edit?agentKey=${encodeURIComponent(id)}`);
  }, [id, onBeforeNavigate, router]);

  const handleConfirmDelete = useCallback(async () => {
    if (!id) return;
    setIsDeleting(true);
    try {
      await AgentsApi.deleteAgent(id);
      setDeleteOpen(false);
      onDeleted(id);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : '');
      toast.error(t('chat.failedToDeleteAgent'), {
        description: detail.trim() || undefined,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [id, onDeleted, t]);

  return (
    <>
      <SidebarItem
        icon={icon}
        label={label}
        isActive={isActive}
        onClick={onSelect}
        textColor="var(--slate-12)"
        fontWeight={500}
        forceHighlight={menuOpen}
        onHoverChange={setRowHovered}
        rightSlot={
          showMenu ? (
            <AgentSidebarItemMenu
              isParentHovered={rowHovered}
              onOpenChange={setMenuOpen}
              canEdit={canEdit}
              canDelete={canDelete}
              onEdit={goEdit}
              onDelete={() => setDeleteOpen(true)}
            />
          ) : undefined
        }
      />
      {canDelete && id ? (
        <DeleteAgentDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onConfirm={handleConfirmDelete}
          agentName={label}
          isDeleting={isDeleting}
        />
      ) : null}
    </>
  );
}
