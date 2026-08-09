'use client';

import React, { useCallback, useRef, useState } from 'react';
import { Box, Button, Flex, Heading, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkflowGraph } from './workflow-graph';
import { WorkflowEditor } from './workflow-editor';
import { isProcessedError } from '@/lib/api';
import { WorkflowsApi } from '../api';
import type { WorkflowVersionSummary } from '../api';
import type { WorkflowEditResult, WorkflowIR } from '../types';

const EMPTY_IR: WorkflowIR = { nodes: [], edges: [], entry_node_id: null };

type EditStep = 'idle' | 'generating' | 'review';

/** Prefers the server's message (409 conflict text, verifier errors) over a generic one. */
function errorMessage(err: unknown): string | null {
  if (isProcessedError(err)) return err.message;
  if (err instanceof Error) return err.message;
  return null;
}

interface WorkflowEditPanelProps {
  workflowId: string;
  currentSource: string;
  currentIR: WorkflowIR;
  onApply: (result: WorkflowEditResult, version: WorkflowVersionSummary) => void;
  onDiscard: () => void;
}

/**
 * Inline natural-language workflow editor.
 *
 * Flow:
 * 1. User types edit instructions (plain English).
 * 2. "Generate Changes" calls POST /{workflowId}/edit, which generates and
 *    verifies code but persists nothing.
 * 3. On success, shows side-by-side before/after code diff and graph preview.
 * 4. "Apply" POSTs the reviewed source to /versions/commit, which is what
 *    actually stores and pins it; "Discard" throws the proposal away.
 */
export function WorkflowEditPanel({
  workflowId,
  currentSource,
  currentIR,
  onApply,
  onDiscard,
}: WorkflowEditPanelProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<EditStep>('idle');
  const [instructions, setInstructions] = useState('');
  const [editResult, setEditResult] = useState<WorkflowEditResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);

  const handleGenerate = useCallback(async () => {
    const trimmed = instructions.trim();
    if (!trimmed) return;
    setStep('generating');
    setError(null);
    setEditResult(null);
    try {
      const result = await WorkflowsApi.editWorkflow(workflowId, trimmed);
      setEditResult(result);
      setStep('review');
    } catch (err: unknown) {
      setError(
        errorMessage(err) ??
          t('workflowsPage.editPanel.generateError', 'Code generation failed')
      );
      setStep('idle');
    }
  }, [workflowId, instructions, t]);

  const handleApply = useCallback(async () => {
    if (!editResult) return;
    setApplying(true);
    setError(null);
    try {
      const version = await WorkflowsApi.commitVersion(
        workflowId,
        editResult.source,
        editResult.baseVersionId
      );
      onApply(editResult, version);
    } catch (err: unknown) {
      setError(
        errorMessage(err) ??
          t('workflowsPage.editPanel.applyError', 'Could not save this version')
      );
    } finally {
      setApplying(false);
    }
  }, [editResult, onApply, workflowId, t]);

  const handleDiscard = useCallback(() => {
    setStep('idle');
    setInstructions('');
    setEditResult(null);
    setError(null);
    onDiscard();
  }, [onDiscard]);

  const newIR: WorkflowIR = editResult?.ir ?? EMPTY_IR;

  return (
    <Flex direction="column" gap="4" style={{ width: '100%' }}>
      <Flex align="center" justify="between" gap="3">
        <Flex align="center" gap="2">
          <MaterialIcon name="edit" size={16} color="var(--violet-9)" />
          <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workflowsPage.editPanel.title', 'Edit Workflow via Natural Language')}
          </Heading>
        </Flex>
        <Button size="1" variant="ghost" color="gray" onClick={handleDiscard}>
          <MaterialIcon name="close" size={14} />
          {t('workflowsPage.editPanel.close', 'Close')}
        </Button>
      </Flex>

      {/* Instructions input */}
      <Flex direction="column" gap="2">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('workflowsPage.editPanel.instructionsLabel', 'Describe the changes you want to make')}
        </Text>
        <TextArea
          ref={instructionsRef}
          size="2"
          placeholder={t(
            'workflowsPage.editPanel.instructionsPlaceholder',
            'e.g. "Add a step that sends a Slack notification when the workflow completes" or "Change the cron schedule to run at 9am UTC on weekdays"'
          )}
          value={instructions}
          disabled={step === 'generating'}
          onChange={(e) => setInstructions(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              void handleGenerate();
            }
          }}
          rows={4}
          style={{ resize: 'vertical', fontFamily: 'inherit' }}
        />
        <Flex align="center" justify="between" gap="2">
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {t('workflowsPage.editPanel.hint', 'Press ⌘↵ to generate')}
          </Text>
          <Button
            size="2"
            color="violet"
            disabled={!instructions.trim() || step === 'generating'}
            onClick={() => void handleGenerate()}
          >
            {step === 'generating' ? (
              <>
                <LottieLoader variant="loader" size={16} />
                {t('workflowsPage.editPanel.generating', 'Generating…')}
              </>
            ) : (
              <>
                <MaterialIcon name="auto_fix_high" size={16} />
                {t('workflowsPage.editPanel.generate', 'Generate Changes')}
              </>
            )}
          </Button>
        </Flex>
      </Flex>

      {error && (
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
            {error}
          </Text>
        </Flex>
      )}

      {step === 'generating' && (
        <Flex
          align="center"
          justify="center"
          gap="3"
          style={{
            padding: 'var(--space-6)',
            background: 'var(--violet-a2)',
            borderRadius: 'var(--radius-3)',
          }}
        >
          <LottieLoader variant="loader" size={32} />
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workflowsPage.editPanel.generatingDescription', 'Generating and verifying code…')}
          </Text>
        </Flex>
      )}

      {/* Review: side-by-side diff + graph */}
      {step === 'review' && editResult && (
        <Flex direction="column" gap="4">
          <Flex
            align="center"
            gap="2"
            style={{
              padding: 'var(--space-3)',
              background: 'var(--jade-a2)',
              borderRadius: 'var(--radius-3)',
              border: '1px solid var(--jade-a5)',
            }}
          >
            <MaterialIcon name="check_circle" size={16} color="var(--jade-9)" />
            <Text size="2" style={{ color: 'var(--jade-11)' }}>
              {t('workflowsPage.editPanel.reviewReady', 'New version generated — review the changes below before applying.')}
            </Text>
          </Flex>

          {/* Graph comparison */}
          <Flex gap="4" wrap="wrap">
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 280 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                {t('workflowsPage.editPanel.beforeGraph', 'Before')}
              </Text>
              <Box style={{ border: '1px solid var(--slate-5)', borderRadius: 'var(--radius-3)', overflow: 'hidden' }}>
                <WorkflowGraph ir={currentIR} height="280px" />
              </Box>
            </Flex>
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 280 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--jade-11)' }}>
                {t('workflowsPage.editPanel.afterGraph', 'After')}
              </Text>
              <Box style={{ border: '1px solid var(--jade-5)', borderRadius: 'var(--radius-3)', overflow: 'hidden' }}>
                <WorkflowGraph ir={newIR} height="280px" />
              </Box>
            </Flex>
          </Flex>

          {/* Code diff */}
          <Flex gap="4" wrap="wrap">
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 280 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                {t('workflowsPage.editPanel.beforeCode', 'Previous Code')}
              </Text>
              <Box style={{ border: '1px solid var(--slate-5)', borderRadius: 'var(--radius-3)', overflow: 'hidden' }}>
                <WorkflowEditor source={editResult.previousSource || currentSource} readOnly height="340px" />
              </Box>
            </Flex>
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 280 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--jade-11)' }}>
                {t('workflowsPage.editPanel.afterCode', 'New Code')}
              </Text>
              <Box style={{ border: '1px solid var(--jade-5)', borderRadius: 'var(--radius-3)', overflow: 'hidden' }}>
                <WorkflowEditor source={editResult.source} readOnly height="340px" />
              </Box>
            </Flex>
          </Flex>

          {/* Apply / Discard */}
          <Flex align="center" gap="3" justify="end">
            <Button size="2" variant="soft" color="gray" onClick={handleDiscard} disabled={applying}>
              <MaterialIcon name="undo" size={16} />
              {t('workflowsPage.editPanel.discard', 'Discard')}
            </Button>
            <Button size="2" color="jade" onClick={() => void handleApply()} disabled={applying}>
              {applying ? (
                <>
                  <LottieLoader variant="loader" size={16} />
                  {t('workflowsPage.editPanel.applying', 'Applying…')}
                </>
              ) : (
                <>
                  <MaterialIcon name="check" size={16} />
                  {t('workflowsPage.editPanel.apply', 'Apply Changes')}
                </>
              )}
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}
