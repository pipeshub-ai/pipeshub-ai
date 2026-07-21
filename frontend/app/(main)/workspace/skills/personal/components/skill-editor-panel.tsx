'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Select, Tabs, Text, TextArea, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import {
  WorkspaceRightPanel,
  FormField,
  TagInput,
  ConfirmationDialog,
  DestructiveTypedConfirmationDialog,
} from '../../../components';
import type { TagItem } from '../../../components';
import { useWorkspaceDrawerNestedModalHost } from '../../../components/workspace-right-panel';
import { toast } from '@/lib/store/toast-store';
import { useSkillsStore } from '../store';
import { SkillsApi } from '../api';
import type { EditorTab, Skill, SkillUsage, SkillVersionSummary, SkillWritePayload } from '../types';
import { MarkdownEditor } from './markdown-editor';

const EMPTY_SKILL: Skill = {
  name: '',
  description: '',
  version: '1.0.0',
  category: null,
  subcategory: null,
  tags: [],
  status: 'active',
  source: 'manual',
  license: null,
  compatibility: null,
  allowedTools: null,
  related: [],
  requires: [],
  concepts: [],
  deprecatedReason: null,
  replacedBy: null,
  createdAt: null,
  updatedAt: null,
  packName: null,
  packVersion: null,
  body: '',
  resources: {},
};

const toTagItems = (values: string[]): TagItem[] => values.map((v) => ({ id: v, value: v }));
const fromTagItems = (items: TagItem[]): string[] => items.map((i) => i.value.trim()).filter(Boolean);

// ========================================
// Component
// ========================================

export function SkillEditorPanel() {
  const { t } = useTranslation();
  const { editorOpen, editingSkillName, editorTab, editorPrefill, closeEditor, setEditorTab, setSkills } = useSkillsStore();
  const isEditMode = !!editingSkillName;

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [skill, setSkill] = useState<Skill>(EMPTY_SKILL);
  const [name, setName] = useState('');

  const nestedHost = useWorkspaceDrawerNestedModalHost(editorOpen);

  const refreshCatalog = useCallback(async () => {
    try {
      const list = await SkillsApi.listSkills();
      setSkills(list);
    } catch {
      // Non-fatal — the panel already reflects the latest save.
    }
  }, [setSkills]);

  useEffect(() => {
    if (!editorOpen) return;
    if (!isEditMode) {
      if (editorPrefill) {
        setName(editorPrefill.prefillName ?? '');
        setSkill({
          ...EMPTY_SKILL,
          description: editorPrefill.prefillDescription ?? '',
          body: editorPrefill.prefillBody ?? '',
          category: editorPrefill.prefillCategory ?? null,
          subcategory: editorPrefill.prefillSubcategory ?? null,
          tags: editorPrefill.prefillTags ?? [],
        });
      } else {
        setSkill(EMPTY_SKILL);
        setName('');
      }
      return;
    }
    setLoading(true);
    SkillsApi.getSkill(editingSkillName!)
      .then((data) => {
        setSkill(data);
        setName(data.name);
      })
      .catch(() => toast.error(t('workspace.skills.toasts.loadError')))
      .finally(() => setLoading(false));
  }, [editorOpen, isEditMode, editingSkillName, editorPrefill, t]);

  const isReadOnly = skill.source === 'builtin';
  const isValid = name.trim().length > 0 && skill.description.trim().length > 0 && skill.body.trim().length > 0;

  const handleSave = useCallback(async () => {
    if (!isValid || saving || isReadOnly) return;
    setSaving(true);
    try {
      const payload: SkillWritePayload = {
        name: isEditMode ? undefined : name.trim(),
        description: skill.description,
        body: skill.body,
        category: skill.category,
        subcategory: skill.subcategory,
        tags: skill.tags,
        license: skill.license,
        compatibility: skill.compatibility,
        allowedTools: skill.allowedTools,
        related: skill.related,
        requires: skill.requires,
        concepts: skill.concepts,
      };
      if (isEditMode) {
        await SkillsApi.updateSkill(editingSkillName!, payload);
        toast.success(t('workspace.skills.toasts.updated', { name }));
      } else {
        await SkillsApi.createSkill(payload);
        toast.success(t('workspace.skills.toasts.created', { name }));
        if (editorPrefill?.candidateId) {
          await SkillsApi.approveCandidate(editorPrefill.candidateId).catch(() => {});
        }
      }
      await refreshCatalog();
      closeEditor();
    } catch {
      toast.error(isEditMode ? t('workspace.skills.toasts.updateError') : t('workspace.skills.toasts.createError'));
    } finally {
      setSaving(false);
    }
  }, [isValid, saving, isReadOnly, isEditMode, name, skill, editingSkillName, editorPrefill, refreshCatalog, closeEditor, t]);

  const headerIcon = <MaterialIcon name="psychology" size={20} color="var(--slate-12)" />;

  const missingFields: string[] = [];
  if (!name.trim()) missingFields.push(t('workspace.skills.form.name'));
  if (!skill.description.trim()) missingFields.push(t('workspace.skills.form.description'));
  if (!skill.body.trim()) missingFields.push(t('workspace.skills.tabs.content'));

  return (
    <WorkspaceRightPanel
      open={editorOpen}
      onOpenChange={(open) => { if (!open) closeEditor(); }}
      title={isEditMode ? name : editorPrefill?.candidateId ? t('workspace.skills.editorPanel.reviewTitle') : t('workspace.skills.editorPanel.createTitle')}
      icon={headerIcon}
      hideFooter={isReadOnly}
      primaryLabel={isEditMode ? t('action.save') : t('action.create')}
      secondaryLabel={t('action.cancel')}
      primaryDisabled={!isValid}
      primaryLoading={saving}
      onPrimaryClick={handleSave}
      onSecondaryClick={closeEditor}
    >
      {loading ? (
        <Flex align="center" justify="center" style={{ height: 200 }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.loading')}</Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="4" style={{ height: '100%' }}>
          {isReadOnly && (
            <Flex align="center" gap="2" style={{ padding: 'var(--space-2)', background: 'var(--gray-a2)', borderRadius: 'var(--radius-2)' }}>
              <MaterialIcon name="info" size={16} color="var(--gray-10)" />
              <Text size="1" style={{ color: 'var(--gray-11)' }}>{t('workspace.skills.builtinReadOnly')}</Text>
            </Flex>
          )}

          {/* Essential fields - always visible at top */}
          {!isEditMode && (
            <FormField label={t('workspace.skills.form.name')} required>
              <TextField.Root
                size="2"
                placeholder={t('workspace.skills.form.namePlaceholder')}
                value={name}
                onChange={(e) => setName(e.target.value.trim())}
              />
            </FormField>
          )}

          <FormField label={t('workspace.skills.form.description')} required>
            <TextArea
              size="2"
              rows={2}
              placeholder={t('workspace.skills.form.descriptionPlaceholder')}
              value={skill.description}
              onChange={(e) => setSkill((s) => ({ ...s, description: e.target.value }))}
              disabled={isReadOnly}
              style={{ resize: 'vertical' }}
            />
          </FormField>

          {/* Validation hint */}
          {!isValid && missingFields.length > 0 && (
            <Flex align="center" gap="2" style={{ padding: '6px 10px', background: 'var(--amber-a2)', borderRadius: 'var(--radius-2)', border: '1px solid var(--amber-a4)' }}>
              <MaterialIcon name="warning" size={14} color="var(--amber-a11)" />
              <Text size="1" style={{ color: 'var(--amber-a11)' }}>
                {t('workspace.skills.editorPanel.requiredHint', { fields: missingFields.join(', ') })}
              </Text>
            </Flex>
          )}

          <Tabs.Root value={editorTab} onValueChange={(v) => setEditorTab(v as EditorTab)} style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <Tabs.List style={{ borderBottom: '1px solid var(--olive-3)', marginBottom: 'var(--space-3)' }}>
              <Tabs.Trigger value="content">
                {t('workspace.skills.tabs.content')}
                {!skill.body.trim() && <Text as="span" style={{ color: 'var(--red-a11)', marginLeft: 2 }}>*</Text>}
              </Tabs.Trigger>
              <Tabs.Trigger value="metadata">{t('workspace.skills.tabs.metadata')}</Tabs.Trigger>
              <Tabs.Trigger value="resources" disabled={!isEditMode}>{t('workspace.skills.tabs.resources')}</Tabs.Trigger>
              <Tabs.Trigger value="versions" disabled={!isEditMode}>{t('workspace.skills.tabs.versions')}</Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="content" style={{ flex: 1, minHeight: 0 }}>
              <Flex direction="column" gap="1" style={{ flex: 1 }}>
                <Text size="1" style={{ color: 'var(--gray-9)', marginBottom: 4 }}>
                  {t('workspace.skills.editorPanel.bodyHint')}
                </Text>
                <MarkdownEditor
                  value={skill.body}
                  onChange={(body) => setSkill((s) => ({ ...s, body }))}
                  editable={!isReadOnly}
                  placeholder={t('workspace.skills.form.bodyPlaceholder')}
                  minHeight={220}
                />
              </Flex>
            </Tabs.Content>

            <Tabs.Content value="metadata">
              <MetadataTab skill={skill} setSkill={setSkill} readOnly={isReadOnly} />
              {isEditMode && !isReadOnly && (
                <DangerZone skillName={editingSkillName!} onDone={async () => { await refreshCatalog(); closeEditor(); }} nestedHost={nestedHost} />
              )}
            </Tabs.Content>

            <Tabs.Content value="resources">
              {isEditMode && <ResourcesTab skillName={editingSkillName!} resources={skill.resources} readOnly={isReadOnly} onRefresh={async () => setSkill(await SkillsApi.getSkill(editingSkillName!))} />}
            </Tabs.Content>

            <Tabs.Content value="versions">
              {isEditMode && (
                <VersionsTab
                  skillName={editingSkillName!}
                  onRolledBack={async () => {
                    const fresh = await SkillsApi.getSkill(editingSkillName!);
                    setSkill(fresh);
                    await refreshCatalog();
                  }}
                  nestedHost={nestedHost}
                />
              )}
            </Tabs.Content>
          </Tabs.Root>
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}

// ========================================
// Metadata tab
// ========================================

function MetadataTab({
  skill,
  setSkill,
  readOnly,
}: {
  skill: Skill;
  setSkill: React.Dispatch<React.SetStateAction<Skill>>;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="4">
      <Flex gap="3">
        <FormField label={t('workspace.skills.form.category')} optional>
          <TextField.Root
            size="2"
            value={skill.category ?? ''}
            onChange={(e) => setSkill((s) => ({ ...s, category: e.target.value || null }))}
            disabled={readOnly}
          />
        </FormField>
        <FormField label={t('workspace.skills.form.subcategory')} optional>
          <TextField.Root
            size="2"
            value={skill.subcategory ?? ''}
            onChange={(e) => setSkill((s) => ({ ...s, subcategory: e.target.value || null }))}
            disabled={readOnly}
          />
        </FormField>
      </Flex>

      <FormField label={t('workspace.skills.form.tags')} optional>
        <TagInput
          tags={toTagItems(skill.tags)}
          onTagsChange={readOnly ? undefined : (items) => setSkill((s) => ({ ...s, tags: fromTagItems(items) }))}
          placeholder={t('workspace.skills.form.tagsPlaceholder')}
          disabled={readOnly}
        />
      </FormField>

      <FormField label={t('workspace.skills.form.related')} optional>
        <TagInput
          tags={toTagItems(skill.related)}
          onTagsChange={readOnly ? undefined : (items) => setSkill((s) => ({ ...s, related: fromTagItems(items) }))}
          placeholder={t('workspace.skills.form.relatedPlaceholder')}
          disabled={readOnly}
        />
      </FormField>

      <FormField label={t('workspace.skills.form.requires')} optional>
        <TagInput
          tags={toTagItems(skill.requires)}
          onTagsChange={readOnly ? undefined : (items) => setSkill((s) => ({ ...s, requires: fromTagItems(items) }))}
          placeholder={t('workspace.skills.form.requiresPlaceholder')}
          disabled={readOnly}
        />
      </FormField>

      <Flex gap="3">
        <FormField label={t('workspace.skills.form.license')} optional>
          <TextField.Root
            size="2"
            value={skill.license ?? ''}
            onChange={(e) => setSkill((s) => ({ ...s, license: e.target.value || null }))}
            disabled={readOnly}
          />
        </FormField>
        <FormField label={t('workspace.skills.form.compatibility')} optional>
          <TextField.Root
            size="2"
            value={skill.compatibility ?? ''}
            onChange={(e) => setSkill((s) => ({ ...s, compatibility: e.target.value || null }))}
            disabled={readOnly}
          />
        </FormField>
      </Flex>

      {skill.status === 'deprecated' && (
        <Flex align="center" gap="2">
          <Badge color="gray">{t('workspace.skills.status.deprecated')}</Badge>
          {skill.deprecatedReason && <Text size="1" style={{ color: 'var(--gray-10)' }}>{skill.deprecatedReason}</Text>}
          {skill.replacedBy && (
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.skills.form.replacedBy_withName', { name: skill.replacedBy })}
            </Text>
          )}
        </Flex>
      )}
    </Flex>
  );
}

// ========================================
// Danger zone: deprecate / delete
// ========================================

function DangerZone({
  skillName,
  onDone,
  nestedHost,
}: {
  skillName: string;
  onDone: () => void;
  nestedHost: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const { skills } = useSkillsStore();
  const [showDeprecate, setShowDeprecate] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [reason, setReason] = useState('');
  const [replacedBy, setReplacedBy] = useState('');
  const [usage, setUsage] = useState<SkillUsage | null>(null);
  const [busy, setBusy] = useState(false);

  const otherSkills = skills.filter((s) => s.name !== skillName);

  const handleDeprecate = useCallback(async () => {
    if (!reason.trim() || busy) return;
    setBusy(true);
    try {
      await SkillsApi.deprecateSkill(skillName, reason.trim(), replacedBy || null);
      toast.success(t('workspace.skills.toasts.deprecated', { name: skillName }));
      setShowDeprecate(false);
      onDone();
    } catch {
      toast.error(t('workspace.skills.toasts.deprecateError'));
    } finally {
      setBusy(false);
    }
  }, [reason, replacedBy, busy, skillName, onDone, t]);

  const openDelete = useCallback(async () => {
    setShowDelete(true);
    try {
      setUsage(await SkillsApi.getUsage(skillName));
    } catch {
      setUsage(null);
    }
  }, [skillName]);

  const handleDelete = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const detach = !!usage?.usedByAgents?.length;
      await SkillsApi.deleteSkill(skillName, detach);
      toast.success(t('workspace.skills.toasts.deleted', { name: skillName }));
      setShowDelete(false);
      onDone();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
      const message = typeof detail === 'string' ? detail : detail?.message;
      toast.error(message || t('workspace.skills.toasts.deleteError'));
    } finally {
      setBusy(false);
    }
  }, [busy, usage, skillName, onDone, t]);

  const requiredByOthers = !!usage?.requiredBySkills?.length;

  return (
    <Flex direction="column" gap="2" style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--olive-3)' }}>
      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{t('workspace.skills.dangerZone.title')}</Text>
      <Flex gap="2">
        <Button variant="outline" color="amber" size="2" onClick={() => setShowDeprecate(true)} style={{ cursor: 'pointer' }}>
          <MaterialIcon name="archive" size={16} color="var(--amber-a11)" />
          {t('workspace.skills.dangerZone.deprecate')}
        </Button>
        <Button variant="outline" color="red" size="2" onClick={openDelete} style={{ cursor: 'pointer' }}>
          <MaterialIcon name="delete" size={16} color="var(--red-a11)" />
          {t('workspace.skills.dangerZone.delete')}
        </Button>
      </Flex>

      <ConfirmationDialog
        open={showDeprecate}
        onOpenChange={setShowDeprecate}
        title={t('workspace.skills.dangerZone.deprecateTitle', { name: skillName })}
        confirmLabel={t('workspace.skills.dangerZone.deprecate')}
        confirmVariant="danger"
        isLoading={busy}
        onConfirm={handleDeprecate}
        container={nestedHost}
        message={
          <Flex direction="column" gap="3">
            <FormField label={t('workspace.skills.form.deprecateReason')} required>
              <TextArea size="2" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </FormField>
            <FormField label={t('workspace.skills.form.replacedBy')} optional>
              <Select.Root value={replacedBy || '__none__'} onValueChange={(v) => setReplacedBy(v === '__none__' ? '' : v)}>
                <Select.Trigger style={{ width: '100%' }} />
                <Select.Content>
                  <Select.Item value="__none__">{t('workspace.skills.form.noReplacement')}</Select.Item>
                  {otherSkills.map((s) => (
                    <Select.Item key={s.name} value={s.name}>{s.name}</Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            </FormField>
          </Flex>
        }
      />

      {requiredByOthers ? (
        <ConfirmationDialog
          open={showDelete}
          onOpenChange={setShowDelete}
          title={t('workspace.skills.dangerZone.cannotDeleteTitle')}
          confirmLabel=""
          hideConfirm
          onConfirm={() => {}}
          container={nestedHost}
          message={
            <Text size="2">
              {t('workspace.skills.dangerZone.requiredBy', { names: usage?.requiredBySkills.join(', ') })}
            </Text>
          }
        />
      ) : (
        <DestructiveTypedConfirmationDialog
          open={showDelete}
          onOpenChange={setShowDelete}
          heading={t('workspace.skills.dangerZone.deleteTitle', { name: skillName })}
          body={
            <>
              <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
                {t('workspace.skills.dangerZone.deleteBody')}
              </Text>
              {!!usage?.usedByAgents?.length && (
                <Text size="2" style={{ color: 'var(--amber-a11)', lineHeight: '20px' }}>
                  {t('workspace.skills.dangerZone.usedByAgents', {
                    names: usage.usedByAgents.map((a) => a.name).join(', '),
                  })}
                </Text>
              )}
            </>
          }
          confirmationKeyword={skillName}
          confirmInputLabel={t('workspace.skills.form.typeNameToConfirm', { name: skillName })}
          primaryButtonText={t('workspace.skills.dangerZone.delete')}
          cancelLabel={t('action.cancel')}
          onConfirm={handleDelete}
          isLoading={busy}
        />
      )}
    </Flex>
  );
}

// ========================================
// Resources tab
// ========================================

function ResourcesTab({
  skillName,
  resources,
  readOnly,
  onRefresh,
}: {
  skillName: string;
  resources: Record<string, string[]>;
  readOnly: boolean;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [newPath, setNewPath] = useState('');
  const [newContent, setNewContent] = useState('');
  const [busy, setBusy] = useState(false);
  const allPaths = Object.values(resources).flat();

  const handleAdd = useCallback(async () => {
    if (!newPath.trim() || busy) return;
    setBusy(true);
    try {
      await SkillsApi.writeResource(skillName, newPath.trim(), newContent);
      setNewPath('');
      setNewContent('');
      await onRefresh();
      toast.success(t('workspace.skills.toasts.resourceAdded'));
    } catch {
      toast.error(t('workspace.skills.toasts.resourceError'));
    } finally {
      setBusy(false);
    }
  }, [newPath, newContent, busy, skillName, onRefresh, t]);

  const handleRemove = useCallback(async (path: string) => {
    setBusy(true);
    try {
      await SkillsApi.removeResource(skillName, path);
      await onRefresh();
    } catch {
      toast.error(t('workspace.skills.toasts.resourceError'));
    } finally {
      setBusy(false);
    }
  }, [skillName, onRefresh, t]);

  return (
    <Flex direction="column" gap="3">
      {allPaths.length === 0 ? (
        <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.resources.empty')}</Text>
      ) : (
        <Flex direction="column" gap="1">
          {allPaths.map((path) => (
            <Flex key={path} align="center" justify="between" style={{ padding: '6px 10px', border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-2)' }}>
              <Flex align="center" gap="2">
                <MaterialIcon name="description" size={14} color="var(--gray-9)" />
                <Text size="1" style={{ color: 'var(--slate-12)', fontFamily: 'monospace' }}>{path}</Text>
              </Flex>
              {!readOnly && (
                <Button variant="ghost" color="red" size="1" onClick={() => handleRemove(path)} disabled={busy} style={{ cursor: 'pointer' }}>
                  <MaterialIcon name="close" size={14} color="var(--red-a11)" />
                </Button>
              )}
            </Flex>
          ))}
        </Flex>
      )}

      {!readOnly && (
        <Flex direction="column" gap="2" style={{ marginTop: 8, paddingTop: 12, borderTop: '1px solid var(--olive-3)' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{t('workspace.skills.resources.addNew')}</Text>
          <FormField label={t('workspace.skills.resources.path')}>
            <TextField.Root size="2" placeholder="references/notes.md" value={newPath} onChange={(e) => setNewPath(e.target.value)} />
          </FormField>
          <FormField label={t('workspace.skills.resources.content')}>
            <TextArea size="2" rows={4} value={newContent} onChange={(e) => setNewContent(e.target.value)} />
          </FormField>
          <LoadingButton variant="solid" size="2" onClick={handleAdd} disabled={!newPath.trim()} loading={busy} style={{ alignSelf: 'flex-start' }}>
            {t('workspace.skills.resources.addNew')}
          </LoadingButton>
        </Flex>
      )}
    </Flex>
  );
}

// ========================================
// Versions tab
// ========================================

function VersionsTab({
  skillName,
  onRolledBack,
  nestedHost,
}: {
  skillName: string;
  onRolledBack: () => Promise<void>;
  nestedHost: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<SkillVersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [rollbackTarget, setRollbackTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLoading(true);
    SkillsApi.listVersions(skillName)
      .then(setVersions)
      .catch(() => setVersions([]))
      .finally(() => setLoading(false));
  }, [skillName]);

  const handleRollback = useCallback(async () => {
    if (!rollbackTarget || busy) return;
    setBusy(true);
    try {
      await SkillsApi.rollback(skillName, rollbackTarget);
      toast.success(t('workspace.skills.toasts.rolledBack', { version: rollbackTarget }));
      setRollbackTarget(null);
      await onRolledBack();
      setVersions(await SkillsApi.listVersions(skillName));
    } catch {
      toast.error(t('workspace.skills.toasts.rollbackError'));
    } finally {
      setBusy(false);
    }
  }, [rollbackTarget, busy, skillName, onRolledBack, t]);

  if (loading) {
    return <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.loading')}</Text>;
  }

  if (versions.length === 0) {
    return <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.versions.empty')}</Text>;
  }

  return (
    <Flex direction="column" gap="0">
      {versions.map((v, idx) => (
        <Flex
          key={`${v.version}-${idx}`}
          align="center"
          justify="between"
          gap="2"
          style={{ padding: '10px 4px', borderBottom: idx < versions.length - 1 ? '1px solid var(--olive-3)' : 'none' }}
        >
          <Flex direction="column" gap="0">
            <Flex align="center" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>v{v.version}</Text>
              {idx === 0 && <Badge color="green" size="1">{t('workspace.skills.versions.current')}</Badge>}
            </Flex>
            {v.summary && <Text size="1" style={{ color: 'var(--gray-10)' }}>{v.summary}</Text>}
            <Text size="1" style={{ color: 'var(--gray-9)' }}>
              {v.updatedBy ? t('workspace.skills.versions.updatedBy', { who: v.updatedBy }) : ''}
            </Text>
          </Flex>
          {idx !== 0 && (
            <Button variant="outline" color="gray" size="1" onClick={() => setRollbackTarget(v.version)} style={{ cursor: 'pointer' }}>
              {t('workspace.skills.versions.rollback')}
            </Button>
          )}
        </Flex>
      ))}

      <ConfirmationDialog
        open={!!rollbackTarget}
        onOpenChange={(open) => !open && setRollbackTarget(null)}
        title={t('workspace.skills.versions.rollbackTitle', { version: rollbackTarget })}
        message={t('workspace.skills.versions.rollbackBody', { version: rollbackTarget })}
        confirmLabel={t('workspace.skills.versions.rollback')}
        confirmVariant="danger"
        isLoading={busy}
        onConfirm={handleRollback}
        container={nestedHost}
      />
    </Flex>
  );
}
