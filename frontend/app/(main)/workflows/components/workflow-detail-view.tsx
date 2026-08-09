'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Badge, Button, Flex, Heading, IconButton, Separator, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useToastStore } from '@/lib/store/toast-store';
import { ErrorType, isProcessedError } from '@/lib/api';
import { ConfirmationDialog } from '../../workspace/components';
import { WorkflowsApi } from '../api';
import type { WorkflowVersionSummary } from '../api';
import { WorkflowStudio } from './workflow-studio';
import { WorkflowDefinitionPanel } from './workflow-definition-panel';
import { RunInspector } from './run-inspector';
import { WorkflowTriggersPanel } from './workflow-triggers-panel';
import type { Workflow, WorkflowRun, WorkflowRunStatus, WorkflowTrigger, WorkflowIR } from '../types';

const WORKFLOW_STATUS_COLOR: Record<string, React.ComponentProps<typeof Badge>['color']> = {
  active: 'jade',
  paused: 'amber',
  disabled: 'gray',
  draft: 'gray',
  completed: 'green',
};

const RUN_STATUS_COLOR: Record<WorkflowRunStatus, React.ComponentProps<typeof Badge>['color']> = {
  pending: 'gray',
  running: 'blue',
  succeeded: 'jade',
  failed: 'red',
  abandoned: 'red',
  dlq: 'red',
  cancelled: 'gray',
  awaiting_input: 'amber',
};

const EMPTY_IR: WorkflowIR = { nodes: [], edges: [], entry_node_id: null };

/** `NOT_FOUND` means "no versions yet" -- expected for a fresh agent_task
 * workflow. Anything else (503 store-unavailable, 500, network) is a real
 * failure the "Generate Code" button must not paper over. */
function isVersionsNotFound(err: unknown): boolean {
  return isProcessedError(err) && err.type === ErrorType.NOT_FOUND;
}

function versionLoadErrorMessage(err: unknown): string | null {
  if (isProcessedError(err)) return err.message;
  if (err instanceof Error) return err.message;
  return null;
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function RunAnswerForm({ run, onAnswer }: { run: WorkflowRun; onAnswer: (runId: string, answer: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = useCallback(async () => {
    const answer = value.trim();
    if (!answer) return;
    setSubmitting(true);
    try {
      await onAnswer(run.runId, answer);
      setValue('');
    } finally {
      setSubmitting(false);
    }
  }, [value, run.runId, onAnswer]);

  return (
    <Flex align="center" gap="2" style={{ width: '100%' }}>
      <TextField.Root
        size="1"
        style={{ flex: 1 }}
        placeholder={t('workflowsPage.detail.answerPlaceholder', 'Type your answer…')}
        value={value}
        disabled={submitting}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void submit();
        }}
      />
      <LoadingButton size="1" variant="soft" loading={submitting} disabled={!value.trim()} onClick={() => void submit()}>
        {t('workflowsPage.detail.answerSubmit', 'Send')}
      </LoadingButton>
    </Flex>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
  onAnswer,
  workflowName,
  conversationId,
}: {
  run: WorkflowRun;
  selected: boolean;
  onSelect: (runId: string) => void;
  onAnswer: (runId: string, answer: string) => Promise<void>;
  workflowName?: string;
  conversationId?: string | null;
}) {
  const { t } = useTranslation();
  const chatHref = conversationId ? `/chat?conversationId=${encodeURIComponent(conversationId)}` : null;

  return (
    <Flex
      direction="column"
      gap="2"
      style={{
        padding: 'var(--space-2) 0',
        borderBottom: '1px solid var(--slate-4)',
        cursor: 'pointer',
      }}
      onClick={() => onSelect(run.runId)}
    >
      <Flex align="center" justify="between" gap="3" wrap="wrap">
        <Flex align="center" gap="2">
          <Badge variant="soft" color={RUN_STATUS_COLOR[run.status] ?? 'gray'} size="1">
            {run.status}
          </Badge>
          {/* A dry run writes nothing, so reading its outcome as a real run's
              is misleading — and the two are otherwise identical here. */}
          {run.isDryRun ? (
            <Badge variant="outline" color="gray" size="1">
              {t('workflowsPage.detail.dryRun', 'Dry run')}
            </Badge>
          ) : null}
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {formatDateTime(run.startedAt ?? run.createdAt)}
          </Text>
        </Flex>
        <Flex align="center" gap="2">
          {run.error ? (
            <Text
              size="1"
              style={{ color: 'var(--red-11)', maxWidth: '50%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {run.error}
            </Text>
          ) : run.status === 'succeeded' && chatHref ? (
            <Link
              href={chatHref}
              onClick={(e) => e.stopPropagation()}
              style={{
                textDecoration: 'none',
                maxWidth: '50%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--space-1)',
              }}
            >
              <MaterialIcon name="chat" size={12} color="var(--violet-9)" />
              <Text size="1" style={{ color: 'var(--violet-11)' }}>
                {workflowName || t('workflowsPage.detail.viewInChat', 'View in Chat')}
              </Text>
            </Link>
          ) : run.status === 'succeeded' && workflowName ? (
            <Text
              size="1"
              style={{ color: 'var(--slate-9)', maxWidth: '50%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {workflowName}
            </Text>
          ) : null}
          <MaterialIcon name={selected ? 'expand_less' : 'expand_more'} size={14} />
        </Flex>
      </Flex>
      {run.status === 'awaiting_input' ? (
        <Flex direction="column" gap="1" onClick={(e) => e.stopPropagation()}>
          <Text size="1" style={{ color: 'var(--amber-11)' }}>
            {run.outputSummary || t('workflowsPage.detail.awaitingInput', 'This run needs your input to continue.')}
          </Text>
          <RunAnswerForm run={run} onAnswer={onAnswer} />
        </Flex>
      ) : null}
    </Flex>
  );
}

export interface WorkflowDetailViewProps {
  workflowId: string;
  onBack: () => void;
  /** When true, opens the edit panel in WorkflowStudio automatically (from ?edit=true). */
  editMode?: boolean;
}

export function WorkflowDetailView({ workflowId, onBack, editMode = false }: WorkflowDetailViewProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [triggers, setTriggers] = useState<WorkflowTrigger[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [workflowSource, setWorkflowSource] = useState<string>('');
  const [workflowIR, setWorkflowIR] = useState<WorkflowIR>(EMPTY_IR);
  const [showCodeGen, setShowCodeGen] = useState(false);
  /** Bumped to force `WorkflowStudio` to remount with `autoOpenEdit` when
   * the user dismisses the stale-version banner via "Regenerate" --
   * `autoOpenEdit` only seeds the Studio's internal `editOpen` state on
   * mount, so re-passing `true` on an already-mounted instance has no
   * effect without a remount. */
  const [studioKey, setStudioKey] = useState(0);
  const [forceEditOnRegenerate, setForceEditOnRegenerate] = useState(false);
  const [versions, setVersions] = useState<WorkflowVersionSummary[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  /** Set when listing/loading versions failed for a reason other than "none
   * exist yet" (store unavailable, network, etc). Distinct from `error`
   * (which is for the workflow itself failing to load) so the rest of the
   * detail view still renders. */
  const [versionLoadError, setVersionLoadError] = useState<string | null>(null);
  /** Version the user last asked for; guards against out-of-order source loads. */
  const pendingVersionRef = useRef<string | null>(null);

  const loadVersionSource = useCallback(async (wfId: string, versionId: string) => {
    try {
      const versionsData = await WorkflowsApi.listVersions(wfId);
      setVersionLoadError(null);
      const allVersions = versionsData.versions ?? [];
      setVersions(allVersions);
      const requested = pendingVersionRef.current ?? versionId;
      const chosen =
        allVersions.find((v) => v.versionId === requested) ??
        allVersions.find((v) => v.versionId === versionId) ??
        allVersions[0];
      if (chosen) {
        setSelectedVersionId(chosen.versionId);
        if (chosen.ir) setWorkflowIR(chosen.ir);
        try {
          const sourceData = await WorkflowsApi.getVersionSource(wfId, chosen.versionId);
          setWorkflowSource(sourceData.source ?? '');
        } catch (srcErr) {
          // The version's metadata (and IR, set above) loaded fine -- only
          // the source body failed, so the graph tab can still render.
          // Leave `workflowSource` empty; WorkflowStudio shows a distinct
          // "source unavailable" message rather than "no code yet" for it.
          console.warn('[WorkflowDetailView] Could not load source for version', chosen.versionId, srcErr);
          setWorkflowSource('');
        }
      }
    } catch (versionErr) {
      console.warn('[WorkflowDetailView] Could not load versions:', versionErr);
      if (isVersionsNotFound(versionErr)) {
        setVersions([]);
        setVersionLoadError(null);
      } else {
        setVersions([]);
        setVersionLoadError(
          versionLoadErrorMessage(versionErr) ??
            t('workflowsPage.detail.versionLoadError', 'Could not load workflow versions')
        );
      }
    }
  }, [t]);

  const fetchAll = useCallback(async () => {
    console.log('[WorkflowDetailView] fetchAll: loading workflow', workflowId);
    setIsLoading(true);
    setError(null);
    setVersionLoadError(null);
    try {
      const [workflowData, triggersData, runsData] = await Promise.all([
        WorkflowsApi.getWorkflow(workflowId),
        WorkflowsApi.listTriggers(workflowId),
        WorkflowsApi.listRuns(workflowId, { limit: 20, offset: 0 }),
      ]);
      console.log('[WorkflowDetailView] fetchAll: loaded', {
        name: workflowData.name,
        status: workflowData.status,
        kind: workflowData.kind,
        executionKind: workflowData.executionKind,
        currentVersionId: workflowData.currentVersionId,
        triggers: triggersData.triggers?.length,
        runs: runsData.runs?.length,
      });
      setWorkflow(workflowData);
      setTriggers(triggersData.triggers);
      setRuns(runsData.runs);

      // Load versions for all workflow types — agent_task workflows may have
      // been upgraded to code (execution_kind=code) by the builder agent.
      if (workflowData.currentVersionId) {
        await loadVersionSource(workflowId, workflowData.currentVersionId);
      } else {
        // Still try listing versions in case the version_id field is missing
        // but versions exist (e.g. a version was saved but its pin failed).
        try {
          const versionsData = await WorkflowsApi.listVersions(workflowId);
          setVersionLoadError(null);
          const allVersions = versionsData.versions ?? [];
          setVersions(allVersions);
          const first = allVersions[0];
          if (first) {
            setSelectedVersionId(first.versionId);
            if (first.ir) setWorkflowIR(first.ir);
            try {
              const sourceData = await WorkflowsApi.getVersionSource(workflowId, first.versionId);
              setWorkflowSource(sourceData.source ?? '');
            } catch (srcErr) {
              console.warn('[WorkflowDetailView] Could not load source for version', first.versionId, srcErr);
              setWorkflowSource('');
            }
          }
        } catch (versionErr) {
          if (isVersionsNotFound(versionErr)) {
            // No versions yet — expected for a fresh agent_task workflow.
            setVersions([]);
          } else {
            console.warn('[WorkflowDetailView] Could not load versions:', versionErr);
            setVersions([]);
            setVersionLoadError(
              versionLoadErrorMessage(versionErr) ??
                t('workflowsPage.detail.versionLoadError', 'Could not load workflow versions')
            );
          }
        }
      }
    } catch (err: unknown) {
      console.error('[WorkflowDetailView] fetchAll failed:', err);
      setError(err instanceof Error ? err.message : t('workflowsPage.detail.loadError', 'Failed to load workflow'));
    } finally {
      setIsLoading(false);
    }
  }, [workflowId, t, loadVersionSource]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    setSelectedRunId(null);
  }, [workflowId]);

  const withAction = useCallback(
    async (action: () => Promise<unknown>, successTitle: string, errorTitle: string) => {
      console.log('[WorkflowDetailView] withAction:', successTitle);
      setActionPending(true);
      try {
        const result = await action();
        console.log('[WorkflowDetailView] withAction succeeded:', successTitle, result);
        addToast({ variant: 'success', title: successTitle, duration: 3000 });
        await fetchAll();
      } catch (err: unknown) {
        console.error('[WorkflowDetailView] withAction failed:', errorTitle, err);
        addToast({
          variant: 'error',
          title: errorTitle,
          description: err instanceof Error ? err.message : undefined,
          duration: 5000,
        });
      } finally {
        setActionPending(false);
      }
    },
    [addToast, fetchAll]
  );

  const handleVersionChange = useCallback(
    async (versionId: string) => {
      setSelectedVersionId(versionId);
      pendingVersionRef.current = versionId;
      try {
        const chosen = versions.find((v) => v.versionId === versionId);
        if (chosen?.ir) setWorkflowIR(chosen.ir);
        const sourceData = await WorkflowsApi.getVersionSource(workflowId, versionId);
        // Two quick switches can resolve out of order; only the newest one
        // may write, otherwise the editor shows one version's code under
        // another version's label.
        if (pendingVersionRef.current !== versionId) return;
        setWorkflowSource(sourceData.source ?? '');
      } catch (err) {
        console.warn('[WorkflowDetailView] Could not load version source:', err);
      }
    },
    [workflowId, versions]
  );

  const handleVersionCommitted = useCallback(
    async (version: WorkflowVersionSummary) => {
      // Point at the version just created so `fetchAll` does not snap the
      // editor back to whatever was pinned before.
      pendingVersionRef.current = version.versionId;
      setSelectedVersionId(version.versionId);
      addToast({ variant: 'success', title: t('workflowsPage.editApplied', 'Workflow updated'), duration: 3000 });
      await fetchAll();
    },
    [fetchAll, addToast, t]
  );

  const handleAnswerRun = useCallback(
    async (runId: string, answer: string) => {
      try {
        await WorkflowsApi.answerRun(workflowId, runId, answer);
        addToast({ variant: 'success', title: t('workflowsPage.detail.answerSuccess', 'Answer sent'), duration: 3000 });
        await fetchAll();
      } catch (err: unknown) {
        addToast({
          variant: 'error',
          title: t('workflowsPage.detail.answerError', 'Failed to send answer'),
          description: err instanceof Error ? err.message : undefined,
          duration: 5000,
        });
      }
    },
    [workflowId, addToast, fetchAll, t]
  );

  const handleDelete = useCallback(async () => {
    setActionPending(true);
    try {
      await WorkflowsApi.deleteWorkflow(workflowId);
      addToast({ variant: 'success', title: t('workflowsPage.deleteSuccess', 'Workflow deleted'), duration: 3000 });
      onBack();
    } catch (err: unknown) {
      addToast({
        variant: 'error',
        title: t('workflowsPage.deleteError', 'Failed to delete workflow'),
        description: err instanceof Error ? err.message : undefined,
        duration: 5000,
      });
    } finally {
      setActionPending(false);
      setConfirmDelete(false);
    }
  }, [workflowId, addToast, onBack, t]);

  if (isLoading && !workflow) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  if (error || !workflow) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', width: '100%', padding: 'var(--space-6)' }}>
        <Text size="2" style={{ color: 'var(--red-11)' }}>
          {error ?? t('workflowsPage.detail.notFound', 'Workflow not found')}
        </Text>
        <Button variant="soft" onClick={onBack}>
          {t('workflowsPage.detail.backToList', 'Back to Workflows')}
        </Button>
      </Flex>
    );
  }

  // The version the workflow actually runs, not whichever one the version
  // dropdown currently previews -- staleness is about what will fire on the
  // next scheduled run.
  const activeVersion =
    versions.find((v) => v.versionId === workflow.currentVersionId) ?? versions[0];
  const needsRegeneration = Boolean(activeVersion?.needsRegeneration);

  return (
    <Flex direction="column" gap="5" style={{ height: '100%', width: '100%', padding: '40px', maxWidth: '900px' }}>
      <Flex align="center" gap="2">
        <IconButton variant="ghost" color="gray" size="2" onClick={onBack}>
          <MaterialIcon name="arrow_back" size={18} />
        </IconButton>
        <MaterialIcon
          name={workflow.kind === 'code' ? 'code' : 'smart_toy'}
          size={18}
          color="var(--violet-9)"
        />
        <Heading size="5" weight="medium" style={{ flex: 1, color: 'var(--slate-12)' }}>
          {workflow.name}
        </Heading>
        <Badge variant="soft" color={WORKFLOW_STATUS_COLOR[workflow.status] ?? 'gray'} size="2">
          {workflow.status}
        </Badge>
        {workflow.conversationId && (
          <Link
            href={`/chat?conversationId=${encodeURIComponent(workflow.conversationId)}`}
            style={{ textDecoration: 'none' }}
          >
            <Flex align="center" gap="1">
              <MaterialIcon name="chat" size={14} color="var(--violet-9)" />
              <Text size="1" color="violet" weight="medium">
                {t('workflowsPage.openInChat', 'Open in Chat')}
              </Text>
            </Flex>
          </Link>
        )}
      </Flex>

      {workflow.description ? (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {workflow.description}
        </Text>
      ) : null}

      <Flex align="center" gap="2" wrap="wrap">
        {workflow.status === 'active' ? (
          <LoadingButton
            variant="soft"
            loading={actionPending}
            onClick={() =>
              withAction(
                () => WorkflowsApi.pauseWorkflow(workflowId),
                t('workflowsPage.pauseSuccess', 'Workflow paused'),
                t('workflowsPage.pauseError', 'Failed to pause workflow')
              )
            }
          >
            <MaterialIcon name="pause" size={16} /> {t('workflowsPage.pause', 'Pause')}
          </LoadingButton>
        ) : null}
        {workflow.status === 'paused' ? (
          <LoadingButton
            variant="soft"
            loading={actionPending}
            onClick={() =>
              withAction(
                () => WorkflowsApi.resumeWorkflow(workflowId),
                t('workflowsPage.resumeSuccess', 'Workflow resumed'),
                t('workflowsPage.resumeError', 'Failed to resume workflow')
              )
            }
          >
            <MaterialIcon name="play_circle" size={16} /> {t('workflowsPage.resume', 'Resume')}
          </LoadingButton>
        ) : null}
        <LoadingButton
          variant="soft"
          loading={actionPending}
          onClick={() =>
            withAction(
              () => WorkflowsApi.runNow(workflowId),
              t('workflowsPage.runNowSuccess', 'Run started'),
              t('workflowsPage.runNowError', 'Failed to start run')
            )
          }
        >
          <MaterialIcon name="play_arrow" size={16} /> {t('workflowsPage.runNow', 'Run Now')}
        </LoadingButton>
        <LoadingButton
          variant="soft"
          loading={actionPending}
          onClick={() =>
            withAction(
              () => WorkflowsApi.dryRun(workflowId),
              t('workflowsPage.dryRunSuccess', 'Dry run started (WRITE steps skipped)'),
              t('workflowsPage.dryRunError', 'Failed to start dry run')
            )
          }
        >
          <MaterialIcon name="science" size={16} /> {t('workflowsPage.dryRun', 'Dry Run')}
        </LoadingButton>
        {workflow.status === 'active' || workflow.status === 'paused' ? (
          <LoadingButton
            variant="soft"
            loading={actionPending}
            onClick={() =>
              withAction(
                () => WorkflowsApi.promoteToAgent(workflowId),
                t('workflowsPage.promoteSuccess', 'Agent created from workflow'),
                t('workflowsPage.promoteError', 'Failed to promote workflow')
              )
            }
          >
            <MaterialIcon name="smart_toy" size={16} /> {t('workflowsPage.actions.promote', 'Promote to agent')}
          </LoadingButton>
        ) : null}
        {workflow.status === 'active' || workflow.status === 'paused' ? (
          <LoadingButton variant="soft" color="red" loading={actionPending} onClick={() => setConfirmCancel(true)}>
            <MaterialIcon name="cancel" size={16} /> {t('workflowsPage.cancel', 'Cancel')}
          </LoadingButton>
        ) : null}
        <LoadingButton variant="soft" color="red" loading={actionPending} onClick={() => setConfirmDelete(true)}>
          <MaterialIcon name="delete" size={16} /> {t('workflowsPage.actions.delete', 'Delete')}
        </LoadingButton>
      </Flex>

      {/* Workflow definition panel for agent_task workflows */}
      {workflow.kind === 'agent_task' && (
        <>
          <Separator size="4" />
          <WorkflowDefinitionPanel workflow={workflow} />
        </>
      )}

      {/* Code studio: shown for code workflows, or agent_task workflows that
          have been upgraded to code by the builder agent. */}
      {(workflow.kind === 'code' || versions.length > 0 || workflowSource) && (
        <>
          <Separator size="4" />
          {needsRegeneration && (
            <Flex
              align="center"
              justify="between"
              gap="3"
              wrap="wrap"
              style={{
                padding: 'var(--space-3) var(--space-4)',
                background: 'var(--amber-a2)',
                borderRadius: 'var(--radius-3)',
                border: '1px dashed var(--amber-a6)',
              }}
            >
              <Flex align="center" gap="2">
                <MaterialIcon name="warning" size={16} color="var(--amber-9)" />
                <Text size="2" style={{ color: 'var(--amber-11)' }}>
                  {t(
                    'workflowsPage.detail.staleVersionBanner',
                    "This workflow's code was generated before recent SDK safety checks and may fail at runtime. Regenerate it to pick up the latest checks."
                  )}
                </Text>
              </Flex>
              <Button
                size="1"
                variant="soft"
                color="amber"
                onClick={() => {
                  setForceEditOnRegenerate(true);
                  setStudioKey((k) => k + 1);
                }}
              >
                <MaterialIcon name="auto_fix_high" size={14} />
                {t('workflowsPage.detail.regenerate', 'Regenerate')}
              </Button>
            </Flex>
          )}
          <WorkflowStudio
            key={studioKey}
            workflowId={workflowId}
            source={workflowSource}
            ir={workflowIR}
            versions={versions}
            selectedVersionId={selectedVersionId}
            onVersionChange={(id) => void handleVersionChange(id)}
            currentVersionId={workflow.currentVersionId}
            readOnly={false}
            onCommitted={handleVersionCommitted}
            autoOpenEdit={editMode || forceEditOnRegenerate}
          />
        </>
      )}

      {/* For pure agent_task with no code versions yet, show a compact
          "Generate Code" entry point instead of the full studio tabs -- unless
          version loading actually failed, in which case "Generate Code" would
          lie about code that may well exist but couldn't be fetched. */}
      {workflow.kind === 'agent_task' && versions.length === 0 && !workflowSource && (
        <>
          <Separator size="4" />
          {versionLoadError ? (
            <Flex
              align="center"
              justify="between"
              gap="3"
              wrap="wrap"
              style={{
                padding: 'var(--space-3) var(--space-4)',
                background: 'var(--red-a2)',
                borderRadius: 'var(--radius-3)',
                border: '1px dashed var(--red-a6)',
              }}
            >
              <Flex align="center" gap="2">
                <MaterialIcon name="error_outline" size={16} color="var(--red-9)" />
                <Text size="2" style={{ color: 'var(--red-11)' }}>
                  {t('workflowsPage.detail.versionLoadErrorBanner', 'Could not load this workflow\'s code: {{error}}', {
                    error: versionLoadError,
                  })}
                </Text>
              </Flex>
              <LoadingButton
                size="1"
                variant="soft"
                color="red"
                loading={isLoading}
                onClick={() => void fetchAll()}
              >
                <MaterialIcon name="refresh" size={14} />
                {t('workflowsPage.detail.retry', 'Retry')}
              </LoadingButton>
            </Flex>
          ) : editMode || showCodeGen ? (
            <WorkflowStudio
              workflowId={workflowId}
              source=""
              ir={EMPTY_IR}
              versions={[]}
              readOnly={false}
              onCommitted={handleVersionCommitted}
              autoOpenEdit
            />
          ) : (
            <Flex
              align="center"
              justify="between"
              style={{
                padding: 'var(--space-3) var(--space-4)',
                background: 'var(--gray-a2)',
                borderRadius: 'var(--radius-3)',
                border: '1px dashed var(--slate-6)',
              }}
            >
              <Flex align="center" gap="2">
                <MaterialIcon name="code" size={16} color="var(--slate-9)" />
                <Text size="2" style={{ color: 'var(--slate-9)' }}>
                  {t('workflowsPage.studio.noCodeYet', 'This workflow runs as an agent task. You can optionally generate code.')}
                </Text>
              </Flex>
              <Button
                size="1"
                variant="soft"
                color="violet"
                onClick={() => setShowCodeGen(true)}
              >
                <MaterialIcon name="auto_fix_high" size={14} />
                {t('workflowsPage.studio.generateCode', 'Generate Code')}
              </Button>
            </Flex>
          )}
        </>
      )}

      {(workflow.executionKind || (workflow.toolNames && workflow.toolNames.length > 0) || (workflow.connectorIds && workflow.connectorIds.length > 0) || (workflow.collectionIds && workflow.collectionIds.length > 0) || workflow.maxTurns != null || workflow.timeoutSeconds != null) && (
        <Flex direction="column" gap="2">
          <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workflowsPage.configuration', 'Configuration')}
          </Heading>
          <Flex direction="column" gap="1" style={{ padding: 'var(--space-3)', background: 'var(--gray-a2)', borderRadius: 'var(--radius-3)' }}>
            {workflow.executionKind && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.executionKind', 'Execution')}
                </Text>
                <Badge variant="soft" color={workflow.executionKind === 'code' ? 'iris' : 'violet'} size="1">
                  {workflow.executionKind === 'code' ? 'Code' : 'Agent Task'}
                </Badge>
              </Flex>
            )}
            {workflow.connectorIds && workflow.connectorIds.length > 0 && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.connectors', 'Connectors')}
                </Text>
                <Flex gap="1" wrap="wrap">
                  {workflow.connectorIds.map((id) => (
                    <Badge key={id} variant="outline" color="cyan" size="1">{id}</Badge>
                  ))}
                </Flex>
              </Flex>
            )}
            {workflow.toolNames && workflow.toolNames.length > 0 && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.tools', 'Tools')}
                </Text>
                <Flex gap="1" wrap="wrap">
                  {workflow.toolNames.map((name) => (
                    <Badge key={name} variant="outline" color="jade" size="1">{name}</Badge>
                  ))}
                </Flex>
              </Flex>
            )}
            {workflow.collectionIds && workflow.collectionIds.length > 0 && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.collections', 'Knowledge Bases')}
                </Text>
                <Flex gap="1" wrap="wrap">
                  {workflow.collectionIds.map((id) => (
                    <Badge key={id} variant="outline" color="amber" size="1">{id}</Badge>
                  ))}
                </Flex>
              </Flex>
            )}
            {workflow.maxTurns != null && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.maxTurns', 'Max Turns')}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>{workflow.maxTurns}</Text>
              </Flex>
            )}
            {workflow.timeoutSeconds != null && (
              <Flex align="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', minWidth: 120 }}>
                  {t('workflowsPage.timeout', 'Timeout')}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {workflow.timeoutSeconds >= 60
                    ? t('workflowsPage.timeoutMinutes', '{{m}}m', { m: Math.floor(workflow.timeoutSeconds / 60) })
                    : t('workflowsPage.timeoutSeconds', '{{s}}s', { s: workflow.timeoutSeconds })}
                </Text>
              </Flex>
            )}
          </Flex>
        </Flex>
      )}

      <WorkflowTriggersPanel
        workflowId={workflowId}
        triggers={triggers}
        onChanged={fetchAll}
      />

      <Flex direction="column" gap="2">
        <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workflowsPage.recentRuns', 'Recent Runs')}
        </Heading>
        {runs.length === 0 ? (
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {t('workflowsPage.detail.noRuns', "This workflow hasn't run yet.")}
          </Text>
        ) : (
          <Flex direction="column">
            {runs.map((run) => (
              <RunRow
                key={run.runId}
                run={run}
                selected={selectedRunId === run.runId}
                onSelect={(id) => setSelectedRunId((prev) => (prev === id ? null : id))}
                onAnswer={handleAnswerRun}
                workflowName={workflow.name}
                conversationId={workflow.conversationId}
              />
            ))}
          </Flex>
        )}
      </Flex>

      {selectedRunId ? (
        <>
          <Separator size="4" />
          <RunInspector workflowId={workflowId} runId={selectedRunId} ir={workflowIR} />
        </>
      ) : null}

      <ConfirmationDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title={t('workflowsPage.cancelConfirmTitle', 'Cancel this workflow?')}
        message={t(
          'workflowsPage.cancelConfirmMessage',
          'The workflow will stop running and its schedule will be disabled. You can still view its history afterward.'
        )}
        confirmLabel={t('workflowsPage.cancel', 'Cancel')}
        confirmVariant="danger"
        isLoading={actionPending}
        onConfirm={() => {
          void withAction(
            () => WorkflowsApi.cancelWorkflow(workflowId),
            t('workflowsPage.cancelSuccess', 'Workflow cancelled'),
            t('workflowsPage.cancelError', 'Failed to cancel workflow')
          ).finally(() => setConfirmCancel(false));
        }}
      />

      <ConfirmationDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('workflowsPage.deleteConfirmTitle', 'Delete this workflow?')}
        message={t(
          'workflowsPage.deleteConfirmMessage',
          'This permanently deletes the workflow and its schedule. This cannot be undone — consider Cancel instead if you might want its history later.'
        )}
        confirmLabel={t('workflowsPage.actions.delete', 'Delete')}
        confirmVariant="danger"
        isLoading={actionPending}
        onConfirm={handleDelete}
      />
    </Flex>
  );
}
