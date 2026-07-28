'use client';

import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Dialog, Flex, Tabs, Text, TextField, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { FormField } from '../../../components';
import { toast } from '@/lib/store/toast-store';
import { useSkillsStore } from '../store';
import { SkillsApi } from '../api';
import type { ImportPreview, ImportSourceTab } from '../types';

// ========================================
// Component
// ========================================

export function SkillImportDialog() {
  const { t } = useTranslation();
  const { importDialogOpen, closeImportDialog, setSkills } = useSkillsStore();
  const [tab, setTab] = useState<ImportSourceTab>('npm');
  const [npmCommand, setNpmCommand] = useState('');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setNpmCommand('');
    setUrl('');
    setFile(null);
    setPreview(null);
    setError(null);
    setTab('npm');
  }, []);

  const handleClose = useCallback(() => {
    closeImportDialog();
    reset();
  }, [closeImportDialog, reset]);

  const handlePreview = useCallback(async () => {
    setError(null);
    setPreviewing(true);
    try {
      let result: ImportPreview;
      if (tab === 'npm') {
        if (!npmCommand.trim()) return;
        result = await SkillsApi.previewNpmImport(npmCommand.trim());
      } else if (tab === 'url') {
        if (!url.trim()) return;
        result = await SkillsApi.previewUrlImport(url.trim());
      } else {
        if (!file) return;
        result = await SkillsApi.previewUploadImport(file);
      }
      setPreview(result);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || t('workspace.skills.import.previewError'));
      setPreview(null);
    } finally {
      setPreviewing(false);
    }
  }, [tab, npmCommand, url, file, t]);

  const handleFinalize = useCallback(async () => {
    if (!preview || finalizing) return;
    setFinalizing(true);
    try {
      await SkillsApi.finalizeImport(preview);
      toast.success(t('workspace.skills.toasts.imported', { name: preview.name }));
      const list = await SkillsApi.listSkills();
      setSkills(list);
      handleClose();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t('workspace.skills.toasts.importError'));
    } finally {
      setFinalizing(false);
    }
  }, [preview, finalizing, setSkills, handleClose, t]);

  const canPreview =
    (tab === 'npm' && npmCommand.trim().length > 0) ||
    (tab === 'url' && url.trim().length > 0) ||
    (tab === 'upload' && !!file);

  return (
    <Dialog.Root open={importDialogOpen} onOpenChange={(v) => !v && handleClose()}>
      <Dialog.Content style={{ maxWidth: '32rem' }}>
        <Dialog.Title>{t('workspace.skills.import.title')}</Dialog.Title>
        <VisuallyHidden>
          <Dialog.Description>{t('workspace.skills.import.description')}</Dialog.Description>
        </VisuallyHidden>

        <Tabs.Root
          value={tab}
          onValueChange={(v) => {
            setTab(v as ImportSourceTab);
            setPreview(null);
            setError(null);
          }}
        >
          <Tabs.List style={{ marginTop: 8, marginBottom: 16 }}>
            <Tabs.Trigger value="npm">{t('workspace.skills.import.tabNpm')}</Tabs.Trigger>
            <Tabs.Trigger value="url">{t('workspace.skills.import.tabUrl')}</Tabs.Trigger>
            <Tabs.Trigger value="upload">{t('workspace.skills.import.tabUpload')}</Tabs.Trigger>
          </Tabs.List>

          <Tabs.Content value="npm">
            <FormField label={t('workspace.skills.import.npmLabel')}>
              <TextField.Root
                size="2"
                placeholder="npm install @acme/pdf-skill  ·  or just: pdf-skill"
                value={npmCommand}
                onChange={(e) => setNpmCommand(e.target.value)}
              />
            </FormField>
            <Text size="1" style={{ color: 'var(--gray-9)', marginTop: 4, display: 'block' }}>
              {t('workspace.skills.import.npmHint')}
            </Text>
          </Tabs.Content>

          <Tabs.Content value="url">
            <FormField label={t('workspace.skills.import.urlLabel')}>
              <TextField.Root
                size="2"
                placeholder="https://example.com/my-skill.zip"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </FormField>
          </Tabs.Content>

          <Tabs.Content value="upload">
            <FormField label={t('workspace.skills.import.uploadLabel')}>
              <Flex direction="column" gap="2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip,.tar,.tar.gz,.tgz"
                  style={{ display: 'none' }}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
                <Button variant="outline" color="gray" size="2" onClick={() => fileInputRef.current?.click()} style={{ cursor: 'pointer', justifyContent: 'flex-start' }}>
                  <MaterialIcon name="upload_file" size={16} color="var(--slate-11)" />
                  {file ? file.name : t('workspace.skills.import.chooseFile')}
                </Button>
              </Flex>
            </FormField>
          </Tabs.Content>
        </Tabs.Root>

        {!preview && (
          <Flex justify="end" style={{ marginTop: 8 }}>
            <LoadingButton variant="solid" size="2" onClick={handlePreview} disabled={!canPreview} loading={previewing}>
              {t('workspace.skills.import.preview')}
            </LoadingButton>
          </Flex>
        )}

        {error && (
          <Box style={{ marginTop: 12, padding: 'var(--space-2)', background: 'var(--red-a2)', borderRadius: 'var(--radius-2)' }}>
            <Text size="1" style={{ color: 'var(--red-a11)' }}>{error}</Text>
          </Box>
        )}

        {preview && (
          <ImportPreviewCard preview={preview} onDiscard={() => setPreview(null)} />
        )}

        <Flex justify="end" gap="2" style={{ marginTop: 16 }}>
          <Button variant="outline" color="gray" size="2" onClick={handleClose} disabled={finalizing} style={{ cursor: 'pointer' }}>
            {t('action.cancel')}
          </Button>
          {preview && (
            <LoadingButton variant="solid" size="2" onClick={handleFinalize} loading={finalizing}>
              {t('workspace.skills.import.confirmImport')}
            </LoadingButton>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

// ========================================
// Preview card
// ========================================

function ImportPreviewCard({ preview, onDiscard }: { preview: ImportPreview; onDiscard: () => void }) {
  const { t } = useTranslation();
  const resourceCount = Object.keys(preview.resources).length;

  return (
    <Flex
      direction="column"
      gap="2"
      style={{ marginTop: 12, padding: 'var(--space-3)', border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-2)', background: 'var(--gray-a2)' }}
    >
      <Flex align="center" justify="between">
        <Flex align="center" gap="2">
          <MaterialIcon name="psychology" size={16} color="var(--gray-10)" />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{preview.name}</Text>
          <Badge size="1" color="gray">v{preview.version}</Badge>
        </Flex>
        <Button variant="ghost" color="gray" size="1" onClick={onDiscard} style={{ cursor: 'pointer' }}>
          <MaterialIcon name="close" size={14} color="var(--gray-10)" />
        </Button>
      </Flex>
      <Text size="1" style={{ color: 'var(--gray-11)' }}>{preview.description}</Text>
      <Text size="1" style={{ color: 'var(--gray-9)' }}>
        {t('workspace.skills.import.source', { source: preview.sourceLabel })} · {t('workspace.skills.import.resourceCount', { count: resourceCount })}
      </Text>

      {preview.skippedBinaryResources.length > 0 && (
        <Text size="1" style={{ color: 'var(--amber-a11)' }}>
          {t('workspace.skills.import.skippedBinary', { count: preview.skippedBinaryResources.length })}
        </Text>
      )}

      {preview.warnings.length > 0 && (
        <Flex direction="column" gap="1" style={{ marginTop: 4 }}>
          {preview.warnings.map((w, i) => (
            <Text key={i} size="1" style={{ color: 'var(--amber-a11)' }}>
              ⚠ {w}
            </Text>
          ))}
        </Flex>
      )}
    </Flex>
  );
}
