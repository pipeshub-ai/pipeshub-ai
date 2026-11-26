'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Callout, Dialog, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import {
  AGENT_CONFIG_MAX_BYTES,
  AgentConfigApi,
  modelDisplayName,
  readAgentExport,
  type ConfiguredModel,
  saveImportResult,
  type ImportPreview,
  type ReadExport,
} from '../config-transfer';

type Step =
  | { kind: 'choose'; refusal: string | null }
  | { kind: 'preview'; document: Record<string, unknown>; preview: ImportPreview; refusal: string | null };

/** The file's text; FileReader, so it reads the same in every browser the dashboard supports. */
const readText = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });

/** The server's own words on a refused import (Node's `{error: {message}}`), else null. */
function refusalMessage(error: unknown): string | null {
  const original = (error as { originalError?: { response?: { data?: { error?: { message?: unknown } } } } })?.originalError;
  const message = original?.response?.data?.error?.message;
  return typeof message === 'string' && message.trim() !== '' ? message : null;
}

/**
 * One label and its values: side by side from `sm`, stacked on a phone. The label's width is a width, not a
 * flex basis: in the stacked (column) direction a basis is a height, which opened a gap under every label.
 */
export function PreviewRow({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <Flex
      data-testid="import-preview-row"
      gap={{ initial: '1', sm: '3' }}
      direction={{ initial: 'column', sm: 'row' }}
      style={{ padding: 'var(--space-2) 0', borderBottom: '1px solid var(--gray-4)' }}
    >
      <Text size="2" weight="medium" style={{ flexShrink: 0, minWidth: 96, color: 'var(--slate-11)' }}>
        {label}
      </Text>
      <Text size="2" style={{ color: 'var(--slate-12)', minWidth: 0, overflowWrap: 'anywhere' }}>
        {values.join(', ')}
      </Text>
    </Flex>
  );
}

/**
 * "Import" beside "New agent" (C4.08): choose an `.agent.json` file, see
 * what it holds by name, and import it as a new agent of yours, shared with
 * no one. The builder then opens with what still needs choosing pinned at top.
 */
export function ImportAgentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<Step>({ kind: 'choose', refusal: null });
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [configured, setConfigured] = useState<ConfiguredModel[]>([]);

  useEffect(() => {
    if (open) setStep({ kind: 'choose', refusal: null });
  }, [open]);

  const previewing = step.kind === 'preview';
  useEffect(() => {
    if (!previewing) return;
    let live = true;
    void AgentConfigApi.configuredModels().then((models) => {
      if (live) setConfigured(models);
    });
    return () => {
      live = false;
    };
  }, [previewing]);

  const problemText = (read: Extract<ReadExport, { ok: false }>): string => {
    if (read.problem === 'newerSchema' && read.schemaVersion !== undefined) {
      return t('agentTransfer.import.newerSchema', { version: read.schemaVersion });
    }
    return read.problem === 'tooBig' ? t('agentTransfer.import.tooBig') : t('agentTransfer.import.notExport');
  };

  const choose = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > AGENT_CONFIG_MAX_BYTES) {
      setStep({ kind: 'choose', refusal: t('agentTransfer.import.tooBig') });
      return;
    }
    let read: ReadExport;
    try {
      read = readAgentExport(await readText(file));
    } catch {
      read = { ok: false, problem: 'notExport' };
    }
    if (read.ok === true) {
      setStep({ kind: 'preview', document: read.document, preview: read.preview, refusal: null });
    } else {
      setStep({ kind: 'choose', refusal: problemText(read as Extract<ReadExport, { ok: false }>) });
    }
  };

  const runImport = async () => {
    if (step.kind !== 'preview') return;
    setBusy(true);
    try {
      const result = await AgentConfigApi.importAgent(step.document);
      saveImportResult(result);
      onOpenChange(false);
      useMobileSidebarStore.getState().close();
      router.push(`/agents/edit?agentKey=${encodeURIComponent(result.agentKey)}&imported=1`);
    } catch (error) {
      setStep({ ...step, refusal: refusalMessage(error) ?? t('agentTransfer.import.failed') });
    } finally {
      setBusy(false);
    }
  };

  const preview = step.kind === 'preview' ? step.preview : null;
  const limits = preview
    ? [
        ...(preview.maxTurns !== null ? [t('agentTransfer.import.turns', { count: preview.maxTurns })] : []),
        ...(preview.maxCostUsd !== null ? [t('agentTransfer.import.costPerRun', { cost: `$${String(preview.maxCostUsd)}` })] : []),
      ]
    : [];
  const safety = preview?.gates
    ? [
        ...(preview.gates.planApproval ? [t('agentBuilder.gates.planApproval')] : []),
        ...preview.gates.checks.map((kind) => t(`agentBuilder.gates.kind.${kind}`)),
      ]
    : [];

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <Dialog.Content maxWidth="34rem" style={{ padding: 'var(--space-5)' }}>
        <Dialog.Title>
          {preview ? t('agentTransfer.import.previewTitle', { name: preview.name }) : t('agentTransfer.import.title')}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
          {preview
            ? preview.sourceVersion !== null
              ? t('agentTransfer.import.fromVersion', { version: preview.sourceVersion })
              : t('agentTransfer.import.fromDraft')
            : t('agentTransfer.import.choose')}
        </Dialog.Description>

        <Flex direction="column" gap="3" mt="3">
          {step.refusal ? (
            <Callout.Root color="red" variant="surface" size="1" role="alert">
              <Callout.Text>
                <Text weight="medium">{t('agentTransfer.import.refusedTitle')}</Text> {step.refusal}
              </Callout.Text>
            </Callout.Root>
          ) : null}

          {step.kind === 'choose' ? (
            <Box
              role="button"
              tabIndex={0}
              aria-label={t('agentTransfer.import.dropAria')}
              onClick={() => input.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') input.current?.click();
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                void choose(e.dataTransfer.files?.[0]);
              }}
              style={{
                border: `1px dashed ${dragging ? 'var(--accent-9)' : 'var(--gray-7)'}`,
                borderRadius: 'var(--radius-3)',
                background: dragging ? 'var(--accent-2)' : 'var(--gray-2)',
                padding: 'var(--space-6) var(--space-4)',
                textAlign: 'center',
                cursor: 'pointer',
                minHeight: 120,
              }}
            >
              <MaterialIcon name="upload_file" size={28} color="var(--slate-11)" />
              <Text size="2" as="div" style={{ marginTop: 'var(--space-2)', color: 'var(--slate-11)' }}>
                {t('agentTransfer.import.drop')}
              </Text>
              <input
                ref={input}
                type="file"
                accept=".json,application/json"
                aria-label={t('agentTransfer.import.fileAria')}
                style={{ display: 'none' }}
                onChange={(e) => {
                  void choose(e.target.files?.[0]);
                  e.target.value = '';
                }}
              />
            </Box>
          ) : (
            <Flex direction="column">
              <PreviewRow
                label={t('agentTransfer.import.models')}
                values={step.preview.models.map((model) => modelDisplayName(model, configured))}
              />
              <PreviewRow label={t('agentTransfer.import.actions')} values={[...step.preview.actions, ...step.preview.mcpServers]} />
              <PreviewRow label={t('agentTransfer.import.knowledge')} values={step.preview.knowledge} />
              <PreviewRow label={t('agentTransfer.import.skills')} values={step.preview.skills} />
              <PreviewRow label={t('agentTransfer.import.limits')} values={limits} />
              <PreviewRow label={t('agentBuilder.gates.title')} values={safety} />
              <Text size="1" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-3)' }}>
                {t('agentTransfer.import.newAgentNote')}
              </Text>
            </Flex>
          )}
        </Flex>

        <Flex justify="end" gap="2" mt="4" wrap="wrap">
          {step.kind === 'preview' ? (
            <Button type="button" variant="outline" color="gray" disabled={busy} onClick={() => setStep({ kind: 'choose', refusal: null })} style={{ minHeight: 36 }}>
              {t('agentTransfer.import.back')}
            </Button>
          ) : (
            <Dialog.Close>
              <Button type="button" variant="outline" color="gray" style={{ minHeight: 36 }}>
                {t('agentTransfer.import.cancel')}
              </Button>
            </Dialog.Close>
          )}
          <Button type="button" disabled={step.kind !== 'preview' || busy} onClick={() => void runImport()} style={{ minHeight: 36 }}>
            {busy ? t('agentTransfer.import.importing') : t('agentTransfer.import.import')}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
