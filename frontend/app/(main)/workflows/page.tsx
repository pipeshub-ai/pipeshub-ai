'use client';

import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { useWorkflowRunUpdates } from '@/lib/hooks/use-workflow-run-updates';
import type { WorkflowRunUpdate } from '@/lib/hooks/use-workflow-run-updates';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  EntityPageHeader,
  EntityDataTable,
  EntityPagination,
  EntityEmptyState,
  EntityRowActionMenu,
  ConfirmationDialog,
  SelectDropdown,
} from '../workspace/components';
import type { ColumnConfig } from '../workspace/components';
import type { RowAction } from '../workspace/components/entity-row-action-menu';
import { WorkflowDetailView } from './components';
import { WorkflowsApi } from './api';
import type { Workflow, WorkflowStatus } from './types';

const STATUS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'paused', label: 'Paused' },
  { value: 'disabled', label: 'Disabled' },
  { value: 'draft', label: 'Draft' },
  { value: 'completed', label: 'Completed' },
];

const STATUS_BADGE_COLOR: Record<WorkflowStatus, React.ComponentProps<typeof Badge>['color']> = {
  active: 'jade',
  paused: 'amber',
  disabled: 'gray',
  draft: 'gray',
  completed: 'green',
};

const RUN_BADGE_COLOR: Record<string, React.ComponentProps<typeof Badge>['color']> = {
  running: 'blue',
  awaiting_input: 'amber',
  succeeded: 'jade',
  failed: 'red',
  cancelled: 'gray',
};

function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  return (
    <Badge variant="soft" color={STATUS_BADGE_COLOR[status] ?? 'gray'} size="1">
      {status}
    </Badge>
  );
}

/** Status of the most recent run — a different axis from the workflow's own
 *  lifecycle status, so it gets its own badge instead of overwriting it. */
function LatestRunBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" color={RUN_BADGE_COLOR[status] ?? 'gray'} size="1">
      {status.replace(/_/g, ' ')}
    </Badge>
  );
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function WorkflowsPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const workflowId = searchParams.get('workflowId');
  const editParam = searchParams.get('edit');
  const editMode = editParam === 'true' || editParam === '1';
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const addToast = useToastStore((s) => s.addToast);

  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Workflow | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [latestRunStatus, setLatestRunStatus] = useState<Record<string, string>>({});

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearchQuery(searchQuery.trim());
    }, 400);
    return () => window.clearTimeout(handle);
  }, [searchQuery]);

  const fetchWorkflows = useCallback(async () => {
    console.log('[WorkflowsPage] fetchWorkflows: page=%d limit=%d status=%s q=%s', page, limit, statusFilter, debouncedSearchQuery);
    setIsLoading(true);
    setError(null);
    try {
      const data = await WorkflowsApi.listWorkflows({
        page,
        limit,
        status: (statusFilter || undefined) as WorkflowStatus | undefined,
        q: debouncedSearchQuery || undefined,
      });
      console.log('[WorkflowsPage] fetchWorkflows: got %d workflows (total=%d)', data.workflows?.length, data.total);
      setWorkflows(data.workflows);
      setTotalCount(data.total);
    } catch (err: unknown) {
      console.error('[WorkflowsPage] fetchWorkflows failed:', err);
      setError(err instanceof Error ? err.message : t('workflowsPage.errorGeneric', 'Failed to load workflows'));
    } finally {
      setIsLoading(false);
    }
  }, [page, limit, statusFilter, debouncedSearchQuery, t]);

  useEffect(() => {
    if (!isProfileInitialized || workflowId) return;
    void fetchWorkflows();
  }, [isProfileInitialized, workflowId, fetchWorkflows]);

  // Live run updates from the notification socket, so a run started elsewhere
  // (webhook, cron) shows up without a manual refresh. Kept in its own map:
  // a run being `running`/`failed` says nothing about whether the workflow
  // itself is active or paused, and writing it into `workflow.status` used to
  // corrupt the status filter and badge colours until the next refetch.
  useWorkflowRunUpdates((update: WorkflowRunUpdate) => {
    if (!update.workflowId || !update.status) return;
    setLatestRunStatus((prev) => ({ ...prev, [update.workflowId]: update.status }));
  });

  const handleLimitChange = useCallback((next: number) => {
    setPage(1);
    setLimit(next);
  }, []);

  const openChatToSchedule = useCallback(() => {
    router.push('/chat');
  }, [router]);

  const runAction = useCallback(
    async (
      action: () => Promise<unknown>,
      { successTitle, errorTitle }: { successTitle: string; errorTitle: string }
    ) => {
      console.log('[WorkflowsPage] runAction started:', successTitle);
      try {
        const result = await action();
        console.log('[WorkflowsPage] runAction succeeded:', successTitle, result);
        addToast({ variant: 'success', title: successTitle, duration: 3000 });
        await fetchWorkflows();
      } catch (err: unknown) {
        console.error('[WorkflowsPage] runAction failed:', errorTitle, err);
        addToast({
          variant: 'error',
          title: errorTitle,
          description: err instanceof Error ? err.message : undefined,
          duration: 5000,
        });
      }
    },
    [addToast, fetchWorkflows]
  );

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setIsDeleting(true);
    try {
      await WorkflowsApi.deleteWorkflow(pendingDelete.workflowId);
      addToast({ variant: 'success', title: t('workflowsPage.deleteSuccess', 'Workflow deleted'), duration: 3000 });
      setPendingDelete(null);
      await fetchWorkflows();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workflowsPage.deleteError', 'Failed to delete workflow'),
        description: err instanceof Error ? err.message : undefined,
        duration: 5000,
      });
    } finally {
      setIsDeleting(false);
    }
  }, [pendingDelete, addToast, fetchWorkflows, t]);

  const columns = useMemo<ColumnConfig<Workflow>[]>(
    () => [
      {
        key: 'name',
        label: t('workflowsPage.columns.name', 'Workflow'),
        minWidth: '220px',
        render: (workflow) => (
          <Flex align="center" gap="2" style={{ overflow: 'hidden' }}>
            <MaterialIcon
              name={workflow.kind === 'code' ? 'code' : 'smart_toy'}
              size={16}
              color="var(--violet-9)"
            />
            <Flex direction="column" gap="1" style={{ overflow: 'hidden' }}>
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {workflow.name}
              </Text>
              {workflow.description ? (
                <Text
                  size="1"
                  style={{ color: 'var(--slate-9)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {workflow.description}
                </Text>
              ) : null}
            </Flex>
          </Flex>
        ),
      },
      {
        key: 'status',
        label: t('workflowsPage.columns.status', 'Status'),
        width: '190px',
        render: (workflow) => {
          const runStatus = latestRunStatus[workflow.workflowId];
          return (
            <Flex gap="1" align="center" wrap="wrap">
              <WorkflowStatusBadge status={workflow.status} />
              {runStatus && <LatestRunBadge status={runStatus} />}
            </Flex>
          );
        },
      },
      {
        key: 'triggers',
        label: t('workflowsPage.columns.triggers', 'Triggers'),
        width: '110px',
        render: (workflow) =>
          workflow.triggers.length > 0 ? (
            <Badge variant="soft" color="iris" size="1">
              {workflow.triggers.length}
            </Badge>
          ) : (
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              -
            </Text>
          ),
      },
      {
        key: 'updatedAt',
        label: t('workflowsPage.columns.updated', 'Last updated'),
        width: '180px',
        render: (workflow) => (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {formatDateTime(workflow.updatedAt)}
          </Text>
        ),
      },
    ],
    [t, latestRunStatus]
  );

  const renderRowActions = useCallback(
    (workflow: Workflow) => {
      const actions: (RowAction | false)[] = [
        {
          icon: 'visibility',
          label: t('workflowsPage.actions.view', 'View details'),
          onClick: () => {
            console.log('[WorkflowsPage] View details clicked:', workflow.workflowId);
            router.push(`/workflows?workflowId=${encodeURIComponent(workflow.workflowId)}`);
          },
        },
        {
          icon: 'play_arrow',
          label: t('workflowsPage.runNow', 'Run Now'),
          onClick: () => {
            console.log('[WorkflowsPage] Run Now clicked:', workflow.workflowId);
            return runAction(() => WorkflowsApi.runNow(workflow.workflowId), {
              successTitle: t('workflowsPage.runNowSuccess', 'Run started'),
              errorTitle: t('workflowsPage.runNowError', 'Failed to start run'),
            });
          },
        },
        workflow.status === 'active' && {
          icon: 'pause',
          label: t('workflowsPage.pause', 'Pause'),
          onClick: () => {
            console.log('[WorkflowsPage] Pause clicked:', workflow.workflowId);
            return runAction(() => WorkflowsApi.pauseWorkflow(workflow.workflowId), {
              successTitle: t('workflowsPage.pauseSuccess', 'Workflow paused'),
              errorTitle: t('workflowsPage.pauseError', 'Failed to pause workflow'),
            });
          },
        },
        workflow.status === 'paused' && {
          icon: 'play_circle',
          label: t('workflowsPage.resume', 'Resume'),
          onClick: () => {
            console.log('[WorkflowsPage] Resume clicked:', workflow.workflowId);
            return runAction(() => WorkflowsApi.resumeWorkflow(workflow.workflowId), {
              successTitle: t('workflowsPage.resumeSuccess', 'Workflow resumed'),
              errorTitle: t('workflowsPage.resumeError', 'Failed to resume workflow'),
            });
          },
        },
        (workflow.status === 'active' || workflow.status === 'paused') && {
          icon: 'smart_toy',
          label: t('workflowsPage.actions.promote', 'Promote to agent'),
          separatorBefore: true,
          onClick: () => {
            console.log('[WorkflowsPage] Promote clicked:', workflow.workflowId);
            return runAction(() => WorkflowsApi.promoteToAgent(workflow.workflowId), {
              successTitle: t('workflowsPage.promoteSuccess', 'Agent created from workflow'),
              errorTitle: t('workflowsPage.promoteError', 'Failed to promote workflow'),
            });
          },
        },
        (workflow.status === 'active' || workflow.status === 'paused') && {
          icon: 'cancel',
          label: t('workflowsPage.cancel', 'Cancel'),
          variant: 'danger' as const,
          onClick: () => {
            console.log('[WorkflowsPage] Cancel clicked:', workflow.workflowId);
            return runAction(() => WorkflowsApi.cancelWorkflow(workflow.workflowId), {
              successTitle: t('workflowsPage.cancelSuccess', 'Workflow cancelled'),
              errorTitle: t('workflowsPage.cancelError', 'Failed to cancel workflow'),
            });
          },
        },
        {
          icon: 'delete',
          label: t('workflowsPage.actions.delete', 'Delete'),
          variant: 'danger' as const,
          onClick: () => {
            console.log('[WorkflowsPage] Delete clicked:', workflow.workflowId);
            setPendingDelete(workflow);
          },
        },
      ];
      return <EntityRowActionMenu actions={actions} />;
    },
    [t, router, runAction]
  );

  const hasActiveFilters = Boolean(debouncedSearchQuery || statusFilter);
  const isEmpty = !isLoading && !error && workflows.length === 0 && !hasActiveFilters;
  const isEmptyFiltered = !isLoading && !error && workflows.length === 0 && hasActiveFilters;

  if (!isProfileInitialized) return null;

  if (workflowId) {
    return (
      <WorkflowDetailView
        key={workflowId}
        workflowId={decodeURIComponent(workflowId)}
        onBack={() => router.push('/workflows')}
        editMode={editMode}
      />
    );
  }

  return (
    <Flex
      direction="column"
      style={{ height: '100%', width: '100%', paddingLeft: '40px', paddingRight: '40px' }}
    >
      <EntityPageHeader
        title={t('workflowsPage.title', 'Workflows')}
        subtitle={t(
          'workflowsPage.subtitle',
          'Workflows created and scheduled through chat. Pause, resume, or cancel them here.'
        )}
        searchPlaceholder={t('workflowsPage.searchPlaceholder', 'Search workflows...')}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        ctaLabel={t('workflowsPage.newInChat', 'New workflow in Chat')}
        ctaIcon="add"
        onCtaClick={openChatToSchedule}
      />

      <Flex direction="column" style={{ flex: 1, overflow: 'hidden' }}>
        {isLoading && workflows.length === 0 ? (
          <Flex align="center" justify="center" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <LottieLoader variant="loader" size={48} showLabel />
          </Flex>
        ) : error ? (
          <Flex direction="column" align="center" justify="center" gap="2" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <Text size="2" style={{ color: 'var(--red-11)' }}>
              {error}
            </Text>
          </Flex>
        ) : isEmpty ? (
          <EntityEmptyState
            icon="account_tree"
            title={t('workflowsPage.emptyTitle', 'No workflows yet')}
            description={t(
              'workflowsPage.emptyDescription',
              'Ask the assistant in chat to schedule a recurring workflow, and it will show up here.'
            )}
            ctaLabel={t('workflowsPage.newInChat', 'New workflow in Chat')}
            ctaIcon="add"
            onCtaClick={openChatToSchedule}
          />
        ) : (
          <Flex
            direction="column"
            style={{ flex: 1, overflow: 'hidden', border: '1px solid var(--slate-6)', borderRadius: 'var(--radius-3)' }}
          >
            <Flex
              align="center"
              gap="2"
              style={{
                height: '40px',
                padding: '0 var(--space-4)',
                borderBottom: '1px solid var(--olive-6)',
                backgroundColor: 'var(--olive-2)',
              }}
            >
              <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
                {t('workflowsPage.statusFilterLabel', 'Status')}
              </Text>
              <div style={{ width: 160 }}>
                <SelectDropdown
                  value={statusFilter || null}
                  onChange={(v) => {
                    setPage(1);
                    setStatusFilter(v);
                  }}
                  options={STATUS_FILTER_OPTIONS}
                  placeholder={t('workflowsPage.statusFilterAll', 'All statuses')}
                />
              </div>
            </Flex>

            {isEmptyFiltered ? (
              <Flex direction="column" align="center" justify="center" gap="2" style={{ flex: 1, padding: 'var(--space-6)' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                  {t('workflowsPage.noFilterResults', 'No workflows match the applied filters')}
                </Text>
              </Flex>
            ) : (
              <>
                <EntityDataTable<Workflow>
                  columns={columns}
                  data={workflows}
                  getItemId={(workflow) => workflow.workflowId}
                  selectedIds={selectedIds}
                  onSelectionChange={setSelectedIds}
                  renderRowActions={renderRowActions}
                  isLoading={isLoading}
                  onRowClick={(workflow) => {
                    console.log('[WorkflowsPage] Row clicked:', workflow.workflowId, workflow.name);
                    router.push(`/workflows?workflowId=${encodeURIComponent(workflow.workflowId)}`);
                  }}
                />
                <EntityPagination
                  page={page}
                  limit={limit}
                  totalCount={totalCount}
                  onPageChange={setPage}
                  onLimitChange={handleLimitChange}
                />
              </>
            )}
          </Flex>
        )}
      </Flex>

      <ConfirmationDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title={t('workflowsPage.deleteConfirmTitle', 'Delete this workflow?')}
        message={t(
          'workflowsPage.deleteConfirmMessage',
          'This permanently deletes the workflow and its schedule. This cannot be undone — consider Cancel instead if you might want its history later.'
        )}
        confirmLabel={t('workflowsPage.actions.delete', 'Delete')}
        confirmVariant="danger"
        isLoading={isDeleting}
        onConfirm={handleConfirmDelete}
      />
    </Flex>
  );
}

export default function WorkflowsPage() {
  return (
    <Suspense
      fallback={
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
          <LottieLoader variant="loader" size={48} showLabel />
        </div>
      }
    >
      <WorkflowsPageContent />
    </Suspense>
  );
}
