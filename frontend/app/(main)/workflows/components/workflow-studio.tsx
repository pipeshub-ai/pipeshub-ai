'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Box, Button, Flex, Select, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkflowEditor } from './workflow-editor';
import { WorkflowGraph } from './workflow-graph';
import { WorkflowEditPanel } from './workflow-edit-panel';
import { isProcessedError } from '@/lib/api';
import { WorkflowsApi } from '../api';
import type { WorkflowEditResult, WorkflowIR } from '../types';
import type { WorkflowVersionSummary } from '../api';

type StudioTab = 'graph' | 'code';

function errorMessage(err: unknown): string | null {
  if (isProcessedError(err)) return err.message;
  if (err instanceof Error) return err.message;
  return null;
}

interface WorkflowStudioProps {
  workflowId: string;
  source: string;
  ir: WorkflowIR;
  versions?: WorkflowVersionSummary[];
  selectedVersionId?: string | null;
  onVersionChange?: (versionId: string) => void;
  /**
   * The version runs actually execute. Selecting anything else in the version
   * dropdown is only a preview until it is restored, so the Studio needs this
   * to tell the two apart.
   */
  currentVersionId?: string | null;
  readOnly?: boolean;
  /**
   * Called after a new version has been committed server-side, so the parent
   * can refresh its version list and pin. The Studio itself owns the commit —
   * the callback is notification, not persistence.
   */
  onCommitted?: (version: WorkflowVersionSummary) => void | Promise<void>;
  /** When true, open the edit panel immediately (e.g. from ?edit=true URL param). */
  autoOpenEdit?: boolean;
}

export function WorkflowStudio({
  workflowId,
  source,
  ir,
  versions = [],
  selectedVersionId,
  onVersionChange,
  currentVersionId,
  readOnly = true,
  onCommitted,
  autoOpenEdit = false,
}: WorkflowStudioProps) {
  const { t } = useTranslation();
  const [editedSource, setEditedSource] = useState(source);
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StudioTab>('graph');
  const [editOpen, setEditOpen] = useState(autoOpenEdit);
  const [currentSource, setCurrentSource] = useState(source);
  const [currentIR, setCurrentIR] = useState<WorkflowIR>(ir);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [revealLine, setRevealLine] = useState<{ line: number; nonce: number } | null>(null);

  // Sync when parent pushes a new version (e.g. after apply).
  useEffect(() => {
    setCurrentSource(source);
    setEditedSource(source);
    setCurrentIR(ir);
  }, [source, ir]);

  const afterCommit = useCallback(
    (version: WorkflowVersionSummary, committedSource: string) => {
      setCurrentSource(committedSource);
      setEditedSource(committedSource);
      if (version.ir) setCurrentIR(version.ir);
      setEditOpen(false);
      void onCommitted?.(version);
    },
    [onCommitted]
  );

  /** Persists whatever is currently in the code editor as a new version. */
  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const version = await WorkflowsApi.commitVersion(
        workflowId,
        editedSource,
        // The version the editor was opened on -- the server rejects the
        // commit if the workflow has moved on since.
        selectedVersionId ?? versions[0]?.versionId ?? null
      );
      afterCommit(version, editedSource);
    } catch (err) {
      setSaveError(
        errorMessage(err) ?? t('workflowsPage.studio.saveError', 'Could not save this version')
      );
    } finally {
      setSaving(false);
    }
  }, [workflowId, editedSource, selectedVersionId, versions, afterCommit, t]);

  /**
   * Re-pins an older version so runs go back to executing it. The dropdown on
   * its own only changes what is displayed, which meant a bad version could
   * be identified but not undone without pasting its source into a new commit.
   */
  const handleRestore = useCallback(async () => {
    if (!selectedVersionId) return;
    setRestoring(true);
    setSaveError(null);
    try {
      const version = await WorkflowsApi.activateVersion(workflowId, selectedVersionId);
      await onCommitted?.(version);
    } catch (err) {
      setSaveError(
        errorMessage(err) ??
          t('workflowsPage.studio.restoreError', 'Could not restore this version')
      );
    } finally {
      setRestoring(false);
    }
  }, [workflowId, selectedVersionId, onCommitted, t]);

  const handleEditApply = useCallback(
    (result: WorkflowEditResult, version: WorkflowVersionSummary) => {
      setCurrentIR(result.ir);
      afterCommit(version, result.source);
    },
    [afterCommit]
  );

  const handleEditDiscard = useCallback(() => {
    setEditOpen(false);
  }, []);

  /** Clicking a graph node jumps to the code that produced it. */
  const handleNodeClick = useCallback(
    (nodeId: string, sourceStart: number | null | undefined) => {
      setSelectedNodeId(nodeId);
      if (sourceStart == null) return;
      setActiveTab('code');
      setRevealLine((prev) => ({ line: sourceStart, nonce: (prev?.nonce ?? 0) + 1 }));
    },
    []
  );

  const hasVersions = versions.length > 0;
  const isViewingSupersededVersion =
    !readOnly &&
    !!selectedVersionId &&
    !!currentVersionId &&
    selectedVersionId !== currentVersionId;
  const hasSource = !!currentSource;
  const hasGraph = currentIR.nodes.length > 0;
  const isDirty = editedSource !== currentSource;

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      {/* Header toolbar */}
      <Flex justify="between" align="center" wrap="wrap" gap="2">
        <Flex gap="2" align="center">
          <Button
            size="1"
            variant={activeTab === 'graph' ? 'solid' : 'soft'}
            onClick={() => setActiveTab('graph')}
          >
            <MaterialIcon name="account_tree" size={14} />
            {t('workflowsPage.studio.graphTab', 'Graph')}
          </Button>
          <Button
            size="1"
            variant={activeTab === 'code' ? 'solid' : 'soft'}
            onClick={() => setActiveTab('code')}
          >
            <MaterialIcon name="code" size={14} />
            {t('workflowsPage.studio.codeTab', 'Code')}
          </Button>

          {/* Version selector */}
          {hasVersions && onVersionChange && (
            <Select.Root
              size="1"
              value={selectedVersionId ?? versions[0]?.versionId ?? ''}
              onValueChange={onVersionChange}
            >
              <Select.Trigger
                variant="soft"
                style={{ minWidth: 120 }}
                placeholder={t('workflowsPage.studio.versionPlaceholder', 'Version')}
              />
              <Select.Content>
                {versions.map((v, idx) => (
                  <Select.Item key={v.versionId} value={v.versionId}>
                    {idx === 0
                      ? t('workflowsPage.studio.latestVersion', 'Latest ({{hash}})', {
                          hash: v.contentHash.slice(0, 7),
                        })
                      : t('workflowsPage.studio.version', 'v{{n}} ({{hash}})', {
                          n: v.versionNumber,
                          hash: v.contentHash.slice(0, 7),
                        })}
                  </Select.Item>
                ))}
              </Select.Content>
            </Select.Root>
          )}

          {isViewingSupersededVersion && (
            <>
              <Badge size="1" variant="soft" color="amber">
                {t('workflowsPage.studio.notActive', 'Not the running version')}
              </Badge>
              <Button
                size="1"
                variant="soft"
                color="amber"
                disabled={restoring}
                onClick={() => void handleRestore()}
              >
                <MaterialIcon name="history" size={14} />
                {restoring
                  ? t('workflowsPage.studio.restoring', 'Restoring…')
                  : t('workflowsPage.studio.restore', 'Restore this version')}
              </Button>
            </>
          )}
        </Flex>

        <Flex gap="2" align="center">
          {/* Edit button (only when not already editing) */}
          {!editOpen && (
            <Button
              size="1"
              variant="soft"
              color="violet"
              onClick={() => setEditOpen(true)}
            >
              <MaterialIcon name="edit" size={14} />
              {t('workflowsPage.studio.edit', 'Edit')}
            </Button>
          )}

          {!readOnly && (
            <Button
              size="2"
              color="violet"
              onClick={() => void handleSave()}
              disabled={saving || !isDirty}
            >
              {saving
                ? t('workflowsPage.studio.saving', 'Saving…')
                : t('workflowsPage.studio.saveVersion', 'Save Version')}
            </Button>
          )}
        </Flex>
      </Flex>

      {saveError && (
        <Flex
          align="center"
          gap="2"
          style={{
            padding: 'var(--space-3)',
            background: 'var(--red-a2)',
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--red-a5)',
          }}
        >
          <MaterialIcon name="error_outline" size={16} color="var(--red-9)" />
          <Text size="2" style={{ color: 'var(--red-11)' }}>
            {saveError}
          </Text>
        </Flex>
      )}

      {/* Edit panel (slides in above the view) */}
      {editOpen && (
        <Box
          style={{
            padding: 'var(--space-4)',
            background: 'var(--slate-1)',
            borderRadius: 'var(--radius-4)',
            border: '1px solid var(--violet-a6)',
          }}
        >
          <WorkflowEditPanel
            workflowId={workflowId}
            currentSource={currentSource}
            currentIR={currentIR}
            onApply={handleEditApply}
            onDiscard={handleEditDiscard}
          />
        </Box>
      )}

      {/* Main content */}
      <Box style={{ minHeight: 400 }}>
        {!hasSource && !hasGraph ? (
          <Flex
            align="center"
            justify="center"
            direction="column"
            gap="3"
            style={{
              height: 300,
              border: '1px dashed var(--slate-6)',
              borderRadius: 'var(--radius-3)',
            }}
          >
            <MaterialIcon name="code_off" size={32} color="var(--slate-7)" />
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {t('workflowsPage.studio.noCode', 'No code generated yet.')}
            </Text>
            <Button size="1" variant="soft" color="violet" onClick={() => setEditOpen(true)}>
              <MaterialIcon name="auto_fix_high" size={14} />
              {t('workflowsPage.studio.generateCode', 'Generate Code')}
            </Button>
          </Flex>
        ) : activeTab === 'graph' ? (
          <>
            <WorkflowGraph
              ir={currentIR}
              height="500px"
              selectedNodeId={selectedNodeId}
              onNodeClick={handleNodeClick}
            />
            {!hasGraph && (
              <Flex align="center" justify="center" gap="2" style={{ marginTop: 'var(--space-2)' }}>
                <Badge variant="soft" color="gray" size="1">
                  {t('workflowsPage.studio.noIr', 'No graph available')}
                </Badge>
              </Flex>
            )}
          </>
        ) : !hasSource && hasVersions ? (
          // The version exists (and its graph rendered on the Graph tab) but
          // the source body itself failed to fetch -- distinct from "no code
          // yet" so the user knows there's something to retry, not generate.
          <Flex
            align="center"
            justify="center"
            direction="column"
            gap="3"
            style={{
              height: 300,
              border: '1px dashed var(--amber-6)',
              borderRadius: 'var(--radius-3)',
            }}
          >
            <MaterialIcon name="error_outline" size={32} color="var(--amber-9)" />
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {t('workflowsPage.studio.sourceUnavailable', 'Source code could not be loaded for this version.')}
            </Text>
          </Flex>
        ) : (
          <WorkflowEditor
            source={editedSource}
            readOnly={readOnly}
            onChange={setEditedSource}
            height="500px"
            revealLine={revealLine}
          />
        )}
      </Box>
    </Flex>
  );
}
