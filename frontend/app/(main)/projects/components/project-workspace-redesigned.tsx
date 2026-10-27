'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Box, Button, Callout, DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { ShareSidebar } from '@/app/components/share';
import { ChatInput } from '@/chat/components/chat-input';
import { ChatApi } from '@/chat/api';
import type { AttachmentRef, AppliedFilters } from '@/chat/types';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail, ProjectKnowledgeScope } from '@/chat/project-types';
import { createProjectShareAdapter } from '@/chat/share-adapter';
import { useChatStore, ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { buildChatHref } from '@/chat/build-chat-url';
import { chatContentColumnStyle } from '@/chat/constants';
import { useProjectScopeHydration } from '@/chat/hooks/use-project-scope-hydration';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { toast } from '@/lib/store/toast-store';
import { DeleteProjectDialog, EditProjectDialog, LeaveProjectDialog } from '@/chat/sidebar/dialogs';
import { useIsMobile, useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { touchTarget } from '@/app/components/ui/responsive';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar/constants';
import { useUserStore } from '@/lib/store/user-store';
import { Link } from '@/lib/navigation';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { ProjectSettingsPanel, type SetupSectionKey } from './settings-panel';
import { ChatDefaultsCard } from './chat-defaults-card';
import { AboutCard } from './about-card';
import { ProjectSharingSettings } from './project-sharing-settings';
import { createSerialSaver } from '../utils/serial-saver';
import { hasDirectMembership } from '../utils/membership';

interface ProjectWorkspaceRedesignedProps {
  projectId: string;
}

type SuggestionKey = 'summarise' | 'plan' | 'instructions';

function SuggestionPill({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <Button
      variant="soft"
      color="gray"
      size="2"
      onClick={onClick}
      style={{ border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-2)', background: 'var(--olive-2)' }}
    >
      <Flex align="center" gap="2">
        <MaterialIcon name={icon} size={14} color="var(--slate-10)" />
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {label}
        </Text>
      </Flex>
    </Button>
  );
}

/**
 * `/projects?projectId=…` — Claude-style two-column workspace. Left column
 * has a composer hero (`chatContentColumnStyle`, matching `/chat`'s new-chat
 * hero) that hands off to `/chat` via the pending-chat buffer. The full
 * conversation list lives in the left app sidebar
 * (`ProjectConversationsSidebar`). Right column has the "Project setup"
 * accordion (Instructions/Files/Connectors/Tools & MCP, plus Members which
 * opens the share drawer), a "Chat defaults" preview when the project has
 * no conversations yet, and an "About" card.
 */
export function ProjectWorkspaceRedesigned({ projectId }: ProjectWorkspaceRedesignedProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const stacked = useIsBelowDesktop();
  const canHover = useCanHover();
  const profile = useUserStore((s) => s.profile);

  const bumpProjectsVersion = useChatStore((s) => s.bumpProjectsVersion);
  const removeProjectFromList = useChatStore((s) => s.removeProjectFromList);
  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);

  const [project, setProjectRaw] = useState<ProjectDetail | null>(null);

  /** Setter that preserves the computed `role` when the API response omits it. */
  const setProject = useCallback(
    (next: ProjectDetail | null | ((prev: ProjectDetail | null) => ProjectDetail | null)) => {
      setProjectRaw((prev) => {
        const val = typeof next === 'function' ? next(prev) : next;
        if (val && !val.role && prev?.role) {
          return { ...val, role: prev.role };
        }
        return val;
      });
    },
    [],
  );
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [conversationTotal, setConversationTotal] = useState(0);

  const [instructionsDraft, setInstructionsDraft] = useState('');
  const [isEditingInstructions, setIsEditingInstructions] = useState(false);
  const [isSavingInstructions, setIsSavingInstructions] = useState(false);
  const [expandedSetupSection, setExpandedSetupSection] = useState<SetupSectionKey | null>(null);

  const [composerPrefill, setComposerPrefill] = useState<{ text: string; key: number } | null>(null);
  const prefillCounterRef = useRef(0);

  const [isMutating, setIsMutating] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [isLeaving, setIsLeaving] = useState(false);

  // The tool list the user last asked for. Tool toggles can outrun the
  // server, so every PATCH response is merged under this instead of
  // replacing it (see `toolsSaver`).
  const toolsRef = useRef<string[]>([]);
  const confirmedToolsRef = useRef<string[]>([]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setLoadError(false);
    try {
      // Conversation rows render in the left sidebar
      // (`ProjectConversationsSidebar`); only the total is needed here, to keep
      // the projects-list "N chats" badge correct after pin/unpin.
      const [detail, conv] = await Promise.all([
        ProjectApi.get(projectId),
        ProjectApi.listConversations(projectId, { page: 1, limit: 1 }),
      ]);
      toolsRef.current = detail.tools ?? [];
      confirmedToolsRef.current = detail.tools ?? [];
      setProject(detail);
      setInstructionsDraft(detail.instructions ?? '');
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

  // Every settings edit below calls `setProject(...)`, so the composer's
  // allow-list follows the right-hand cards live.
  useProjectScopeHydration(project);

  // Preload org models so the composer's model pill (and the "Chat defaults"
  // card) aren't empty on a fresh session that never visited /chat first.
  useEffect(() => {
    fetchModelsForContext(ASSISTANT_CTX).catch(() => {});
  }, []);

  const canEdit = project?.role === 'owner' || project?.role === 'editor';
  const isOwner = project?.role === 'owner';
  const isMember =
    (project?.role === 'editor' || project?.role === 'viewer') &&
    hasDirectMembership(project?.members, profile?.userId);
  const isArchived = Boolean(project?.isArchived);

  /** A PATCH response, minus a tool list that newer local toggles have already moved past. */
  const applyServerProject = useCallback(
    (updated: ProjectDetail) => {
      setProject({ ...updated, tools: toolsRef.current });
    },
    [setProject],
  );
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
      applyServerProject(updated);
      setInstructionsDraft(updated.instructions ?? '');
      setIsEditingInstructions(false);
    } catch {
      toast.error(t('chat.projects.workspace.saveInstructionsFailed'));
    } finally {
      setIsSavingInstructions(false);
    }
  }, [project, projectId, instructionsDraft, applyServerProject, t]);

  const handleKbCreated = useCallback((kbId: string) => {
    setProject((prev) => (prev ? { ...prev, linkedKnowledgeBaseId: kbId } : prev));
  }, []);

  const handleConnectorsChange = useCallback(
    async (patch: { knowledgeScope: ProjectKnowledgeScope; appliedFilters: AppliedFilters }) => {
      if (!project) return;
      const previousScope = project.knowledgeScope;
      const previousFilters = project.appliedFilters;
      setProject({ ...project, knowledgeScope: patch.knowledgeScope, appliedFilters: patch.appliedFilters });
      try {
        const updated = await ProjectApi.update(projectId, patch);
        applyServerProject(updated);
      } catch {
        setProject((prev) =>
          prev ? { ...prev, knowledgeScope: previousScope, appliedFilters: previousFilters } : prev,
        );
        toast.error(t('chat.projects.workspace.updateConnectorsFailed'));
      }
    },
    [project, projectId, applyServerProject, t],
  );

  const toolsSaver = useMemo(
    () =>
      createSerialSaver<string[], ProjectDetail>({
        save: (tools) => ProjectApi.update(projectId, { tools }),
        onSaved: (updated) => {
          confirmedToolsRef.current = updated.tools ?? [];
          toolsRef.current = updated.tools ?? [];
          setProject(updated);
        },
        onError: () => {
          toolsRef.current = confirmedToolsRef.current;
          setProject((prev) => (prev ? { ...prev, tools: confirmedToolsRef.current } : prev));
          toast.error(t('chat.projects.workspace.updateToolsFailed'));
        },
      }),
    [projectId, setProject, t],
  );

  const handleToolsChange = useCallback(
    (update: (previous: string[]) => string[]) => {
      const next = update(toolsRef.current);
      toolsRef.current = next;
      setProject((prev) => (prev ? { ...prev, tools: next } : prev));
      void toolsSaver.push(next);
    },
    [setProject, toolsSaver],
  );

  const handleTogglePin = useCallback(async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const updated = project.isPinned
        ? await ProjectApi.unpin(projectId)
        : await ProjectApi.pin(projectId);
      applyServerProject(updated);
      upsertProjectInList({ ...updated, conversationCount: conversationTotal });
    } catch {
      toast.error(t('chat.projects.workspace.updatePinFailed'));
    } finally {
      setIsMutating(false);
    }
  }, [project, projectId, isMutating, conversationTotal, upsertProjectInList, applyServerProject, t]);

  const handleToggleArchive = useCallback(async () => {
    if (!project || isMutating) return;
    setIsMutating(true);
    try {
      const wasArchived = project.isArchived;
      const updated = wasArchived
        ? await ProjectApi.unarchive(projectId)
        : await ProjectApi.archive(projectId);
      applyServerProject(updated);
      bumpProjectsVersion();
      toast.success(wasArchived ? t('chat.projects.restoreSuccess') : t('chat.projects.archiveSuccess'));
    } catch {
      toast.error(t('chat.projects.workspace.updateArchiveFailed'));
    } finally {
      setIsMutating(false);
    }
  }, [project, projectId, isMutating, bumpProjectsVersion, applyServerProject, t]);

  const handleConfirmLeave = useCallback(async () => {
    const myUserId = profile?.userId;
    if (!myUserId) {
      toast.error(t('chat.projects.leaveDialog.failed'));
      return;
    }
    setIsLeaving(true);
    try {
      const { removed } = await ProjectApi.removeMember(projectId, myUserId);
      if (!removed) {
        setLeaveOpen(false);
        toast.error(t('chat.projects.leaveDialog.inherited'));
        return;
      }
      removeProjectFromList(projectId);
      setLeaveOpen(false);
      toast.success(t('chat.projects.leaveDialog.left'));
      router.push('/projects/');
    } catch {
      toast.error(t('chat.projects.leaveDialog.failed'));
    } finally {
      setIsLeaving(false);
    }
  }, [profile?.userId, projectId, removeProjectFromList, router, t]);

  const handleDetailsSaved = useCallback(
    (updated: ProjectDetail) => {
      applyServerProject(updated);
      upsertProjectInList({ ...updated, tools: toolsRef.current, conversationCount: conversationTotal });
    },
    [applyServerProject, upsertProjectInList, conversationTotal],
  );

  const handleConfirmDelete = useCallback(async () => {
    setIsDeleting(true);
    try {
      await ProjectApi.remove(projectId);
      removeProjectFromList(projectId);
      setDeleteDialogOpen(false);
      router.push('/projects/');
    } catch {
      toast.error(t('chat.projects.deleteDialog.failed'));
    } finally {
      setIsDeleting(false);
    }
  }, [projectId, removeProjectFromList, router, t]);

  const handleShareSuccess = useCallback(async () => {
    try {
      const refreshed = await ProjectApi.get(projectId);
      applyServerProject(refreshed);
    } catch {
      toast.error(t('chat.projects.workspace.shareRefreshFailed'));
    }
  }, [projectId, applyServerProject, t]);

  // ── Composer: hands the message off to /chat via the pending-chat buffer ──
  const handleSend = useCallback(
    (message: string, attachments?: AttachmentRef[]) => {
      if (!message.trim() && (!attachments || attachments.length === 0)) return;
      // The server refuses new chats in an archived project; keep the draft.
      if (isArchived) {
        toast.error(t('chat.projects.archivedNoNewChats'));
        return false;
      }
      usePendingChatStore.getState().setPending({
        message,
        attachments,
        pageContext: {},
        referrerPage: pathname,
      });
      router.push(buildChatHref({ projectId }));
    },
    [projectId, pathname, router, isArchived, t],
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

  // ── Quick-start suggestion pills ────────────────────────────────────────
  const handleSuggestionClick = useCallback(
    (key: SuggestionKey) => {
      if (key === 'instructions') {
        // The instructions are literally the "Project setup" row below —
        // jump straight into editing it instead of prefilling the composer.
        setExpandedSetupSection('instructions');
        if (canEdit) {
          setInstructionsDraft(project?.instructions ?? '');
          setIsEditingInstructions(true);
        }
        return;
      }
      const text =
        key === 'summarise'
          ? t('chat.projects.workspace.suggestSummariseDocumentPrompt', {
              defaultValue: "Summarize the most important document in this project's files.",
            })
          : t('chat.projects.workspace.suggestPlanWorkPrompt', {
              defaultValue: 'Help me plan a piece of work for this project. Break it down into concrete next steps.',
            });
      prefillCounterRef.current += 1;
      setComposerPrefill({ text, key: prefillCounterRef.current });
    },
    [canEdit, project],
  );

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
    <Flex direction="column" style={{ width: '100%', height: '100%', minHeight: 0, overflow: 'hidden' }}>
      {/* Breadcrumb bar */}
      <Flex
        align="center"
        justify="between"
        gap="3"
        style={{
          padding: isMobile
            ? `var(--space-3) var(--space-4) var(--space-3) ${MOBILE_HAMBURGER_GUTTER_PX}px`
            : 'var(--space-3) var(--space-6)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
        }}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <SidebarExpandButton placement="inline" />
          <Link
            href="/projects/"
            style={{ display: 'flex', alignItems: 'center', gap: 4, textDecoration: 'none', flexShrink: 0 }}
          >
            <MaterialIcon name="chevron_left" size={18} color="var(--slate-10)" />
            <Text size="2" style={{ color: 'var(--slate-10)' }}>
              {t('projects.pageTitle')}
            </Text>
          </Link>
          <Text size="2" style={{ color: 'var(--slate-7)' }}>
            /
          </Text>
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {project.name}
          </Text>
        </Flex>

        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {/* Phones move Share into the menu so the project name keeps room. */}
          {isOwner && !isMobile && (
            <LoadingButton size="1" variant="soft" color="gray" className="touch-hit" onClick={() => setShareOpen(true)}>
              <Flex align="center" gap="1">
                <MaterialIcon name="ios_share" size={14} />
                {t('chat.projects.workspace.share')}
              </Flex>
            </LoadingButton>
          )}
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <button
                type="button"
                className="touch-hit"
                aria-label={t('projects.actionsLabel')}
                style={{
                  appearance: 'none',
                  border: 'none',
                  background: 'transparent',
                  borderRadius: 'var(--radius-1)',
                  padding: 4,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  ...touchTarget(!canHover),
                }}
              >
                <MaterialIcon name="more_horiz" size={20} color="var(--slate-11)" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content align="end">
              {isOwner && isMobile && (
                <DropdownMenu.Item onClick={() => setShareOpen(true)}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="ios_share" size={16} />
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
              {canEdit && !isArchived && (
                <DropdownMenu.Item onClick={() => setEditOpen(true)}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="edit" size={16} />
                    <Text size="2">{t('chat.projects.editDetails')}</Text>
                  </Flex>
                </DropdownMenu.Item>
              )}
              {isOwner && (
                <DropdownMenu.Item onClick={() => void handleToggleArchive()} disabled={isMutating}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name={project.isArchived ? 'unarchive' : 'archive'} size={16} />
                    <Text size="2">
                      {project.isArchived
                        ? t('chat.projects.unarchiveProject')
                        : t('chat.projects.archiveProject')}
                    </Text>
                  </Flex>
                </DropdownMenu.Item>
              )}
              {isMember && (
                <DropdownMenu.Item color="red" onClick={() => setLeaveOpen(true)}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="logout" size={16} color="var(--red-11)" />
                    <Text size="2" style={{ color: 'var(--red-11)' }}>
                      {t('chat.projects.leaveProject')}
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
          <Link href="/workspace/profile/" aria-label={t('workspace.sidebar.nav.profile')} style={{ lineHeight: 0 }}>
            <UserAvatar
              fullName={profile?.fullName}
              firstName={profile?.firstName}
              lastName={profile?.lastName}
              email={profile?.email}
              src={profile?.avatarUrl}
              size={28}
              radius="small"
            />
          </Link>
        </Flex>
      </Flex>

      {/* Two-column body */}
      <Flex
        direction={stacked ? 'column' : 'row'}
        gap="6"
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          overflow: stacked ? 'auto' : 'hidden',
          padding: isMobile ? 'var(--space-4)' : 'var(--space-6)',
        }}
      >
        {/* Left column — leftover space is 40% above / 60% below the composer. */}
        <Flex
          direction="column"
          align="center"
          className="no-scrollbar"
          style={{
            // Stacked, it takes its content height, so Project setup follows the composer without a gap.
            flex: stacked ? '0 0 auto' : '1 1 60%',
            minWidth: 0,
            minHeight: 0,
            height: stacked ? undefined : '100%',
            overflowY: stacked ? undefined : 'auto',
          }}
        >
          {!stacked && <Box style={{ flex: 4, minHeight: 0 }} />}
          <Box
            style={{
              ...chatContentColumnStyle(),
              // Stacked, the page already pads; the chat gutter would push the hero in past the cards below.
              ...(stacked ? { paddingLeft: 0, paddingRight: 0 } : {}),
              width: '100%',
              flexShrink: 0,
            }}
          >
            {isArchived && (
              <Callout.Root color="amber" variant="soft" size="1" role="status" style={{ marginBottom: 'var(--space-4)' }}>
                <Callout.Icon>
                  <MaterialIcon name="archive" size={16} />
                </Callout.Icon>
                <Flex align="center" justify="between" gap="3" wrap="wrap" style={{ width: '100%' }}>
                  <Callout.Text size="2">
                    {isOwner ? t('chat.projects.archivedBanner') : t('chat.projects.archivedBannerMember')}
                  </Callout.Text>
                  {isOwner && (
                    <Button size="1" variant="soft" color="amber" disabled={isMutating} onClick={() => void handleToggleArchive()}>
                      {t('chat.projects.unarchiveProject')}
                    </Button>
                  )}
                </Flex>
              </Callout.Root>
            )}
            <Flex direction="column" gap="1" style={{ marginBottom: 'var(--space-5)' }}>
              <Text size="6" weight="bold" style={{ color: 'var(--slate-12)' }}>
                {t('chat.projects.workspace.heroTitle', { defaultValue: 'What should we work on?' })}
              </Text>
              <Text size="2" style={{ color: 'var(--slate-10)' }}>
                {t('chat.projects.workspace.heroSubtitle', {
                  defaultValue: "Every chat you start here inherits this project's instructions, files and tools.",
                })}
              </Text>
              {project.description?.trim() ? (
                <Text
                  as="p"
                  size="2"
                  style={{
                    color: 'var(--slate-12)',
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                    marginTop: 'var(--space-2)',
                  }}
                >
                  {project.description.trim()}
                </Text>
              ) : (
                canEdit && !isArchived && (
                  <Button
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={() => setEditOpen(true)}
                    style={{ alignSelf: 'flex-start', margin: 'var(--space-2) 0 0' }}
                  >
                    <MaterialIcon name="add" size={14} />
                    {t('chat.projects.workspace.addDescription')}
                  </Button>
                )
              )}
            </Flex>
            <Box
              inert={isArchived || undefined}
              aria-disabled={isArchived || undefined}
              style={isArchived ? { opacity: 0.5, pointerEvents: 'none' } : undefined}
            >
              <ChatInput
                variant="full"
                onSend={handleSend}
                onUploadFile={handleUploadFile}
                onDeleteFile={handleDeleteFile}
                prefill={composerPrefill}
              />
            </Box>
            <Flex
              gap="2"
              wrap="wrap"
              inert={isArchived || undefined}
              style={{ marginTop: 'var(--space-3)', ...(isArchived ? { opacity: 0.5 } : {}) }}
            >
              <SuggestionPill
                icon="description"
                label={t('chat.projects.workspace.suggestSummariseDocument', {
                  defaultValue: 'Summarise a document',
                })}
                onClick={() => handleSuggestionClick('summarise')}
              />
              <SuggestionPill
                icon="checklist"
                label={t('chat.projects.workspace.suggestPlanWork', { defaultValue: 'Plan a piece of work' })}
                onClick={() => handleSuggestionClick('plan')}
              />
              {canEdit && (
                <SuggestionPill
                  icon="sync_alt"
                  label={t('chat.projects.workspace.suggestWriteInstructions', {
                    defaultValue: 'Write the project instructions',
                  })}
                  onClick={() => handleSuggestionClick('instructions')}
                />
              )}
            </Flex>
          </Box>
          {!stacked && <Box style={{ flex: 6, minHeight: 0 }} />}
        </Flex>

        {/* Right column */}
        <Box
          className="no-scrollbar"
          style={{
            flex: stacked ? '0 0 auto' : '0 0 300px',
            width: stacked ? '100%' : 300,
            minHeight: 0,
            height: stacked ? undefined : '100%',
            overflowY: stacked ? undefined : 'auto',
          }}
        >
          <Flex direction="column" gap="4">
            <ProjectSettingsPanel
              project={project}
              canEdit={canEdit}
              isOwner={isOwner}
              expandedSection={expandedSetupSection}
              onExpandedSectionChange={setExpandedSetupSection}
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
              onKbCreated={handleKbCreated}
              onConnectorsChange={(patch) => void handleConnectorsChange(patch)}
              onToolsChange={handleToolsChange}
              onOpenShare={() => setShareOpen(true)}
            />
            <ChatDefaultsCard />
            <AboutCard project={project} isOwner={isOwner} onOpenShare={() => setShareOpen(true)} />
          </Flex>
        </Box>
      </Flex>

      {shareAdapter && (
        <ShareSidebar
          open={shareOpen}
          onOpenChange={setShareOpen}
          adapter={shareAdapter}
          onShareSuccess={() => void handleShareSuccess()}
          headerContent={<ProjectSharingSettings project={project} onUpdated={handleDetailsSaved} />}
        />
      )}

      <EditProjectDialog open={editOpen} onOpenChange={setEditOpen} project={project} onSaved={handleDetailsSaved} />

      <LeaveProjectDialog
        open={leaveOpen}
        onOpenChange={setLeaveOpen}
        projectName={project.name}
        onConfirm={handleConfirmLeave}
        isLeaving={isLeaving}
      />

      <DeleteProjectDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        isDeleting={isDeleting}
        projectName={project.name}
      />
    </Flex>
  );
}
