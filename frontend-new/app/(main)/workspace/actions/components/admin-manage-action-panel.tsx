'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import {
  AlertDialog,
  Badge,
  Button,
  Callout,
  Flex,
  IconButton,
  Select,
  Separator,
  Text,
  TextField,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  ToolsetsApi,
  type BuilderSidebarToolset,
  type ToolsetOauthConfigListRow,
} from '@/app/(main)/toolsets/api';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import type { AuthSchemaField, SchemaField } from '@/app/(main)/workspace/connectors/types';
import {
  apiErrorDetail,
  configureAuthFieldsForType,
  getToolsetAuthConfigFromSchema,
  oauthConfigureSeedValuesFromListRow,
  stableStringifyRecord,
} from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import { toolNamesFromSchema } from '../utils/tool-names-from-schema';
import { isNoneAuthType, isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { toolsetRedirectUri } from '../utils/toolset-redirect-uri';
import { useToolsetOauthPopupFlow } from '@/app/(main)/agents/agent-builder/hooks/use-toolset-oauth-popup-flow';
import {
  useWorkspaceDrawerNestedModalHost,
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
} from '@/app/(main)/workspace/components/workspace-right-panel';

/** Pick the OAuth app row for this instance when id is missing or stale. */
function resolveLinkedOauthConfig(
  list: ToolsetOauthConfigListRow[],
  inst: Pick<BuilderSidebarToolset, 'oauthConfigId' | 'instanceName' | 'displayName'>
): ToolsetOauthConfigListRow | undefined {
  if (!list.length) return undefined;
  const id = inst.oauthConfigId?.trim();
  if (id) {
    const byId = list.find((c) => c._id === id);
    if (byId) return byId;
  }
  const instKey = (inst.instanceName || inst.displayName || '').trim().toLowerCase();
  if (instKey) {
    const byName = list.find((c) => (c.oauthInstanceName || '').trim().toLowerCase() === instKey);
    if (byName) return byName;
  }
  if (list.length === 1) return list[0];
  return undefined;
}

function asAuthRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

/** Prefer full GET /instances/:id auth; else list-row `auth` when present. */
function resolvedStoredAuth(
  fromGet: Record<string, unknown> | null,
  fromSidebar: unknown
): Record<string, unknown> {
  const g = fromGet && Object.keys(fromGet).length > 0 ? fromGet : null;
  return g ?? asAuthRecord(fromSidebar) ?? {};
}

export interface AdminManageActionPanelProps {
  instance: BuilderSidebarToolset;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
  onNotify?: (message: string) => void;
}

export function AdminManageActionPanel({
  instance,
  onClose,
  onSaved,
  onDeleted,
  onNotify,
}: AdminManageActionPanelProps) {
  const router = useRouter();
  const nestedModalHost = useWorkspaceDrawerNestedModalHost(true);
  const { t } = useTranslation();
  const instanceId = instance.instanceId ?? '';
  const toolsetType = (instance.toolsetType || '').trim();
  const authType = (instance.authType || 'NONE').toUpperCase();
  const oauth = isOAuthType(authType);

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [oauthConfigs, setOauthConfigs] = useState<ToolsetOauthConfigListRow[]>([]);
  const [oauthConfigsLoading, setOauthConfigsLoading] = useState(false);

  const [instanceName, setInstanceName] = useState(instance.instanceName || instance.displayName || '');
  const [selectedOauthConfigId, setSelectedOauthConfigId] = useState(instance.oauthConfigId ?? '');
  const [oauthFieldValues, setOauthFieldValues] = useState<Record<string, unknown>>({});
  const [initialOauthSnapshot, setInitialOauthSnapshot] = useState(stableStringifyRecord({}));
  const [clientSecretWasSet, setClientSecretWasSet] = useState(false);

  const [nonOauthValues, setNonOauthValues] = useState<Record<string, unknown>>({});
  /** Instance document `auth` from GET /instances/:id (admin); my-toolsets rows often omit this. */
  const [instanceAuthFromGet, setInstanceAuthFromGet] = useState<Record<string, unknown> | null>(null);
  const [instanceAuthLoading, setInstanceAuthLoading] = useState(false);

  const [initialNonOauthSnapshot, setInitialNonOauthSnapshot] = useState(stableStringifyRecord({}));
  const [initialOauthConfigId, setInitialOauthConfigId] = useState(instance.oauthConfigId ?? '');

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [instanceNameError, setInstanceNameError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const lastOauthHydrateKeyRef = useRef<string>('');
  const oauthConfigIdInitializedRef = useRef(false);

  const toolNames = useMemo(() => {
    const fromSchema = toolNamesFromSchema(schemaRaw);
    if (fromSchema.length) return fromSchema;
    return (instance.tools || []).map((x) => x.name).filter(Boolean);
  }, [schemaRaw, instance.tools]);

  const authConfigSchema = useMemo(() => getToolsetAuthConfigFromSchema(schemaRaw), [schemaRaw]);

  const oauthFields = useMemo(() => {
    if (!oauth) return [];
    return configureAuthFieldsForType(authConfigSchema, 'OAUTH').filter(
      (f) => f.name.toLowerCase() !== 'redirecturi'
    );
  }, [oauth, authConfigSchema]);

  const nonOauthConfigureFields = useMemo(() => {
    if (oauth || isNoneAuthType(authType)) return [];
    return configureAuthFieldsForType(authConfigSchema, authType).filter(
      (f) => f.name.toLowerCase() !== 'redirecturi'
    );
  }, [oauth, authType, authConfigSchema]);

  const redirectUri = useMemo(() => {
    if (typeof window === 'undefined') return '';
    return toolsetRedirectUri(window.location.origin, toolsetType);
  }, [toolsetType]);

  // Reset selected OAuth app when the panel switches to a different instance.
  useEffect(() => {
    setSelectedOauthConfigId(instance.oauthConfigId ?? '');
    setInitialOauthConfigId(instance.oauthConfigId ?? '');
  }, [instance.instanceId, instance.oauthConfigId]);

  // Once OAuth configs finish loading, resolve the linked app if nothing is selected yet.
  useEffect(() => {
    if (!oauth || oauthConfigsLoading || !oauthConfigs.length) return;
    if (selectedOauthConfigId) return;
    const resolved = resolveLinkedOauthConfig(oauthConfigs, {
      oauthConfigId: instance.oauthConfigId,
      instanceName: instance.instanceName,
      displayName: instance.displayName,
    });
    if (resolved) setSelectedOauthConfigId(resolved._id);
  }, [
    oauth,
    oauthConfigs,
    oauthConfigsLoading,
    selectedOauthConfigId,
    instance.oauthConfigId,
    instance.instanceName,
    instance.displayName,
  ]);

  // The OAuth app row that drives field hydration (follows the dropdown selection).
  const selectedOauthRow = useMemo(
    () => oauthConfigs.find((c) => c._id === selectedOauthConfigId),
    [oauthConfigs, selectedOauthConfigId]
  );

  useEffect(() => {
    lastOauthHydrateKeyRef.current = '';
    oauthConfigIdInitializedRef.current = false;
  }, [instance.instanceId]);

  useEffect(() => {
    setInstanceName(instance.instanceName || instance.displayName || '');
  }, [instance.instanceName, instance.displayName]);

  useEffect(() => {
    if (!instanceId) {
      setInstanceAuthLoading(false);
      setInstanceAuthFromGet(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      setInstanceAuthLoading(true);
      try {
        const doc = await ToolsetsApi.getToolsetInstance(instanceId);
        if (cancelled) return;
        setInstanceAuthFromGet(asAuthRecord(doc.auth));
      } catch {
        if (!cancelled) setInstanceAuthFromGet(null);
      } finally {
        if (!cancelled) setInstanceAuthLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [instanceId]);

  useEffect(() => {
    if (!toolsetType) {
      setSchemaRaw(null);
      setSchemaLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setSchemaRaw(null);
      setSchemaLoading(true);
      try {
        const s = await ToolsetsApi.getToolsetRegistrySchema(toolsetType);
        if (!cancelled) setSchemaRaw(s);
      } catch {
        if (!cancelled) setSchemaRaw(null);
      } finally {
        if (!cancelled) setSchemaLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toolsetType]);

  useEffect(() => {
    if (!oauth || !toolsetType) return;
    let cancelled = false;
    (async () => {
      setOauthConfigs([]);
      setOauthConfigsLoading(true);
      try {
        const list = await ToolsetsApi.listToolsetOAuthConfigs(toolsetType);
        if (!cancelled) setOauthConfigs(list);
      } catch {
        if (!cancelled) setOauthConfigs([]);
      } finally {
        if (!cancelled) setOauthConfigsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [oauth, toolsetType]);

  useEffect(() => {
    if (oauth) return;
    setOauthConfigs([]);
    setOauthConfigsLoading(false);
  }, [oauth, toolsetType]);

  useEffect(() => {
    if (!oauth || !oauthFields.length) return;
    if (oauthConfigsLoading) return;
    const row = selectedOauthRow;
    const hydrateKey = `${instance.instanceId}:${row?._id ?? 'none'}:${oauthFields.map((f) => f.name).join(',')}`;
    if (hydrateKey === lastOauthHydrateKeyRef.current) return;
    const seeded = oauthConfigureSeedValuesFromListRow(row, oauthFields);
    setOauthFieldValues(seeded);
    setInitialOauthSnapshot(stableStringifyRecord(seeded));
    if (!oauthConfigIdInitializedRef.current) {
      setInitialOauthConfigId(row?._id ?? '');
      oauthConfigIdInitializedRef.current = true;
    }
    setClientSecretWasSet(
      Boolean(row?.clientSecretSet) || Boolean(String(seeded.clientSecret ?? '').trim())
    );
    lastOauthHydrateKeyRef.current = hydrateKey;
  }, [oauth, oauthFields, selectedOauthRow, instance.instanceId, oauthConfigsLoading]);

  useEffect(() => {
    if (oauth || !nonOauthConfigureFields.length) {
      setNonOauthValues({});
      setInitialNonOauthSnapshot(stableStringifyRecord({}));
      return;
    }
    const src = resolvedStoredAuth(instanceAuthFromGet, instance.auth);
    const next: Record<string, unknown> = {};
    for (const f of nonOauthConfigureFields) {
      const v = src[f.name];
      if (v !== undefined && v !== null) {
        next[f.name] = Array.isArray(v) ? v.join(',') : v;
      }
    }
    setNonOauthValues(next);
    setInitialNonOauthSnapshot(stableStringifyRecord(next));
  }, [oauth, nonOauthConfigureFields, instance.instanceId, instance.auth, instanceAuthFromGet]);

  const verifyOAuthComplete = useCallback(async (): Promise<boolean> => {
    try {
      const row = await ToolsetsApi.findMyToolsetByInstanceId(instanceId);
      return Boolean(row?.isAuthenticated);
    } catch {
      return false;
    }
  }, [instanceId]);

  const { authenticating, beginOAuth, stopOAuthUi } = useToolsetOauthPopupFlow({
    t,
    verifyAuthenticated: verifyOAuthComplete,
    onVerified: () => {
      onNotify?.(t('agentBuilder.oauthSuccessNotify'));
      onSaved();
    },
    onNotify,
    onIncomplete: () => setError(t('agentBuilder.oauthSignInIncomplete')),
    onOAuthPopupError: (msg) => setError(msg),
  });

  useEffect(
    () => () => {
      stopOAuthUi();
    },
    [stopOAuthUi]
  );

  const oauthFieldForDisplay = useCallback(
    (f: AuthSchemaField): AuthSchemaField => {
      if (!selectedOauthRow) return f;
      const ln = f.name.toLowerCase();
      if (ln === 'clientid' || ln === 'clientsecret') {
        return {
          ...f,
          required: false,
          placeholder:
            ln === 'clientsecret' ? t('workspace.actions.manage.secretPlaceholder') : f.placeholder,
        };
      }
      return f;
    },
    [selectedOauthRow, t]
  );

  const showOauthImpactCallout =
    oauth && oauthFields.length > 0 && stableStringifyRecord(oauthFieldValues) !== initialOauthSnapshot;
  const credentialsSectionLoading =
    (schemaLoading && !schemaRaw) ||
    (oauth && oauthConfigsLoading) ||
    (!oauth && instanceAuthLoading);
  const showPersonalActionsCta = !isNoneAuthType(authType) && !oauth && Boolean(toolsetType);

  const hasChanges = useMemo(() => {
    const originalName = instance.instanceName || instance.displayName || '';
    if (instanceName !== originalName) return true;
    if (oauth) {
      if (selectedOauthConfigId !== initialOauthConfigId) return true;
      if (stableStringifyRecord(oauthFieldValues) !== initialOauthSnapshot) return true;
    } else {
      if (stableStringifyRecord(nonOauthValues) !== initialNonOauthSnapshot) return true;
    }
    return false;
  }, [
    instance.instanceName,
    instance.displayName,
    instanceName,
    oauth,
    selectedOauthConfigId,
    initialOauthConfigId,
    oauthFieldValues,
    initialOauthSnapshot,
    nonOauthValues,
    initialNonOauthSnapshot,
  ]);

  const copyRedirect = useCallback(async () => {
    if (!redirectUri) return;
    try {
      await navigator.clipboard.writeText(redirectUri);
      onNotify?.(t('workspace.actions.redirectUriCopied'));
    } catch {
      setError(t('workspace.actions.manage.copyFailed'));
    }
  }, [onNotify, redirectUri, t]);

  const runManageValidation = useCallback((): boolean => {
    setFieldErrors({});
    setInstanceNameError(null);
    setError(null);
    const next: Record<string, string> = {};
    if (!instanceName.trim()) {
      setInstanceNameError(t('workspace.actions.errors.instanceNameRequired'));
    }
    if (oauth) {
      for (const f of oauthFields) {
        const display = oauthFieldForDisplay(f);
        if (!display.required) continue;
        const raw = oauthFieldValues[f.name];
        const ln = f.name.toLowerCase();
        if (ln === 'clientsecret' && !String(raw ?? '').trim() && clientSecretWasSet) {
          continue;
        }
        if (
          raw === undefined ||
          raw === null ||
          (typeof raw === 'string' && raw.trim() === '')
        ) {
          next[f.name] = t('workspace.actions.validation.fieldRequired', { field: f.displayName });
        }
      }
    } else {
      for (const f of nonOauthConfigureFields) {
        if (!f.required) continue;
        const raw = nonOauthValues[f.name];
        if (
          raw === undefined ||
          raw === null ||
          (typeof raw === 'string' && raw.trim() === '')
        ) {
          next[f.name] = t('workspace.actions.validation.fieldRequired', { field: f.displayName });
        }
      }
    }
    if (Object.keys(next).length > 0) {
      setFieldErrors(next);
    }
    const nameInvalid = !instanceName.trim();
    if (nameInvalid || Object.keys(next).length > 0) {
      requestAnimationFrame(() => {
        if (nameInvalid) {
          document
            .querySelector('[data-ph-toolset-admin-instance-name]')
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else {
          const k = Object.keys(next)[0];
          if (k) {
            document
              .querySelector(`[data-ph-auth-field="${k}"]`)
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      });
    }
    if (nameInvalid || Object.keys(next).length > 0) {
      return false;
    }
    return true;
  }, [
    clientSecretWasSet,
    instanceName,
    nonOauthConfigureFields,
    nonOauthValues,
    oauth,
    oauthFieldForDisplay,
    oauthFields,
    oauthFieldValues,
    t,
  ]);

  const handleSave = useCallback(async () => {
    if (!instanceId) return;
    if (!runManageValidation()) {
      return;
    }
    const name = instanceName.trim();
    setSaving(true);
    setError(null);
    try {
      if (oauth) {
        const authConfig: Record<string, unknown> = { type: 'OAUTH' };
        for (const f of oauthFields) {
          const ln = f.name.toLowerCase();
          if (ln === 'redirecturi') continue;
          const raw = oauthFieldValues[f.name];
          if (ln === 'clientsecret' && (!raw || !String(raw).trim())) {
            if (clientSecretWasSet) continue;
          }
          if (raw === undefined || raw === null || String(raw).trim() === '') continue;
          if (ln === 'scopes' && typeof raw === 'string') {
            authConfig[f.name] = raw
              .split(',')
              .map((s: string) => s.trim())
              .filter(Boolean);
          } else {
            authConfig[f.name] = raw;
          }
        }
        const res = await ToolsetsApi.updateToolsetInstance(instanceId, {
          instanceName: name,
          authConfig,
          ...(selectedOauthConfigId ? { oauthConfigId: selectedOauthConfigId } : {}),
        });
        const n = res.deauthenticatedUserCount ?? 0;
        if (n > 0) {
          onNotify?.(t('workspace.actions.manage.saveDeauthNotice', { count: n }));
        } else {
          onNotify?.(t('workspace.actions.manage.saveSuccess'));
        }
        onSaved();
        onClose();
        return;
      }

      // Backend PUT replaces instance.auth like POST create assigns new_instance["auth"] (no merge).
      // Build one object per configure field: form wins when set; otherwise keep prior instance auth.
      const src = resolvedStoredAuth(instanceAuthFromGet, instance.auth);
      const authCfg: Record<string, unknown> = {};
      for (const f of nonOauthConfigureFields) {
        const raw = nonOauthValues[f.name];
        if (Object.prototype.hasOwnProperty.call(nonOauthValues, f.name)) {
          if (raw === null || raw === undefined || String(raw).trim() === '') {
            continue;
          }
          authCfg[f.name] = Array.isArray(raw) ? raw.join(',') : raw;
          continue;
        }
        const prev = src[f.name];
        if (prev !== undefined && prev !== null && String(prev).trim() !== '') {
          authCfg[f.name] = Array.isArray(prev) ? (prev as unknown[]).join(',') : prev;
        }
      }
      const putRes = await ToolsetsApi.updateToolsetInstance(instanceId, {
        instanceName: name,
        ...(nonOauthConfigureFields.length > 0 ? { authConfig: authCfg } : {}),
      });
      const updatedAuth = asAuthRecord(putRes.instance?.auth);
      if (updatedAuth) {
        setInstanceAuthFromGet(updatedAuth);
      } else if (nonOauthConfigureFields.length > 0) {
        try {
          const doc = await ToolsetsApi.getToolsetInstance(instanceId);
          setInstanceAuthFromGet(asAuthRecord(doc.auth));
        } catch {
          /* ignore */
        }
      }
      onNotify?.(t('workspace.actions.manage.saveSuccess'));
      onSaved();
      onClose();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setSaving(false);
    }
  }, [
    clientSecretWasSet,
    instance.auth,
    instanceAuthFromGet,
    instanceId,
    instanceName,
    nonOauthConfigureFields,
    nonOauthValues,
    oauth,
    oauthFieldValues,
    oauthFields,
    onClose,
    onNotify,
    onSaved,
    runManageValidation,
    selectedOauthConfigId,
    t,
  ]);

  const handleDelete = useCallback(async () => {
    if (!instanceId) return;
    setDeleting(true);
    setError(null);
    try {
      await ToolsetsApi.deleteToolsetInstance(instanceId);
      onNotify?.(t('workspace.actions.manage.deleteSuccess'));
      setDeleteOpen(false);
      onDeleted();
      onClose();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setDeleting(false);
    }
  }, [instanceId, onClose, onDeleted, onNotify, t]);

  const handleAuthenticate = useCallback(async () => {
    setError(null);
    await beginOAuth(
      async () => {
        const result = await ToolsetsApi.getInstanceOAuthAuthorizationUrl(
          instanceId,
          typeof window !== 'undefined' ? window.location.origin : undefined
        );
        if (!result.success || !result.authorizationUrl) {
          throw new Error(t('agentBuilder.oauthUrlFailed'));
        }
        return {
          authorizationUrl: result.authorizationUrl,
          windowName: 'oauth_admin_toolset',
        };
      },
      {
        onTimeout: () => setError(t('agentBuilder.authTimeout')),
        onOpenError: (e) => setError(apiErrorDetail(e)),
      }
    );
  }, [beginOAuth, instanceId, t]);

  const goToPersonalActions = useCallback(() => {
    if (!toolsetType) return;
    router.push(`/workspace/actions/personal?toolsetType=${encodeURIComponent(toolsetType)}`);
  }, [router, toolsetType]);

  if (!instanceId) {
    return (
      <Text size="2" color="gray">
        {t('workspace.actions.manage.missingInstance')}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="4" style={{ width: '100%' }}>
      {oauth ? (
        <>
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.actions.redirectUri')}
            </Text>
            <Flex align="center" gap="2">
              <TextField.Root
                readOnly
                value={redirectUri}
                style={{ flex: 1 }}
                onClick={() => void copyRedirect()}
              />
              <IconButton type="button" variant="soft" color="gray" onClick={() => void copyRedirect()}>
                <MaterialIcon name="content_copy" size={16} color="var(--gray-11)" />
              </IconButton>
            </Flex>
            <Text size="1" color="gray">
              {t('workspace.actions.redirectUriHint')}
            </Text>
          </Flex>

          <Separator size="4" />

          <FormField label={t('workspace.actions.manage.oauthAppHeading')}>
            {oauthConfigsLoading ? (
              <Text size="2" color="gray">
                {t('workspace.actions.manage.loadingOauthApps', 'Loading OAuth apps…')}
              </Text>
            ) : oauthConfigs.length === 0 ? (
              <Text size="2" color="gray">
                {t('workspace.actions.manage.noOauthApps', 'No OAuth apps configured')}
              </Text>
            ) : (
              <Select.Root
                size="2"
                value={selectedOauthConfigId}
                onValueChange={(val) => {
                  lastOauthHydrateKeyRef.current = '';
                  setSelectedOauthConfigId(val);
                }}
              >
                <Select.Trigger style={{ width: '100%' }} />
                <Select.Content
                  position="popper"
                  container={nestedModalHost ?? undefined}
                  style={{ zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX }}
                >
                  {oauthConfigs.map((c) => (
                    <Select.Item key={c._id} value={c._id}>
                      {c.oauthInstanceName || c._id}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            )}
          </FormField>
        </>
      ) : null}

      <div data-ph-toolset-admin-instance-name>
        <FormField
          label={t('workspace.actions.instanceName')}
          required
          error={instanceNameError ?? undefined}
        >
          <TextField.Root
            value={instanceName}
            onChange={(e) => {
              setInstanceName(e.target.value);
              if (instanceNameError) {
                setInstanceNameError(null);
              }
            }}
            color={instanceNameError ? 'red' : undefined}
            placeholder={t('workspace.actions.instanceNamePlaceholder')}
            aria-invalid={instanceNameError ? true : undefined}
          />
        </FormField>
      </div>

      {oauth ? (
        <>
          {credentialsSectionLoading ? (
            <Flex align="center" justify="center" mt="1" style={{ width: '100%', minHeight: 120 }}>
              <LottieLoader variant="loader" size={40} showLabel label={t('agentBuilder.loadingSchema')} />
            </Flex>
          ) : oauthFields.length > 0 ? (
            <Flex direction="column" gap="3" mt="1">
              {oauthFields.map((field) => (
                <SchemaFormField
                  key={field.name}
                  field={oauthFieldForDisplay(field) as SchemaField}
                  value={oauthFieldValues[field.name]}
                  onChange={(name, val) => {
                    setOauthFieldValues((p) => ({ ...p, [name]: val }));
                    setFieldErrors((p) => {
                      if (!p[name]) return p;
                      const n = { ...p };
                      delete n[name];
                      return n;
                    });
                  }}
                  error={fieldErrors[field.name]}
                  selectPortalZIndex={WORKSPACE_DRAWER_POPPER_Z_INDEX}
                />
              ))}
            </Flex>
          ) : (
            <Callout.Root color="amber" variant="surface" size="1">
              <Callout.Text size="1">{t('agentBuilder.noCredentialFields')}</Callout.Text>
            </Callout.Root>
          )}
        </>
      ) : credentialsSectionLoading ? (
        <Flex align="center" justify="center" mt="1" style={{ width: '100%', minHeight: 120 }}>
          <LottieLoader variant="loader" size={40} showLabel label={t('agentBuilder.loadingSchema')} />
        </Flex>
      ) : nonOauthConfigureFields.length > 0 ? (
        <Flex direction="column" gap="3" mt="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.actions.configurationHeading')}
          </Text>
          {nonOauthConfigureFields.map((field) => (
            <SchemaFormField
              key={field.name}
              field={field as SchemaField}
              value={nonOauthValues[field.name]}
              onChange={(name, val) => {
                setNonOauthValues((p) => ({ ...p, [name]: val }));
                setFieldErrors((p) => {
                  if (!p[name]) return p;
                  const n = { ...p };
                  delete n[name];
                  return n;
                });
              }}
              error={fieldErrors[field.name]}
              selectPortalZIndex={WORKSPACE_DRAWER_POPPER_Z_INDEX}
            />
          ))}
        </Flex>
      ) : !isNoneAuthType(authType) ? (
        <Callout.Root color="blue">
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Callout.Text>{t('workspace.actions.manage.nonOAuthHint')}</Callout.Text>
        </Callout.Root>
      ) : null}

      {oauth && !credentialsSectionLoading && showOauthImpactCallout ? (
        <Callout.Root color="amber">
          <Callout.Icon>
            <MaterialIcon name="warning" size={16} />
          </Callout.Icon>
          <Callout.Text>{t('workspace.actions.manage.oauthImpact')}</Callout.Text>
        </Callout.Root>
      ) : null}

      {toolNames.length > 0 ? (
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.actions.availableActions')} ({toolNames.length})
          </Text>
          <Flex gap="2" wrap="wrap">
            {toolNames.map((n) => (
              <Badge key={n} size="1" variant="soft" color="gray">
                {n}
              </Badge>
            ))}
          </Flex>
        </Flex>
      ) : null}

      {showPersonalActionsCta ? (
        <Callout.Root color="blue" variant="surface" size="1">
          <Callout.Text>
            {t('workspace.actions.manage.personalActionsHint', {
              defaultValue: 'User credentials are managed in My Actions.',
            })}
            <Button
              type="button"
              size="1"
              variant="soft"
              color="blue"
              ml="2"
              onClick={goToPersonalActions}
            >
              {t('workspace.actions.manage.openPersonalActionsCta', {
                defaultValue: 'Open My Actions',
              })}
            </Button>
          </Callout.Text>
        </Callout.Root>
      ) : null}

      <Separator size="4" />

      <Flex
        direction="column"
        gap="2"
        p="3"
        style={{
          borderRadius: 'var(--radius-3)',
          border: '1px solid var(--red-a6)',
          backgroundColor: 'var(--red-a2)',
        }}
      >
        <Text size="2" weight="bold" color="red">
          {t('workspace.actions.manage.dangerZone')}
        </Text>
        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Text size="2" color="gray" style={{ maxWidth: 420 }}>
            {t('workspace.actions.manage.deleteDescription', {
              name: instance.displayName || instance.toolsetType || '',
            })}
          </Text>
          <Button color="red" variant="soft" onClick={() => setDeleteOpen(true)}>
            {t('workspace.actions.manage.deleteInstance')}
          </Button>
        </Flex>
      </Flex>

      {error ? (
        <Callout.Root color="red">
          <Callout.Text>{error}</Callout.Text>
        </Callout.Root>
      ) : null}

      <Flex justify="end" gap="2" pt="2" style={{ borderTop: '1px solid var(--gray-a4)' }}>
        <Button type="button" variant="soft" color="gray" onClick={onClose}>
          {t('action.cancel')}
        </Button>
        {oauth ? (
          <Button
            type="button"
            variant="soft"
            color="gray"
            loading={authenticating}
            onClick={() => void handleAuthenticate()}
          >
            {t('workspace.actions.cta.authenticate')}
          </Button>
        ) : null}
        <Button
          type="button"
          color="jade"
          loading={saving}
          disabled={!hasChanges}
          onClick={() => void handleSave()}
        >
          {t('action.save')}
        </Button>
      </Flex>

      {nestedModalHost ? (
        <AlertDialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
          <AlertDialog.Content container={nestedModalHost} style={{ maxWidth: 440 }}>
            <AlertDialog.Title>{t('workspace.actions.manage.deleteConfirmTitle')}</AlertDialog.Title>
            <AlertDialog.Description size="2">
              {t('workspace.actions.manage.deleteConfirmBody')}
            </AlertDialog.Description>
            <Flex gap="3" justify="end" mt="4">
              <AlertDialog.Cancel>
                <Button variant="soft" color="gray">
                  {t('action.cancel')}
                </Button>
              </AlertDialog.Cancel>
              <Button color="red" loading={deleting} onClick={() => void handleDelete()}>
                {t('workspace.actions.manage.deleteInstance')}
              </Button>
            </Flex>
          </AlertDialog.Content>
        </AlertDialog.Root>
      ) : null}
    </Flex>
  );
}
