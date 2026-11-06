'use client';

import { useCallback, useState, type ReactElement, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { DropdownMenu } from '@radix-ui/themes';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { IconMenuItem } from '@/app/components/ui/icon-menu-item';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { PermissionLockIcon, useAgentRowShare, useUserPermission } from '@/config';
import type { AgentRowShare } from './agent-row-share';
import { getAgentSidebarRowMenuAccess, type AgentSidebarRowMenuAccess } from './agent-sidebar-row-access';
import { DeleteAgentDialog } from './dialogs';

interface AgentRowActionsOptions {
  agent: AgentListRecord;
  /** The agent's display name, shown in the delete dialog. */
  label: string;
  /** Called after a successful delete. */
  onDeleted: (agentId: string) => void;
}

export interface AgentRowActions {
  access: AgentSidebarRowMenuAccess | null;
  openBuilder: () => void;
  requestDelete: () => void;
  /** Share agent, where the edition shares agents and the person may share this one. */
  share: AgentRowShare | null;
  /**
   * The delete confirmation and the share panel. Render it outside any clickable row: its clicks and keys would
   * otherwise reach the row through the React tree.
   */
  dialog: ReactNode;
}

/** What an agent row's ⋯ menu does: open the builder, share where the edition can, and delete after a typed confirmation. */
export function useAgentRowActions({ agent, label, onDeleted }: AgentRowActionsOptions): AgentRowActions {
  const router = useRouter();
  const { t } = useTranslation();
  const access = getAgentSidebarRowMenuAccess(agent);
  const id = access?.agentKey;
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const share = useAgentRowShare({ agentKey: id, name: label, canShare: access?.canShare ?? false });

  const openBuilder = useCallback(() => {
    if (!id) return;
    router.push(`/agents/edit?agentKey=${encodeURIComponent(id)}`);
  }, [id, router]);

  const handleConfirmDelete = useCallback(async () => {
    if (!id) return;
    setIsDeleting(true);
    try {
      await AgentsApi.deleteAgent(id);
      setDeleteOpen(false);
      onDeleted(id);
    } catch (e: unknown) {
      toast.error(t('chat.failedToDeleteAgent'), {
        description: getUserFacingErrorMessage(e, '') || undefined,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [id, onDeleted, t]);

  const dialog = (
    <>
      {access?.canDelete && id ? (
        <DeleteAgentDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onConfirm={handleConfirmDelete}
          agentName={label}
          isDeleting={isDeleting}
        />
      ) : null}
      {share?.panel}
    </>
  );

  return { access, openBuilder, requestDelete: () => setDeleteOpen(true), share, dialog };
}

interface AgentRowMenuProps {
  actions: AgentRowActions;
  /** The ⋯ control. It must be one element that takes a ref (a button). */
  trigger: ReactElement;
  modal?: boolean;
}

/**
 * The ⋯ menu of an agent row: View or Edit agent, Share agent where the edition shares agents, then Delete agent.
 * Edit, Share and Delete are locked when the person's role does not allow them. Renders nothing when it would
 * hold no item.
 */
export function AgentRowMenu({ actions, trigger, modal = false }: AgentRowMenuProps) {
  const { t } = useTranslation();
  const { access, openBuilder, requestDelete, share } = actions;
  const canEditAgent = useUserPermission('editAgent');
  const canDeleteAgent = useUserPermission('deleteAgent');
  const showViewAgent = access?.showViewAgent ?? false;
  const canEdit = access?.canEdit ?? false;
  const canDelete = access?.canDelete ?? false;
  const editPermissionDenied = canEdit && !canEditAgent;
  const deletePermissionDenied = canDelete && !canDeleteAgent;

  if (!showViewAgent && !canEdit && !share && !canDelete) return null;

  return (
    <DropdownMenu.Root modal={modal}>
      <DropdownMenu.Trigger>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Content side="bottom" align="start" sideOffset={4} style={{ minWidth: 140 }}>
        {showViewAgent && (
          <IconMenuItem
            icon="visibility"
            onClick={(e) => {
              e.stopPropagation();
              openBuilder();
            }}
          >
            {t('chat.viewAgent')}
          </IconMenuItem>
        )}
        {canEdit && (
          <IconMenuItem
            icon="edit"
            textValue={t('chat.editAgent')}
            disabled={editPermissionDenied}
            onClick={(e) => {
              e.stopPropagation();
              if (editPermissionDenied) return;
              openBuilder();
            }}
          >
            {t('chat.editAgent')}
            {editPermissionDenied && <PermissionLockIcon />}
          </IconMenuItem>
        )}
        {share?.menuItem}
        {canDelete && (
          <IconMenuItem
            icon="delete"
            textValue={t('chat.deleteAgent')}
            color={deletePermissionDenied ? undefined : 'red'}
            disabled={deletePermissionDenied}
            onClick={(e) => {
              e.stopPropagation();
              if (deletePermissionDenied) return;
              requestDelete();
            }}
          >
            {t('chat.deleteAgent')}
            {deletePermissionDenied && <PermissionLockIcon />}
          </IconMenuItem>
        )}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
