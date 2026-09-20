'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Box, Button, DropdownMenu, Flex, Text } from '@radix-ui/themes';
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
import { DeleteProjectDialog } from '@/chat/sidebar/dialogs';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useUserStore } from '@/lib/store/user-store';
import { Link } from '@/lib/navigation';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { ProjectSettingsPanel, type SetupSectionKey } from './settings-panel';
import { ChatDefaultsCard } from './chat-defaults-card';
import { AboutCard } from './about-card';

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
      toast.error(t('chat.projects.workspace.saveInstructionsFailed'));
    } finally {
      setIsSavingInstructions(false);
    }
  }, [project, projectId, instructionsDraft, t]);

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
        setProject(updated);
      } catch {
        setProject((prev) =>
          prev ? { ...prev, knowledgeScope: previousScope, appliedFilters: previousFilters } : prev,
        );
        toast.error(t('chat.projects.workspace.updateConnectorsFailed'));
      }
    },
    [project, projectId, t],
  );

  const handleToolsChange = useCallback(
    async (tools: string[]) => {
      if (!project) return;
      const previous = project.tools;
      setProject({ ...project, tools });
      try {
        const updated = await ProjectApi.update(projectId, { tools });
        setProject(updated);
      } catch {
        setProject((prev) => (prev ? { ...prev, tools: previous } : prev));
        toast.error(t('chat.projects.workspace.updateToolsFailed'));
      }
    },
    [project, projectId, t],
  );

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
      toast.error(t('chat.projects.workspace.updatePinFailed'));
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
      toast.error(t('chat.projects.workspace.updateArchiveFailed'));
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
      toast.error(t('chat.projects.deleteDialog.failed'));
    } finally {
      setIsDeleting(false);
    }
  }, [projectId, removeProjectFromList, router, t]);

  const handleShareSuccess = useCallback(async () => {
    try {
      const refreshed = await ProjectApi.get(projectId);
      setProject(refreshed);
    } catch {
      toast.error(t('chat.projects.workspace.shareRefreshFailed'));
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
          padding: isMobile ? 'var(--space-3) var(--space-4)' : 'var(--space-3) var(--space-6)',
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
          {isOwner && (
            <LoadingButton size="1" variant="soft" color="gray" onClick={() => setShareOpen(true)}>
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
        direction={isMobile ? 'column' : 'row'}
        gap="6"
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          overflow: isMobile ? 'auto' : 'hidden',
          padding: isMobile ? 'var(--space-4)' : 'var(--space-6)',
        }}
      >
        {/* Left column — leftover space is 40% above / 60% below the composer. */}
        <Flex
          direction="column"
          align="center"
          className="no-scrollbar"
          style={{
            flex: '1 1 60%',
            minWidth: 0,
            minHeight: 0,
            height: isMobile ? undefined : '100%',
            overflowY: isMobile ? undefined : 'auto',
          }}
        >
          {!isMobile && <Box style={{ flex: 4, minHeight: 0 }} />}
          <Box style={{ ...chatContentColumnStyle(isMobile), width: '100%', flexShrink: 0 }}>
            <Flex direction="column" gap="1" style={{ marginBottom: 'var(--space-5)' }}>
              <Text size="6" weight="bold" style={{ color: 'var(--slate-12)' }}>
                {t('chat.projects.workspace.heroTitle', { defaultValue: 'What should we work on?' })}
              </Text>
              <Text size="2" style={{ color: 'var(--slate-10)' }}>
                {t('chat.projects.workspace.heroSubtitle', {
                  defaultValue: "Every chat you start here inherits this project's instructions, files and tools.",
                })}
              </Text>
            </Flex>
            <ChatInput
              variant="full"
              onSend={handleSend}
              onUploadFile={handleUploadFile}
              onDeleteFile={handleDeleteFile}
              prefill={composerPrefill}
            />
            <Flex gap="2" wrap="wrap" style={{ marginTop: 'var(--space-3)' }}>
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
              <SuggestionPill
                icon="sync_alt"
                label={t('chat.projects.workspace.suggestWriteInstructions', {
                  defaultValue: 'Write the project instructions',
                })}
                onClick={() => handleSuggestionClick('instructions')}
              />
            </Flex>
          </Box>
          {!isMobile && <Box style={{ flex: 6, minHeight: 0 }} />}
        </Flex>

        {/* Right column */}
        <Box
          className="no-scrollbar"
          style={{
            flex: isMobile ? '1 1 auto' : '0 0 300px',
            width: isMobile ? '100%' : 300,
            minHeight: 0,
            height: isMobile ? undefined : '100%',
            overflowY: isMobile ? undefined : 'auto',
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
              onToolsChange={(tools) => void handleToolsChange(tools)}
              onOpenShare={() => setShareOpen(true)}
            />
            <ChatDefaultsCard />
            <AboutCard project={project} isOwner={isOwner} />
          </Flex>
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
