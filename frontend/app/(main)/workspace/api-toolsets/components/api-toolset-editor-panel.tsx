'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Callout, Checkbox, Flex, Switch, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { FormField, SelectDropdown, WorkspaceRightPanel } from '../../components';
import { ApiToolsetsApi } from '../api';
import {
  NO_AUTH_SCHEME,
  MAX_TIMEOUT_SECONDS,
  authSchemeOptions,
  buildPayload,
  forcesSharedCredential,
  formFromConfig,
  formFromSpec,
  formProblems,
  isOAuth2,
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

const SPEC_FILE_TYPES = '.json,.yaml,.yml,application/json,application/yaml,text/yaml';
const OAUTH_CALLBACK_PATH = '/api-toolsets/oauth/callback/';

interface ApiToolsetEditorPanelProps {
  open: boolean;
  /** null adds a new toolset. */
  editingId: string | null;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

/** Add or change a toolset: upload the spec, pick operations, then say where and how to call the API. */
export function ApiToolsetEditorPanel({ open, editingId, onOpenChange, onSaved }: ApiToolsetEditorPanelProps) {
  const { t } = useTranslation();
  const [specText, setSpecText] = useState('');
  // The spec text that was compiled, so a later edit to the text box can't slip into the save unchecked.
  const [preview, setPreview] = useState<{ spec: string; compiled: ApiCompiledSpec } | null>(null);
  const [config, setConfig] = useState<ApiToolsetConfig | null>(null);
  const [form, setForm] = useState<ToolsetForm | null>(null);
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

  const update = (patch: Partial<ToolsetForm>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setSpecText(await file.text());
  };

  const handlePreview = async () => {
    const spec = specText;
    setValidating(true);
    setError(null);
    try {
      const result = await ApiToolsetsApi.validateSpec(spec);
      setPreview({ spec, compiled: result });
      setForm((f) => (f ? rebaseFormOnSpec(f, result) : formFromSpec(result)));
    } catch (e) {
      setError(getUserFacingErrorMessage(e, t('workspace.apiToolsets.editor.validateError')));
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    if (!form || problems.length > 0) return;
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

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(v) => !saving && onOpenChange(v)}
      title={t(editingId ? 'workspace.apiToolsets.editor.editTitle' : 'workspace.apiToolsets.editor.addTitle')}
      primaryLabel={t('common.save')}
      secondaryLabel={t('common.cancel')}
      primaryDisabled={!form || problems.length > 0}
      primaryTooltip={problems.length > 0 ? problemText(problems[0]) : undefined}
      primaryLoading={saving}
      onPrimaryClick={() => void handleSave()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      <Flex direction="column" gap="5">
        <FormField
          label={t('workspace.apiToolsets.editor.spec')}
          required={!editingId}
          optional={Boolean(editingId)}
          description={t(editingId ? 'workspace.apiToolsets.editor.specEditHint' : 'workspace.apiToolsets.editor.specHint')}
        >
          <LabeledTextArea
            size="2"
            rows={8}
            value={specText}
            onChange={(e) => setSpecText(e.target.value)}
            placeholder={t('workspace.apiToolsets.editor.specPlaceholder')}
            style={{ fontFamily: 'var(--code-font-family)' }}
          />
        </FormField>
        <Flex gap="2" wrap="wrap">
          <input
            ref={fileInputRef}
            type="file"
            accept={SPEC_FILE_TYPES}
            hidden
            onChange={(e) => {
              void handleFile(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
          <Button variant="outline" color="gray" size="2" onClick={() => fileInputRef.current?.click()}>
            {t('workspace.apiToolsets.editor.chooseFile')}
          </Button>
          <LoadingButton
            size="2"
            variant="soft"
            disabled={!specText.trim()}
            loading={validating}
            loadingLabel={t('workspace.apiToolsets.editor.previewing')}
            onClick={() => void handlePreview()}
          >
            {t('workspace.apiToolsets.editor.preview')}
          </LoadingButton>
        </Flex>

        {error ? (
          <Callout.Root color="red" variant="surface" size="1">
            <Callout.Text>{error}</Callout.Text>
          </Callout.Root>
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

        {form ? (
          <>
            <FormField
              label={t('workspace.apiToolsets.editor.operations', { selected: selected.size, total: operations.length })}
              required
              description={t('workspace.apiToolsets.editor.operationsHint')}
            >
              <ApiOperationsChecklist
                operations={operations}
                selected={selected}
                onToggle={(name, checked) =>
                  update({ operations: toggleOperation(operations, form.operations, name, checked) })
                }
              />
            </FormField>

            <FormField label={t('workspace.apiToolsets.editor.name')} required>
              <LabeledTextField size="2" value={form.name} onChange={(e) => update({ name: e.target.value })} />
            </FormField>
            <FormField label={t('workspace.apiToolsets.editor.description')} optional>
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
              />
            </FormField>

            <FormField
              label={t('workspace.apiToolsets.editor.authScheme')}
              description={compiled ? undefined : t('workspace.apiToolsets.editor.authSchemeLocked')}
            >
              <SelectDropdown
                value={form.authSchemeName}
                disabled={!compiled}
                options={schemes.map((s) => ({
                  value: s.name,
                  label:
                    s.type === 'none'
                      ? t('workspace.apiToolsets.authType.none')
                      : `${s.name} · ${t(`workspace.apiToolsets.authType.${s.type}`)}`,
                  description: s.description || undefined,
                }))}
                onChange={(name) => {
                  const next = schemes.find((s) => s.name === name);
                  if (next) setForm((f) => (f ? withAuthScheme(f, next) : f));
                }}
              />
            </FormField>

            {isOAuth2(authType) && Object.keys(scheme.scopes).length > 0 ? (
              <FormField label={t('workspace.apiToolsets.editor.scopes')} optional>
                <Flex direction="column" gap="1">
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
                  label={t('workspace.apiToolsets.editor.oauthClientId')}
                  required
                  description={t('workspace.apiToolsets.editor.oauthRedirectHint', {
                    url: `${typeof window !== 'undefined' ? window.location.origin : ''}${OAUTH_CALLBACK_PATH}`,
                  })}
                >
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
                <Flex gap="2" align="start">
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
            ) : null}

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
          </>
        ) : null}
      </Flex>
    </WorkspaceRightPanel>
  );
}
