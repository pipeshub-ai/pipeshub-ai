'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Badge, Box, DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ShareSidebar } from '@/app/components/share';
import { ChatInput } from '@/chat/components/chat-input';
import { ChatApi } from '@/chat/api';
import type { AttachmentRef } from '@/chat/types';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import { createProjectShareAdapter } from '@/chat/share-adapter';
import { useChatStore, ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { buildChatHref } from '@/chat/build-chat-url';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { toast } from '@/lib/store/toast-store';
import { DeleteProjectDialog } from '@/chat/sidebar/dialogs';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ProjectSettingsPanel } from './settings-panel';

const MAX_RECENT_CHATS_SHOWN = 5;

interface ProjectWorkspaceRedesignedProps {
  projectId: string;
}

/**
 * `/projects?projectId=…` — Claude-style two-column workspace. Left column
 * has the project header, composer (starts a new project-scoped chat via the
 * pending-chat hand-off to `/chat`), and recent chats. Right column has
 * collapsible Instructions/Files/Members cards.
 */
export function ProjectWorkspaceRedesigned({ projectId }: ProjectWorkspaceRedesignedProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  const bumpProjectsVersion = useChatStore((s) => s.bumpProjectsVersion);
  const removeProjectFromList = useChatStore((s) => s.removeProjectFromList);
  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const [recentChats, setRecentChats] = useState<
    Array<{ _id: string; title?: string; sessionType: 'chat' | 'agent'; agentKey?: string }>
  >([]);
  const [conversationTotal, setConversationTotal] = useState(0);

  const [instructionsDraft, setInstructionsDraft] = useState('');
  const [isEditingInstructions, setIsEditingInstructions] = useState(false);
  const [isSavingInstructions, setIsSavingInstructions] = useState(false);

  const [isUploading, setIsUploading] = useState(false);

  const [isMutating, setIsMutating] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setLoadError(false);
    try {
      const [detail, conv] = await Promise.all([
        ProjectApi.get(projectId),
        ProjectApi.listConversations(projectId, { page: 1, limit: MAX_RECENT_CHATS_SHOWN }),
      ]);
      setProject(detail);
      setInstructionsDraft(detail.instructions ?? '');
      setRecentChats(conv.conversations);
      setConversationTotal(conv.pagination.totalCount);
    } catch {
      setLoadError(true);
      setProject(null);
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // Preload org models so the composer's model pill isn't empty on a fresh
  // session that never visited /chat first.
  useEffect(() => {
    fetchModelsForContext(ASSISTANT_CTX).catch(() => {});
  }, []);

  const canEdit = project?.role === 'owner' || project?.role === 'editor';
  const isOwner = project?.role === 'owner';
  const shareAdapter = useMemo(
    () => (project && isOwner ? createProjectShareAdapter(project) : null),
    [project, isOwner],
  );

  const handleSaveInstructions = useCallback(async () => {
    if (!project) return;
    setIsSavingInstructions(true);
    try {
      const updated = await ProjectApi.update(projectId, {
        instructions: instructionsDraft.trim(),
      });
      setProject(updated);
      setInstructionsDraft(updated.instructions ?? '');
      setIsEditingInstructions(false);
    } catch {
      toast.error(t('chat.projects.workspace.failedToLoad'));
    } finally {
      setIsSavingInstructions(false);
    }
  }, [project, projectId, instructionsDraft, t]);

  const handleTogglePin = useCallback(async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const updated = project.isPinned
        ? await ProjectApi.unpin(projectId)
        : await ProjectApi.pin(projectId);
      setProject(updated);
      upsertProjectInList({ ...updated, conversationCount: conversationTotal });
    } catch {
      toast.error(t('chat.projects.failedToLoad'));
    } finally {
      setIsMutating(false);
    }
  }, [project, projectId, isMutating, conversationTotal, upsertProjectInList, t]);

  const handleToggleArchive = useCallback(async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const updated = project.isArchived
        ? await ProjectApi.unarchive(projectId)
        : await ProjectApi.archive(projectId);
      setProject(updated);
      bumpProjectsVersion();
    } catch {
      toast.error(t('chat.projects.failedToLoad'));
    } finally {
      setIsMutating(false);
    }
  }, [project, projectId, isMutating, bumpProjectsVersion, t]);

  const handleConfirmDelete = useCallback(async () => {
    setIsDeleting(true);
    try {
      await ProjectApi.remove(projectId);
      removeProjectFromList(projectId);
      setDeleteDialogOpen(false);
      router.push('/projects/');
    } catch {
      toast.error(t('chat.projects.deleteDialog.title'));
    } finally {
      setIsDeleting(false);
    }
  }, [projectId, removeProjectFromList, router, t]);

  const handleUploadFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || !project) return;
      setIsUploading(true);
      try {
        await ProjectApi.uploadFiles(projectId, Array.from(files));
        const refreshed = await ProjectApi.get(projectId);
        setProject(refreshed);
      } catch {
        toast.error(t('chat.projects.workspace.uploadFiles'));
      } finally {
        setIsUploading(false);
      }
    },
    [project, projectId, t],
  );

  const handleRemoveFile = useCallback(
    async (recordId: string) => {
      if (!project) return;
      try {
        const files = await ProjectApi.removeFile(projectId, recordId);
        setProject({ ...project, files });
      } catch {
        toast.error(t('chat.projects.workspace.removeFile'));
      }
    },
    [project, projectId, t],
  );

  const handleShareSuccess = useCallback(async () => {
    try {
      const refreshed = await ProjectApi.get(projectId);
      setProject(refreshed);
    } catch {
      toast.error(t('chat.projects.workspace.share'));
    }
  }, [projectId, t]);

  // ── Composer: hands the message off to /chat via the pending-chat buffer ──
  const handleSend = useCallback(
    (message: string, attachments?: AttachmentRef[]) => {
      if (!message.trim() && (!attachments || attachments.length === 0)) return;
      usePendingChatStore.getState().setPending({
        message,
        attachments,
        pageContext: {},
        referrerPage: pathname,
      });
      router.push(buildChatHref({ projectId }));
    },
    [projectId, pathname, router],
  );

  const handleUploadFile = useCallback(async (file: File, signal: AbortSignal): Promise<AttachmentRef> => {
    const refs = await ChatApi.uploadAttachments([file], { conversationId: null, signal });
    const ref = refs[0];
    if (!ref) throw new Error('Upload returned no attachment ref');
    return ref;
  }, []);

  const handleDeleteFile = useCallback((recordId: string) => {
    ChatApi.deleteAttachment(recordId, {}).catch(() => {});
  }, []);

  const openMostRecentChat = useCallback(() => {
    if (recentChats.length === 0) return;
    const c = recentChats[0];
    const href =
      c.sessionType === 'agent' && c.agentKey
        ? `/chat/?agentId=${encodeURIComponent(c.agentKey)}&conversationId=${encodeURIComponent(c._id)}`
        : buildChatHref({ projectId, conversationId: c._id });
    router.push(href);
  }, [recentChats, projectId, router]);

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ width: '100%', padding: 'var(--space-8) 0' }}>
        <LottieLoader autoplay loop style={{ width: 48, height: 48 }} />
      </Flex>
    );
  }

  if (loadError || !project) {
    return (
      <Flex direction="column" align="center" style={{ width: '100%', padding: 'var(--space-6) 0' }}>
        <Text size="2" style={{ color: '#ef4444' }}>
          {t('chat.projects.workspace.failedToLoad')}
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" style={{ width: '100%', height: '100%' }}>
      {/* Header */}
      <Flex
        align="start"
        justify="between"
        gap="3"
        style={{
          padding: isMobile ? 'var(--space-4)' : 'var(--space-4) var(--space-6)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
        }}
      >
        <Flex align="center" gap="3" style={{ minWidth: 0 }}>
          <button
            type="button"
            aria-label={t('projects.backToAllProjects')}
            onClick={() => router.push('/projects/')}
            style={{
              appearance: 'none',
              border: 'none',
              background: 'transparent',
              padding: 0,
              cursor: 'pointer',
              display: 'flex',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="chevron_left" size={20} color="var(--slate-11)" />
          </button>
          <Box
            style={{
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--accent-3)',
              border: '1px solid var(--accent-6)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="folder" size={20} color={project.color || 'var(--accent-11)'} />
          </Box>
          <Flex direction="column" style={{ minWidth: 0 }}>
            <Text
              size="5"
              weight="bold"
              style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {project.name}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-10)' }}>
              {project.description?.trim() || t('chat.projects.workspace.noDescription')}
            </Text>
          </Flex>
        </Flex>

        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <Badge color="gray" variant="soft">
            {t(`chat.projects.roles.${project.role === 'none' ? 'viewer' : project.role}`)}
          </Badge>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <button
                type="button"
                aria-label="Project actions"
                style={{
                  appearance: 'none',
                  border: 'none',
                  background: 'transparent',
                  borderRadius: 'var(--radius-1)',
                  padding: 4,
                  cursor: 'pointer',
                  display: 'flex',
                }}
              >
                <MaterialIcon name="more_horiz" size={20} color="var(--slate-11)" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content align="end">
              {isOwner && (
                <DropdownMenu.Item onClick={() => setShareOpen(true)}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="share" size={16} />
                    <Text size="2">{t('chat.projects.workspace.share')}</Text>
                  </Flex>
                </DropdownMenu.Item>
              )}
              <DropdownMenu.Item onClick={() => void handleTogglePin()} disabled={isMutating}>
                <Flex align="center" gap="2">
                  <MaterialIcon name={project.isPinned ? 'star' : 'star_outline'} size={16} />
                  <Text size="2">
                    {project.isPinned ? t('chat.projects.unpinProject') : t('chat.projects.pinProject')}
                  </Text>
                </Flex>
              </DropdownMenu.Item>
              {isOwner && (
                <DropdownMenu.Item onClick={() => void handleToggleArchive()} disabled={isMutating}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="archive" size={16} />
                    <Text size="2">
                      {project.isArchived
                        ? t('chat.projects.unarchiveProject')
                        : t('chat.projects.archiveProject')}
                    </Text>
                  </Flex>
                </DropdownMenu.Item>
              )}
              {isOwner && (
                <DropdownMenu.Item color="red" onClick={() => setDeleteDialogOpen(true)}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="delete" size={16} color="var(--red-11)" />
                    <Text size="2" style={{ color: 'var(--red-11)' }}>
                      {t('chat.projects.deleteProject')}
                    </Text>
                  </Flex>
                </DropdownMenu.Item>
              )}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        </Flex>
      </Flex>

      {/* Two-column body */}
      <Flex
        direction={isMobile ? 'column' : 'row'}
        gap="6"
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          padding: isMobile ? 'var(--space-4)' : 'var(--space-6)',
        }}
      >
        {/* Left column — composer + recent chats */}
        <Flex direction="column" gap="4" style={{ flex: '1 1 60%', minWidth: 0 }}>
          <ChatInput variant="full" onSend={handleSend} onUploadFile={handleUploadFile} onDeleteFile={handleDeleteFile} />

          {recentChats.length > 0 ? (
            <Flex direction="column" gap="2">
              <Flex align="center" justify="between">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {t('chat.projects.workspace.recentChatsTitle')}
                </Text>
                <button
                  type="button"
                  onClick={openMostRecentChat}
                  style={{
                    appearance: 'none',
                    border: 'none',
                    background: 'transparent',
                    color: 'var(--accent-11)',
                    fontSize: 13,
                    cursor: 'pointer',
                    padding: 0,
                  }}
                >
                  {t('chat.projects.workspace.seeAllChats')}
                </button>
              </Flex>
              <Flex direction="column" gap="1">
                {recentChats.map((c) => (
                  <Flex
                    key={c._id}
                    align="center"
                    gap="2"
                    style={{
                      padding: 'var(--space-2) var(--space-3)',
                      borderRadius: 'var(--radius-2)',
                      cursor: 'pointer',
                    }}
                    onClick={() =>
                      router.push(
                        c.sessionType === 'agent' && c.agentKey
                          ? `/chat/?agentId=${encodeURIComponent(c.agentKey)}&conversationId=${encodeURIComponent(c._id)}`
                          : buildChatHref({ projectId, conversationId: c._id }),
                      )
                    }
                  >
                    <MaterialIcon name="chat_bubble_outline" size={16} color="var(--slate-11)" />
                    <Text
                      size="2"
                      style={{
                        color: 'var(--slate-12)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {c.title || t('chat.generatingTitle')}
                    </Text>
                  </Flex>
                ))}
              </Flex>
            </Flex>
          ) : (
            <Text size="2" style={{ color: 'var(--slate-10)', textAlign: 'center', marginTop: 'var(--space-4)' }}>
              {t('chat.projects.startChatInProject')}
            </Text>
          )}
        </Flex>

        {/* Right column — settings panel */}
        <Box style={{ flex: isMobile ? '1 1 auto' : '0 0 300px', width: isMobile ? '100%' : 300 }}>
          <ProjectSettingsPanel
            project={project}
            canEdit={canEdit}
            isOwner={isOwner}
            instructionsDraft={instructionsDraft}
            isEditingInstructions={isEditingInstructions}
            isSavingInstructions={isSavingInstructions}
            onInstructionsDraftChange={setInstructionsDraft}
            onStartEditInstructions={() => setIsEditingInstructions(true)}
            onCancelEditInstructions={() => {
              setInstructionsDraft(project.instructions ?? '');
              setIsEditingInstructions(false);
            }}
            onSaveInstructions={() => void handleSaveInstructions()}
            isUploading={isUploading}
            onUploadFiles={(files) => void handleUploadFiles(files)}
            onRemoveFile={(recordId) => void handleRemoveFile(recordId)}
            onOpenShare={() => setShareOpen(true)}
          />
        </Box>
      </Flex>

      {shareAdapter && (
        <ShareSidebar
          open={shareOpen}
          onOpenChange={setShareOpen}
          adapter={shareAdapter}
          onShareSuccess={() => void handleShareSuccess()}
        />
      )}

      <DeleteProjectDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        isDeleting={isDeleting}
      />
    </Flex>
  );
}
