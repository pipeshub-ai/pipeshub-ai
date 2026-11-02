'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Box,
  DropdownMenu,
  Flex,
  IconButton,
  SegmentedControl,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectSummary } from '@/chat/project-types';
import { useChatStore } from '@/chat/store';
import { CreateProjectDialog, DeleteProjectDialog, LeaveProjectDialog } from '@/chat/sidebar/dialogs';
import { useUserStore } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar/constants';
import { pageInlinePadding } from '@/app/(main)/workspace/components/settings-page-container';

const PROJECT_LIST_PAGE_SIZE = 30;
type ProjectListTab = 'active' | 'archived';

const HEADER_ROW_HEIGHT = 32;
const MENU_TRIGGER_SIZE = 22;

export type ProjectCardAction = 'pin' | 'unpin' | 'archive' | 'unarchive' | 'delete' | 'leave';

/** What the caller may do to a project from its card: pin is per user, the rest follow the role. */
export function projectCardActions(project: Pick<ProjectSummary, 'role' | 'isPinned' | 'isArchived'>): ProjectCardAction[] {
  const actions: ProjectCardAction[] = [];
  if (!project.isArchived) actions.push(project.isPinned ? 'unpin' : 'pin');
  if (project.role === 'owner') {
    actions.push(project.isArchived ? 'unarchive' : 'archive', 'delete');
  } else if (project.role === 'editor' || project.role === 'viewer') {
    actions.push('leave');
  }
  return actions;
}

const ACTION_ICON: Record<ProjectCardAction, string> = {
  pin: 'star_outline',
  unpin: 'star',
  archive: 'archive',
  unarchive: 'unarchive',
  delete: 'delete',
  leave: 'logout',
};

const ACTION_LABEL_KEY: Record<ProjectCardAction, string> = {
  pin: 'chat.projects.pinProject',
  unpin: 'chat.projects.unpinProject',
  archive: 'chat.projects.archiveProject',
  unarchive: 'chat.projects.unarchiveProject',
  delete: 'chat.projects.deleteProject',
  leave: 'chat.projects.leaveProject',
};

interface ProjectCardProps {
  project: ProjectSummary;
  onOpen: () => void;
  onAction: (action: ProjectCardAction) => void;
  isBusy?: boolean;
}

function ProjectCard({ project, onOpen, onAction, isBusy = false }: ProjectCardProps) {
  const { t } = useTranslation();
  const actions = projectCardActions(project);
  const [isHovered, setIsHovered] = useState(false);
  const [isTriggerFocused, setIsTriggerFocused] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  // The opener and the restore menu are siblings: a button nested inside a
  // `role="button"` element is exposed inconsistently by assistive tech.
  return (
    <Box
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: 'relative',
        display: 'flex',
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-3)',
        minHeight: 130,
      }}
    >
      <Box
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onOpen();
          }
        }}
        style={{
          flex: 1,
          minWidth: 0,
          borderRadius: 'inherit',
          padding: 'var(--space-4)',
          cursor: 'pointer',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-2)',
        }}
      >
        <Flex align="center" justify="between">
          <Box
            style={{
              width: HEADER_ROW_HEIGHT,
              height: HEADER_ROW_HEIGHT,
              borderRadius: 'var(--radius-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--accent-3)',
              border: '1px solid var(--accent-6)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="folder" size={18} color={project.color || 'var(--accent-11)'} />
          </Box>
          <Flex align="center" gap="2">
            {project.isPinned && <MaterialIcon name="star" size={16} color="var(--amber-9)" />}
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {t(`chat.projects.roles.${project.role === 'none' ? 'viewer' : project.role}`)}
            </Text>
            {/* Reserves the slot the absolutely-positioned menu trigger sits over. */}
            {actions.length > 0 && <Box style={{ width: MENU_TRIGGER_SIZE, height: MENU_TRIGGER_SIZE, flexShrink: 0 }} />}
          </Flex>
        </Flex>
        <Text
          size="3"
          weight="bold"
          style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {project.name}
        </Text>
        <Text
          size="2"
          style={{
            color: 'var(--slate-10)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            flex: 1,
          }}
        >
          {project.description?.trim() || t('chat.projects.workspace.noDescription')}
        </Text>
        <Flex align="center" justify="between" style={{ marginTop: 'auto' }}>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {t(
              project.conversationCount === 1 ? 'projects.chatCount_one' : 'projects.chatCount_other',
              { count: project.conversationCount },
            )}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {t('projects.lastActive', { date: formatRelativeTime(project.lastActivityAt) })}
          </Text>
        </Flex>
      </Box>
      {actions.length > 0 && (
        <Box
          style={{
            position: 'absolute',
            // Vertically centred on the header row.
            top: `calc(var(--space-4) + ${(HEADER_ROW_HEIGHT - MENU_TRIGGER_SIZE) / 2}px)`,
            right: 'var(--space-4)',
            width: MENU_TRIGGER_SIZE,
            height: MENU_TRIGGER_SIZE,
          }}
        >
          <DropdownMenu.Root open={isMenuOpen} onOpenChange={setIsMenuOpen} modal={false}>
            <DropdownMenu.Trigger>
              {/* Always mounted: revealing it on hover alone hid it from keyboard and touch users. */}
              <button
                type="button"
                className="touch-hit"
                aria-label={t('projects.cardActionsLabel', { name: project.name })}
                onFocus={() => setIsTriggerFocused(true)}
                onBlur={() => setIsTriggerFocused(false)}
                style={{
                  appearance: 'none',
                  border: 'none',
                  background: isMenuOpen ? 'var(--olive-5)' : 'transparent',
                  borderRadius: 'var(--radius-1)',
                  padding: 2,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  opacity: isHovered || isTriggerFocused || isMenuOpen ? 1 : 0.5,
                  transition: 'opacity 0.15s ease',
                }}
              >
                <MaterialIcon name="more_horiz" size={18} color="var(--slate-11)" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content side="bottom" align="end" sideOffset={4} style={{ minWidth: 170 }}>
              {actions.map((action) => {
                const danger = action === 'delete' || action === 'leave';
                const color = danger ? 'var(--red-11)' : 'var(--slate-11)';
                return (
                  <DropdownMenu.Item
                    key={action}
                    color={danger ? 'red' : undefined}
                    disabled={isBusy}
                    onSelect={() => onAction(action)}
                  >
                    <Flex align="center" gap="2">
                      <MaterialIcon name={ACTION_ICON[action]} size={16} color={color} />
                      <Text size="2" style={{ color }}>
                        {t(ACTION_LABEL_KEY[action])}
                      </Text>
                    </Flex>
                  </DropdownMenu.Item>
                );
              })}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        </Box>
      )}
    </Box>
  );
}

/**
 * `/projects` (no `projectId`) — grid of the user's projects (owned + shared),
 * with search and create. Clicking a card navigates to `/projects?projectId=…`.
 */
const PROJECTS_MAX_WIDTH = 1100;

export function ProjectList() {
  const router = useRouter();
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  const upsertProjectInList = useChatStore((s) => s.upsertProjectInList);
  const bumpProjectsVersion = useChatStore((s) => s.bumpProjectsVersion);

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [activeTab, setActiveTab] = useState<ProjectListTab>('active');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ProjectSummary | null>(null);
  const [pendingLeave, setPendingLeave] = useState<ProjectSummary | null>(null);
  const myUserId = useUserStore((s) => s.profile?.userId);
  const removeProjectFromList = useChatStore((s) => s.removeProjectFromList);

  // Guards against an in-flight request for a stale query/page overwriting
  // the result of a newer one that resolves first.
  const requestIdRef = useRef(0);

  const load = useCallback(async (
    query: string,
    targetPage: number,
    tab: ProjectListTab,
  ) => {
    const requestId = ++requestIdRef.current;
    if (targetPage === 1) setIsLoading(true);
    else setIsLoadingMore(true);
    setHasError(false);
    try {
      const { projects: rows, pagination } = await ProjectApi.list({
        scope: 'all',
        page: targetPage,
        limit: PROJECT_LIST_PAGE_SIZE,
        isArchived: tab === 'archived',
        ...(query.trim() ? { search: query.trim() } : {}),
      });
      if (requestId !== requestIdRef.current) return;
      setProjects((prev) => (targetPage === 1 ? rows : [...prev, ...rows]));
      setPage(targetPage);
      setHasMore(targetPage < pagination.totalPages);
    } catch {
      if (requestId !== requestIdRef.current) return;
      setHasError(true);
      if (targetPage === 1) setProjects([]);
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(
      () => void load(search, 1, activeTab),
      search ? 250 : 0,
    );
    return () => clearTimeout(handle);
  }, [search, activeTab, load]);

  const loadMore = () => {
    if (isLoadingMore || !hasMore) return;
    void load(search, page + 1, activeTab);
  };

  const openProject = (projectId: string) => {
    router.push(`/projects/?projectId=${encodeURIComponent(projectId)}`);
  };

  const dropFromView = (projectId: string) => {
    setProjects((prev) => prev.filter((project) => project._id !== projectId));
  };

  const runCardAction = async (project: ProjectSummary, action: ProjectCardAction) => {
    if (action === 'delete') {
      setPendingDelete(project);
      return;
    }
    if (action === 'leave') {
      setPendingLeave(project);
      return;
    }
    if (busyId) return;
    setBusyId(project._id);
    try {
      if (action === 'pin' || action === 'unpin') {
        const updated = action === 'pin' ? await ProjectApi.pin(project._id) : await ProjectApi.unpin(project._id);
        setProjects((prev) =>
          prev.map((row) => (row._id === project._id ? { ...row, isPinned: updated.isPinned } : row)),
        );
        upsertProjectInList({ ...project, isPinned: updated.isPinned });
      } else {
        await (action === 'archive' ? ProjectApi.archive(project._id) : ProjectApi.unarchive(project._id));
        dropFromView(project._id);
        bumpProjectsVersion();
        toast.success(t(action === 'archive' ? 'chat.projects.archiveSuccess' : 'chat.projects.restoreSuccess'));
      }
    } catch {
      toast.error(
        t(
          action === 'pin' || action === 'unpin'
            ? 'chat.projects.workspace.updatePinFailed'
            : 'chat.projects.workspace.updateArchiveFailed',
        ),
      );
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setBusyId(pendingDelete._id);
    try {
      await ProjectApi.remove(pendingDelete._id);
      dropFromView(pendingDelete._id);
      removeProjectFromList(pendingDelete._id);
      setPendingDelete(null);
    } catch {
      toast.error(t('chat.projects.deleteDialog.failed'));
    } finally {
      setBusyId(null);
    }
  };

  const confirmLeave = async () => {
    if (!pendingLeave) return;
    if (!myUserId) {
      toast.error(t('chat.projects.leaveDialog.failed'));
      return;
    }
    setBusyId(pendingLeave._id);
    try {
      const { removed } = await ProjectApi.removeMember(pendingLeave._id, myUserId);
      if (!removed) {
        setPendingLeave(null);
        toast.error(t('chat.projects.leaveDialog.inherited'));
        return;
      }
      dropFromView(pendingLeave._id);
      removeProjectFromList(pendingLeave._id);
      setPendingLeave(null);
      toast.success(t('chat.projects.leaveDialog.left'));
    } catch {
      toast.error(t('chat.projects.leaveDialog.failed'));
    } finally {
      setBusyId(null);
    }
  };

  const sorted = [...projects].sort((a, b) => {
    if (a.isPinned !== b.isPinned) return a.isPinned ? -1 : 1;
    return b.lastActivityAt - a.lastActivityAt;
  });

  return (
    <Flex direction="column" style={{ width: '100%' }}>
      {isMobile && <ProjectsMobileHeader onCreate={() => setCreateOpen(true)} />}
      <Flex
        direction="column"
        gap="5"
        style={{
          width: '100%',
          paddingTop: isMobile ? 'var(--space-4)' : 'var(--space-6)',
          paddingBottom: 'var(--space-6)',
          paddingInline: pageInlinePadding(PROJECTS_MAX_WIDTH),
        }}
      >
        {!isMobile && (
          <Flex align="center" justify="between" wrap="wrap" gap="3">
            <Flex align="center" gap="3" style={{ minWidth: 0 }}>
              <SidebarExpandButton placement="inline" />
              <Text size="6" weight="bold" style={{ color: 'var(--slate-12)' }}>
                {t('projects.pageTitle')}
              </Text>
            </Flex>
            <LoadingButton color="jade" className="touch-hit" onClick={() => setCreateOpen(true)}>
              <Flex align="center" gap="2">
                <MaterialIcon name="add" size={16} />
                {t('chat.projects.newProject')}
              </Flex>
            </LoadingButton>
          </Flex>
        )}

        <SegmentedControl.Root
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as ProjectListTab)}
          size={isMobile ? '3' : '2'}
          style={{ alignSelf: 'flex-start', ...(isMobile ? { height: 44 } : {}) }}
        >
          <SegmentedControl.Item value="active">
            {t('projects.tabYourProjects')}
          </SegmentedControl.Item>
          <SegmentedControl.Item value="archived">
            {t('projects.tabArchived')}
          </SegmentedControl.Item>
        </SegmentedControl.Root>

        <TextField.Root
          placeholder={t('projects.searchPlaceholder')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 360 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--slate-9)" />
          </TextField.Slot>
        </TextField.Root>

        {isLoading ? (
          <Flex align="center" justify="center" style={{ padding: 'var(--space-8) 0' }}>
            <LottieLoader autoplay loop style={{ width: 48, height: 48 }} />
          </Flex>
        ) : hasError ? (
          <Text size="2" style={{ color: '#ef4444' }}>
            {t('projects.failedToLoad')}
          </Text>
        ) : sorted.length === 0 && search.trim() ? (
          <Flex direction="column" align="center" gap="2" role="status" style={{ padding: 'var(--space-8) 0' }}>
            <MaterialIcon name="search_off" size={40} color="var(--slate-8)" />
            <Text size="3" weight="medium" style={{ color: 'var(--slate-11)', textAlign: 'center', overflowWrap: 'anywhere' }}>
              {t('projects.noSearchResults', { query: search.trim() })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {t('projects.noSearchResultsHint')}
            </Text>
            <LoadingButton variant="soft" color="gray" onClick={() => setSearch('')}>
              {t('projects.clearSearch')}
            </LoadingButton>
          </Flex>
        ) : sorted.length === 0 ? (
          <Flex direction="column" align="center" gap="2" style={{ padding: 'var(--space-8) 0' }}>
            <MaterialIcon name="folder_open" size={40} color="var(--slate-8)" />
            <Text size="3" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t(
                activeTab === 'archived'
                  ? 'projects.archivedEmptyState'
                  : 'projects.emptyState',
              )}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {t(
                activeTab === 'archived'
                  ? 'projects.archivedEmptyStateHint'
                  : 'projects.emptyStateHint',
              )}
            </Text>
          </Flex>
        ) : (
          <Flex direction="column" gap="4">
            <Box
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
                gap: 'var(--space-4)',
              }}
            >
              {sorted.map((project) => (
                <ProjectCard
                  key={project._id}
                  project={project}
                  isBusy={busyId === project._id}
                  onOpen={() => openProject(project._id)}
                  onAction={(action) => void runCardAction(project, action)}
                />
              ))}
            </Box>
            {hasMore && (
              <Flex justify="center">
                <LoadingButton variant="outline" loading={isLoadingMore} onClick={loadMore}>
                  {t('projects.loadMore')}
                </LoadingButton>
              </Flex>
            )}
          </Flex>
        )}

        <DeleteProjectDialog
          open={pendingDelete !== null}
          onOpenChange={(open) => !open && setPendingDelete(null)}
          onConfirm={confirmDelete}
          isDeleting={pendingDelete !== null && busyId === pendingDelete._id}
          projectName={pendingDelete?.name}
        />

        <LeaveProjectDialog
          open={pendingLeave !== null}
          onOpenChange={(open) => !open && setPendingLeave(null)}
          projectName={pendingLeave?.name ?? ''}
          onConfirm={confirmLeave}
          isLeaving={pendingLeave !== null && busyId === pendingLeave._id}
        />

        <CreateProjectDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          onCreated={(project) => {
            upsertProjectInList({ ...project, conversationCount: 0 });
            openProject(project._id);
          }}
        />
      </Flex>
    </Flex>
  );
}

/** Phones: the same 40px title bar as All artifacts, beside the fixed menu button. */
function ProjectsMobileHeader({ onCreate }: { onCreate: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      style={{
        height: 40,
        padding: `0 var(--space-2) 0 ${MOBILE_HAMBURGER_GUTTER_PX}px`,
        borderBottom: '1px solid var(--olive-3)',
        flexShrink: 0,
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <h1
          style={{
            margin: 0,
            fontSize: 'var(--font-size-2)',
            fontWeight: 'var(--font-weight-medium)',
            lineHeight: 'var(--line-height-2)',
            whiteSpace: 'nowrap',
            color: 'var(--slate-12)',
          }}
        >
          {t('projects.pageTitle')}
        </h1>
        <BetaBadge />
      </Flex>
      <IconButton
        className="touch-hit"
        variant="ghost"
        size="2"
        color="gray"
        onClick={onCreate}
        aria-label={t('chat.projects.newProject')}
      >
        <MaterialIcon name="add" size={18} />
      </IconButton>
    </Flex>
  );
}
