'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Badge, Box, DropdownMenu, Flex, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { formatFileSize } from '@/app/components/file-preview/utils';
import { ShareSidebar } from '@/app/components/share';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import { createProjectShareAdapter } from '@/chat/share-adapter';
import { useChatStore } from '@/chat/store';
import { DeleteProjectDialog } from '@/chat/sidebar/dialogs';

const MAX_RECENT_CHATS_SHOWN = 5;

interface ProjectWorkspaceProps {
  projectId: string;
}

/**
 * Landing view rendered at `/chat?projectId=…` when no conversation is
 * selected — project header + actions, editable instructions, files, and
 * recent chats. Sits above the composer (see `page.tsx`); sending a message
 * from there starts a new project-scoped conversation.
 */
export function ProjectWorkspace({ projectId }: ProjectWorkspaceProps) {
  const router = useRouter();
  const { t } = useTranslation();
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
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const canEdit = project?.role === 'owner' || project?.role === 'editor';
  const isOwner = project?.role === 'owner';
  const shareAdapter = useMemo(
    () => (project && isOwner ? createProjectShareAdapter(project) : null),
    [project, isOwner],
  );

  const handleSaveInstructions = async () => {
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
      // Keep the draft + editing state open so the user can retry.
    } finally {
      setIsSavingInstructions(false);
    }
  };

  const handleTogglePin = async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const updated = project.isPinned
        ? await ProjectApi.unpin(projectId)
        : await ProjectApi.pin(projectId);
      setProject(updated);
      upsertProjectInList({ ...updated, conversationCount: conversationTotal });
    } catch {
      // Non-fatal — UI stays on the current pin state; user can retry.
    } finally {
      setIsMutating(false);
    }
  };

  const handleToggleArchive = async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const updated = project.isArchived
        ? await ProjectApi.unarchive(projectId)
        : await ProjectApi.archive(projectId);
      setProject(updated);
      bumpProjectsVersion();
    } catch {
      // Non-fatal — UI stays on the current archive state; user can retry.
    } finally {
      setIsMutating(false);
    }
  };

  const handleConfirmDelete = async () => {
    setIsDeleting(true);
    try {
      await ProjectApi.remove(projectId);
      removeProjectFromList(projectId);
      setDeleteDialogOpen(false);
      router.push('/chat/');
    } catch {
      // Dialog stays open with isDeleting reset — user can retry or dismiss.
    } finally {
      setIsDeleting(false);
    }
  };

  const handleFilesSelected = async (files: FileList | null) => {
    if (!files || files.length === 0 || !project) return;
    setIsUploading(true);
    try {
      await ProjectApi.uploadFiles(projectId, Array.from(files));
      const refreshed = await ProjectApi.get(projectId);
      setProject(refreshed);
    } catch {
      // Non-fatal — file list simply won't reflect the failed upload.
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleShareSuccess = useCallback(async () => {
    try {
      const refreshed = await ProjectApi.get(projectId);
      setProject(refreshed);
    } catch {
      // Non-fatal — stale project state until the next full reload.
    }
  }, [projectId]);

  const handleRemoveFile = async (recordId: string) => {
    if (!project) return;
    try {
      const files = await ProjectApi.removeFile(projectId, recordId);
      setProject({ ...project, files });
    } catch {
      // Non-fatal — leave the file listed; user can retry.
    }
  };

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ width: '100%', padding: 'var(--space-6) 0' }}>
        <LottieLoader autoplay loop style={{ width: 48, height: 48 }} />
      </Flex>
    );
  }

  if (loadError || !project) {
    return (
      <Text size="2" style={{ color: '#ef4444', padding: 'var(--space-4) 0' }}>
        {t('chat.projects.workspace.failedToLoad')}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="5" style={{ width: '100%', paddingBottom: 'var(--space-4)' }}>
      {/* Header */}
      <Flex align="start" justify="between" gap="3">
        <Flex align="center" gap="3" style={{ minWidth: 0 }}>
          <Box
            style={{
              width: 40,
              height: 40,
              borderRadius: 'var(--radius-3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--accent-3)',
              border: '1px solid var(--accent-6)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="folder" size={22} color={project.color || 'var(--accent-11)'} />
          </Box>
          <Flex direction="column" style={{ minWidth: 0 }}>
            <Text
              size="5"
              weight="bold"
              style={{
                color: 'var(--slate-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
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

      {/* Instructions */}
      <Flex direction="column" gap="2">
        <Flex align="center" justify="between">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('chat.projects.workspace.instructionsTitle')}
          </Text>
          {canEdit && !isEditingInstructions && (
            <LoadingButton
              size="1"
              variant="ghost"
              color="gray"
              onClick={() => setIsEditingInstructions(true)}
            >
              {t('chat.projects.workspace.editInstructions')}
            </LoadingButton>
          )}
        </Flex>
        {isEditingInstructions ? (
          <Flex direction="column" gap="2">
            <TextArea
              value={instructionsDraft}
              onChange={(e) => setInstructionsDraft(e.target.value)}
              placeholder={t('chat.projects.workspace.instructionsPlaceholder')}
              rows={4}
              maxLength={8000}
              autoFocus
            />
            <Flex gap="2" justify="end">
              <LoadingButton
                size="1"
                variant="soft"
                color="gray"
                onClick={() => {
                  setInstructionsDraft(project.instructions ?? '');
                  setIsEditingInstructions(false);
                }}
                disabled={isSavingInstructions}
              >
                {t('action.cancel')}
              </LoadingButton>
              <LoadingButton
                size="1"
                color="jade"
                onClick={() => void handleSaveInstructions()}
                loading={isSavingInstructions}
                loadingLabel={t('chat.projects.workspace.saving')}
              >
                {t('chat.projects.workspace.save')}
              </LoadingButton>
            </Flex>
          </Flex>
        ) : (
          <Box
            style={{
              padding: 'var(--space-3)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--olive-2)',
              border: '1px solid var(--olive-4)',
              minHeight: 40,
            }}
          >
            <Text
              size="2"
              style={{
                color: project.instructions ? 'var(--slate-12)' : 'var(--slate-10)',
                whiteSpace: 'pre-wrap',
              }}
            >
              {project.instructions?.trim() || t('chat.projects.workspace.noInstructions')}
            </Text>
          </Box>
        )}
      </Flex>

      {/* Files */}
      <Flex direction="column" gap="2">
        <Flex align="center" justify="between">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('chat.projects.workspace.filesTitle')}
          </Text>
          {canEdit && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => void handleFilesSelected(e.target.files)}
              />
              <LoadingButton
                size="1"
                variant="ghost"
                color="gray"
                onClick={() => fileInputRef.current?.click()}
                loading={isUploading}
                loadingLabel={t('chat.projects.workspace.uploading')}
              >
                {t('chat.projects.workspace.uploadFiles')}
              </LoadingButton>
            </>
          )}
        </Flex>
        {project.files.length === 0 ? (
          <Text size="2" style={{ color: 'var(--slate-10)' }}>
            {t('chat.projects.workspace.noFiles')}
          </Text>
        ) : (
          <Flex direction="column" gap="1">
            {project.files.map((file) => (
              <Flex
                key={file.recordId}
                align="center"
                justify="between"
                style={{
                  padding: 'var(--space-2) var(--space-3)',
                  borderRadius: 'var(--radius-2)',
                  background: 'var(--olive-2)',
                }}
              >
                <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                  <MaterialIcon name="description" size={16} color="var(--slate-11)" />
                  <Text
                    size="2"
                    style={{
                      color: 'var(--slate-12)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {file.recordName || file.recordId}
                  </Text>
                  {typeof file.sizeBytes === 'number' && (
                    <Text size="1" style={{ color: 'var(--slate-10)', flexShrink: 0 }}>
                      {formatFileSize(file.sizeBytes)}
                    </Text>
                  )}
                </Flex>
                {canEdit && (
                  <button
                    type="button"
                    aria-label={t('chat.projects.workspace.removeFile')}
                    onClick={() => void handleRemoveFile(file.recordId)}
                    style={{
                      appearance: 'none',
                      border: 'none',
                      background: 'transparent',
                      cursor: 'pointer',
                      display: 'flex',
                    }}
                  >
                    <MaterialIcon name="close" size={16} color="var(--slate-10)" />
                  </button>
                )}
              </Flex>
            ))}
          </Flex>
        )}
      </Flex>

      {/* Members */}
      <Flex align="center" justify="between">
        <Flex align="center" gap="2">
          <MaterialIcon name="group" size={16} color="var(--slate-11)" />
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('chat.projects.workspace.membersTitle')}:{' '}
            {t(
              project.members.length === 1
                ? 'chat.projects.workspace.memberCount_one'
                : 'chat.projects.workspace.memberCount_other',
              { count: project.members.length + 1 },
            )}
          </Text>
        </Flex>
        {isOwner && (
          <LoadingButton size="1" variant="ghost" color="gray" onClick={() => setShareOpen(true)}>
            {t('chat.projects.workspace.share')}
          </LoadingButton>
        )}
      </Flex>

      {shareAdapter && (
        <ShareSidebar
          open={shareOpen}
          onOpenChange={setShareOpen}
          adapter={shareAdapter}
          onShareSuccess={() => void handleShareSuccess()}
        />
      )}

      {/* Recent chats */}
      {recentChats.length > 0 && (
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('chat.projects.workspace.recentChatsTitle')}
          </Text>
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
                      : `/chat/?projectId=${encodeURIComponent(projectId)}&conversationId=${encodeURIComponent(c._id)}`,
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
