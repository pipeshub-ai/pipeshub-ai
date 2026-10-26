'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Callout, Checkbox, Flex, Switch, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField, SelectDropdown, WorkspaceRightPanel } from '../../components';
import { ApiToolsetsApi } from '../api';
import {
  NO_AUTH_SCHEME,
  MAX_TIMEOUT_SECONDS,
  authSchemeOptions,
  buildPayload,
  defaultOperationSelection,
  forcesSharedCredential,
  formFromConfig,
  formFromSpec,
  formProblems,
  isLoopbackUrl,
  isOAuth2,
  operationsNeedingAuth,
  rebaseFormOnSpec,
  toggleOperation,
  usesSignIn,
  withAuthScheme,
  type FormProblem,
  type ToolsetForm,
} from '../toolset-form';
import type { ApiCompiledSpec, ApiToolsetConfig } from '../types';
import { ApiOperationsChecklist } from './api-operations-checklist';
import { LabeledTextArea, LabeledTextField } from './labeled-controls';
import { PanelSection } from './panel-section';

const SPEC_FILE_TYPES = '.json,.yaml,.yml,application/json,application/yaml,text/yaml';
const OAUTH_CALLBACK_PATH = '/api-toolsets/oauth/callback/';
/** The Acme Helpdesk reference spec, kept equal to integration-tests/openapi_fixture/openapi.yaml by a test there. */
const SAMPLE_SPECS = [
  { format: 'yaml', href: '/samples/api-action-sample.yaml' },
  { format: 'json', href: '/samples/api-action-sample.json' },
] as const;

interface ApiToolsetEditorPanelProps {
  open: boolean;
  /** null adds a new toolset. */
  editingId: string | null;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

/** Add or change an API action: read the spec, then say where and how to call the API and which operations agents get. */
export function ApiToolsetEditorPanel({ open, editingId, onOpenChange, onSaved }: ApiToolsetEditorPanelProps) {
  const { t } = useTranslation();
  const [specText, setSpecText] = useState('');
  // The spec text that was compiled, so a later edit to the text box can't slip into the save unchecked.
  const [preview, setPreview] = useState<{ spec: string; compiled: ApiCompiledSpec } | null>(null);
  const [config, setConfig] = useState<ApiToolsetConfig | null>(null);
  const [form, setForm] = useState<ToolsetForm | null>(null);
  const [replacingSpec, setReplacingSpec] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setSpecText('');
    setPreview(null);
    setConfig(null);
    setForm(null);
    setReplacingSpec(false);
    setError(null);
    if (!editingId) return;
    let cancelled = false;
    ApiToolsetsApi.getConfig(editingId)
      .then((loaded) => {
        if (cancelled) return;
        setConfig(loaded);
        setForm(formFromConfig(loaded));
      })
      .catch((e) => {
        if (!cancelled) setError(getUserFacingErrorMessage(e, t('workspace.apiToolsets.toasts.loadError')));
      });
    return () => {
      cancelled = true;
    };
  }, [open, editingId, t]);

  const compiled = preview?.compiled ?? null;
  const operations = compiled?.operations ?? config?.operations ?? [];
  // Without a new spec, the backend keeps the stored scheme, so it is the only choice.
  const schemes = compiled ? authSchemeOptions(compiled) : config ? [config.authScheme] : [];
  const scheme = schemes.find((s) => s.name === form?.authSchemeName) ?? NO_AUTH_SCHEME;
  const authType = scheme.type;
  const selected = useMemo(() => new Set(form?.operations ?? []), [form?.operations]);
  const problems = form ? formProblems(form, authType) : [];
  const unreachable = form ? operationsNeedingAuth(operations, form.operations, authType) : [];
  const showSpecInput = !form || replacingSpec;
  // Save sends the spec that was read; text typed after that must be read first, or it is lost.
  const specUnread = replacingSpec && specText.trim() !== '' && specText !== preview?.spec;

  const update = (patch: Partial<ToolsetForm>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const readFile = async (file: File | undefined) => {
    if (file) setSpecText(await file.text());
  };

  const handlePreview = async () => {
    const spec = specText;
    setValidating(true);
    setError(null);
    try {
      const result = await ApiToolsetsApi.validateSpec(spec);
      setPreview({ spec, compiled: result });
      setForm((f) => (f ? rebaseFormOnSpec(f, result) : formFromSpec(result)));
      setReplacingSpec(false);
    } catch (e) {
      setError(getUserFacingErrorMessage(e, t('workspace.apiToolsets.editor.validateError')));
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form || problems.length > 0 || specUnread) return;
    setSaving(true);
    setError(null);
    try {
      const payload = buildPayload(form, authType, preview?.spec);
      if (editingId) await ApiToolsetsApi.updateInstance(editingId, payload);
      else await ApiToolsetsApi.createInstance(payload);
      toast.success(t(editingId ? 'workspace.apiToolsets.toasts.updated' : 'workspace.apiToolsets.toasts.created'));
      onSaved();
      onOpenChange(false);
    } catch (e) {
      setError(getUserFacingErrorMessage(e, t('workspace.apiToolsets.toasts.saveError')));
    } finally {
      setSaving(false);
    }
  };

  const problemText = (problem: FormProblem) => t(`workspace.apiToolsets.editor.problems.${problem}`);
  const specSummary = compiled
    ? { title: compiled.title, version: compiled.version, openapi: compiled.openapiVersion }
    : config
      ? { title: config.specTitle, version: config.specVersion, openapi: config.openapiVersion }
      : null;
  const callbackUrl = `${typeof window !== 'undefined' ? window.location.origin : ''}${OAUTH_CALLBACK_PATH}`;

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(v) => !saving && onOpenChange(v)}
      title={t(editingId ? 'workspace.apiToolsets.editor.editTitle' : 'workspace.apiToolsets.editor.addTitle')}
      icon={<MaterialIcon name="api" size={20} color="var(--accent-11)" />}
      primaryLabel={t(editingId ? 'common.save' : 'workspace.apiToolsets.editor.create')}
      secondaryLabel={t('common.cancel')}
      primaryDisabled={!form || problems.length > 0 || specUnread}
      primaryTooltip={
        specUnread
          ? t('workspace.apiToolsets.editor.specUnread')
          : problems.length > 0
            ? problemText(problems[0])
            : undefined
      }
      primaryLoading={saving}
      onPrimaryClick={() => void handleSave()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      <Flex direction="column" gap="6" style={{ minWidth: 0, padding: '4px 0' }}>
        <PanelSection
          title={t('workspace.apiToolsets.editor.spec')}
          description={t(form ? 'workspace.apiToolsets.editor.specLoadedHint' : 'workspace.apiToolsets.editor.specHint')}
          aside={
            form ? (
              <Button
                size="1"
                variant="soft"
                color="gray"
                onClick={() => setReplacingSpec((v) => !v)}
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon name={replacingSpec ? 'close' : 'upload_file'} size={14} />
                {t(replacingSpec ? 'common.cancel' : 'workspace.apiToolsets.editor.replaceSpec')}
              </Button>
            ) : null
          }
        >
          {specSummary ? (
            <Flex align="center" gap="3">
              <Flex
                align="center"
                justify="center"
                style={{ width: 36, height: 36, borderRadius: 'var(--radius-2)', background: 'var(--accent-a3)', flexShrink: 0 }}
              >
                <MaterialIcon name="description" size={18} color="var(--accent-11)" />
              </Flex>
              <Flex direction="column" style={{ minWidth: 0 }}>
                <Text size="2" weight="medium" style={{ overflowWrap: 'anywhere' }}>
                  {specSummary.title}
                </Text>
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.apiToolsets.editor.specSummary', {
                    version: specSummary.version || '—',
                    openapi: specSummary.openapi,
                    count: operations.length,
                  })}
                </Text>
              </Flex>
            </Flex>
          ) : null}

          {showSpecInput ? (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept={SPEC_FILE_TYPES}
                hidden
                onChange={(e) => {
                  void readFile(e.target.files?.[0]);
                  e.target.value = '';
                }}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  void readFile(e.dataTransfer.files?.[0]);
                }}
                style={{
                  appearance: 'none',
                  margin: 0,
                  font: 'inherit',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 4,
                  padding: '20px 16px',
                  border: `1px dashed ${dragOver ? 'var(--accent-8)' : 'var(--olive-6)'}`,
                  borderRadius: 'var(--radius-2)',
                  background: dragOver ? 'var(--accent-a2)' : 'var(--color-surface)',
                  cursor: 'pointer',
                }}
              >
                <MaterialIcon name="upload_file" size={24} color="var(--accent-11)" />
                <Text size="2" weight="medium">
                  {t('workspace.apiToolsets.editor.dropFile')}
                </Text>
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {t('workspace.apiToolsets.editor.dropFileHint')}
                </Text>
              </button>
              <Flex align="center" justify="between" gap="2" wrap="wrap">
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {t('workspace.apiToolsets.editor.sampleHint')}
                </Text>
                <Flex gap="2">
                  {SAMPLE_SPECS.map(({ format, href }) => (
                    <Button key={format} asChild size="1" variant="soft" color="gray">
                      <a href={href} download>
                        <MaterialIcon name="download" size={14} />
                        {t(`workspace.apiToolsets.editor.sample.${format}`)}
                      </a>
                    </Button>
                  ))}
                </Flex>
              </Flex>
              <LabeledTextArea
                aria-label={t('workspace.apiToolsets.editor.spec')}
                size="2"
                rows={6}
                value={specText}
                onChange={(e) => setSpecText(e.target.value)}
                placeholder={t('workspace.apiToolsets.editor.specPlaceholder')}
                style={{ fontFamily: 'var(--code-font-family)', fontSize: 12 }}
              />
              <Flex justify="end">
                <LoadingButton
                  size="2"
                  disabled={!specText.trim()}
                  loading={validating}
                  loadingLabel={t('workspace.apiToolsets.editor.previewing')}
                  onClick={() => void handlePreview()}
                >
                  <MaterialIcon name="auto_awesome" size={16} />
                  {t('workspace.apiToolsets.editor.preview')}
                </LoadingButton>
              </Flex>
            </>
          ) : null}

          {compiled && compiled.warnings.length > 0 ? (
            <Callout.Root color="amber" variant="surface" size="1">
              <Flex direction="column" gap="1">
                <Callout.Text weight="medium">{t('workspace.apiToolsets.editor.warnings')}</Callout.Text>
                {compiled.warnings.map((w) => (
                  <Callout.Text key={w}>{w}</Callout.Text>
                ))}
              </Flex>
            </Callout.Root>
          ) : null}
        </PanelSection>

        {error ? (
          <Callout.Root color="red" variant="surface" size="1">
            <Callout.Icon>
              <MaterialIcon name="error_outline" size={16} />
            </Callout.Icon>
            <Callout.Text>{error}</Callout.Text>
          </Callout.Root>
        ) : null}

        {form ? (
          <>
            <PanelSection title={t('workspace.apiToolsets.editor.detailsHeading')}>
              <FormField label={t('workspace.apiToolsets.editor.name')} required>
                <LabeledTextField size="2" value={form.name} onChange={(e) => update({ name: e.target.value })} />
              </FormField>
              <FormField
                label={t('workspace.apiToolsets.editor.description')}
                optional
                description={t('workspace.apiToolsets.editor.descriptionHint')}
              >
                <LabeledTextArea size="2" rows={2} value={form.description} onChange={(e) => update({ description: e.target.value })} />
              </FormField>
              <FormField
                label={t('workspace.apiToolsets.editor.baseUrl')}
                required
                description={t('workspace.apiToolsets.editor.baseUrlHint')}
              >
                <LabeledTextField
                  size="2"
                  value={form.baseUrl}
                  placeholder="https://api.example.com"
                  onChange={(e) => update({ baseUrl: e.target.value })}
                  style={{ fontFamily: 'var(--code-font-family)' }}
                />
              </FormField>
              {isLoopbackUrl(form.baseUrl) ? (
                <Callout.Root color="amber" variant="surface" size="1">
                  <Callout.Icon>
                    <MaterialIcon name="warning_amber" size={16} />
                  </Callout.Icon>
                  <Callout.Text>{t('workspace.apiToolsets.editor.loopbackHint')}</Callout.Text>
                </Callout.Root>
              ) : null}
              {compiled && compiled.servers.length > 1 ? (
                <Flex gap="2" wrap="wrap">
                  {compiled.servers.map((server) => (
                    <Button
                      key={server.url}
                      size="1"
                      variant={server.url === form.baseUrl ? 'solid' : 'soft'}
                      color="gray"
                      title={server.description || undefined}
                      onClick={() => update({ baseUrl: server.url })}
                      style={{ cursor: 'pointer', fontFamily: 'var(--code-font-family)' }}
                    >
                      {server.url}
                    </Button>
                  ))}
                </Flex>
              ) : null}
            </PanelSection>

            <PanelSection
              title={t('workspace.apiToolsets.editor.authScheme')}
              description={t(compiled ? 'workspace.apiToolsets.editor.authSchemeHint' : 'workspace.apiToolsets.editor.authSchemeLocked')}
            >
              <SelectDropdown
                value={form.authSchemeName}
                disabled={!compiled}
                options={schemes.map((s) => ({
                  value: s.name,
                  label:
                    s.type === 'none'
                      ? t('workspace.apiToolsets.authType.none')
                      : `${t(`workspace.apiToolsets.authType.${s.type}`)} · ${s.name}`,
                  description: s.description || undefined,
                }))}
                onChange={(name) => {
                  const next = schemes.find((s) => s.name === name);
                  if (next) setForm((f) => (f ? withAuthScheme(f, next) : f));
                }}
              />

              {isOAuth2(authType) && Object.keys(scheme.scopes).length > 0 ? (
                <FormField label={t('workspace.apiToolsets.editor.scopes')} optional>
                  <Flex direction="column" gap="2">
                    {Object.entries(scheme.scopes).map(([scope, description]) => (
                      <Text as="label" size="2" key={scope}>
                        <Flex gap="2" align="start">
                          <Checkbox
                            checked={form.scopes.includes(scope)}
                            onCheckedChange={(checked) =>
                              update({
                                scopes: checked === true ? [...form.scopes, scope] : form.scopes.filter((s) => s !== scope),
                              })
                            }
                          />
                          <Text style={{ overflowWrap: 'anywhere' }}>
                            <Text style={{ fontFamily: 'var(--code-font-family)' }}>{scope}</Text>
                            {description ? <Text style={{ color: 'var(--gray-11)' }}> — {description}</Text> : null}
                          </Text>
                        </Flex>
                      </Text>
                    ))}
                  </Flex>
                </FormField>
              ) : null}

              {usesSignIn(authType) ? (
                <>
                  <FormField
                    label={t('workspace.apiToolsets.editor.redirectUrl')}
                    description={t('workspace.apiToolsets.editor.redirectUrlHint')}
                  >
                    <LabeledTextField
                      size="2"
                      readOnly
                      value={callbackUrl}
                      onFocus={(e) => e.target.select()}
                      style={{ fontFamily: 'var(--code-font-family)', fontSize: 12 }}
                    />
                  </FormField>
                  <FormField label={t('workspace.apiToolsets.editor.oauthClientId')} required>
                    <LabeledTextField size="2" value={form.oauthClientId} onChange={(e) => update({ oauthClientId: e.target.value })} />
                  </FormField>
                  <FormField label={t('workspace.apiToolsets.editor.oauthClientSecret')} optional>
                    <LabeledTextField
                      size="2"
                      type="password"
                      value={form.oauthClientSecret}
                      placeholder={config?.hasOauthClientSecret ? t('workspace.apiToolsets.editor.keepSecret') : undefined}
                      onChange={(e) => update({ oauthClientSecret: e.target.value })}
                    />
                  </FormField>
                </>
              ) : null}

              {authType !== 'none' ? (
                <Text as="label" size="2">
                  <Flex gap="3" align="start">
                    <Switch
                      checked={forcesSharedCredential(authType) || form.useSharedCredential}
                      disabled={forcesSharedCredential(authType)}
                      onCheckedChange={(checked) => update({ useSharedCredential: checked })}
                    />
                    <Flex direction="column" gap="1">
                      <Text weight="medium">{t('workspace.apiToolsets.editor.sharedCredential')}</Text>
                      <Text size="1" style={{ color: 'var(--gray-11)' }}>
                        {t(
                          forcesSharedCredential(authType)
                            ? 'workspace.apiToolsets.editor.sharedCredentialForced'
                            : 'workspace.apiToolsets.editor.sharedCredentialHint',
                        )}
                      </Text>
                    </Flex>
                  </Flex>
                </Text>
              ) : (
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t('workspace.apiToolsets.editor.noAuthHint')}
                </Text>
              )}
            </PanelSection>

            <PanelSection
              title={t('workspace.apiToolsets.editor.operations', { selected: selected.size, total: operations.length })}
              description={t('workspace.apiToolsets.editor.operationsHint')}
              aside={
                <Flex gap="1">
                  <Button
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={() => update({ operations: defaultOperationSelection(operations, authType) })}
                    style={{ cursor: 'pointer' }}
                  >
                    {t('workspace.apiToolsets.editor.selectReads')}
                  </Button>
                  <Button
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={() => update({ operations: operations.map((op) => op.toolName) })}
                    style={{ cursor: 'pointer' }}
                  >
                    {t('workspace.apiToolsets.editor.selectAll')}
                  </Button>
                  <Button size="1" variant="ghost" color="gray" onClick={() => update({ operations: [] })} style={{ cursor: 'pointer' }}>
                    {t('workspace.apiToolsets.editor.selectNone')}
                  </Button>
                </Flex>
              }
            >
              {unreachable.length > 0 ? (
                <Callout.Root color="amber" variant="surface" size="1">
                  <Callout.Icon>
                    <MaterialIcon name="lock" size={16} />
                  </Callout.Icon>
                  <Flex direction="column" gap="2" align="start">
                    <Callout.Text>
                      {t('workspace.apiToolsets.editor.unreachableWarning', { count: unreachable.length })}
                    </Callout.Text>
                    <Button
                      size="1"
                      variant="soft"
                      color="amber"
                      onClick={() => {
                        const drop = new Set(unreachable.map((op) => op.toolName));
                        update({ operations: form.operations.filter((name) => !drop.has(name)) });
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('workspace.apiToolsets.editor.deselectUnreachable')}
                    </Button>
                  </Flex>
                </Callout.Root>
              ) : null}
              <ApiOperationsChecklist
                operations={operations}
                selected={selected}
                flagSecured={authType === 'none'}
                onToggle={(name, checked) =>
                  update({ operations: toggleOperation(operations, form.operations, name, checked) })
                }
              />
              <Flex gap="4" wrap="wrap">
                {(['read', 'write', 'destructive'] as const).map((kind) => (
                  <Text key={kind} size="1" style={{ color: 'var(--gray-10)' }}>
                    <Text weight="medium" style={{ color: 'var(--gray-11)' }}>
                      {t(`workspace.apiToolsets.kind.${kind}`)}
                    </Text>
                    {' — '}
                    {t(`workspace.apiToolsets.kindPolicy.${kind}`)}
                  </Text>
                ))}
              </Flex>
            </PanelSection>

            <PanelSection title={t('workspace.apiToolsets.editor.advancedHeading')}>
              <FormField
                label={t('workspace.apiToolsets.editor.timeout')}
                description={t('workspace.apiToolsets.editor.timeoutHint', { max: MAX_TIMEOUT_SECONDS })}
              >
                <LabeledTextField
                  size="2"
                  type="number"
                  min={1}
                  max={MAX_TIMEOUT_SECONDS}
                  value={String(form.timeoutSeconds)}
                  onChange={(e) => update({ timeoutSeconds: Number(e.target.value) })}
                  style={{ maxWidth: 120 }}
                />
              </FormField>
            </PanelSection>
          </>
        ) : null}
      </Flex>
    </WorkspaceRightPanel>
  );
}
