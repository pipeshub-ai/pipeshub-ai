'use client';

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertDialog,
  Badge,
  Box,
  Button,
  Callout,
  Flex,
  IconButton,
  Select,
  Separator,
  Text,
  TextField,
  Tooltip,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { DocumentationSection } from '@/app/(main)/workspace/connectors/components/authenticate-tab/documentation-section';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { AuthSchemaField, DocumentationLink, SchemaField } from '@/app/(main)/workspace/connectors/types';
import { normalizeDocumentationLinks } from '@/app/(main)/workspace/connectors/normalize-documentation-links';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import {
  useWorkspaceDrawerNestedModalHost,
  WorkspaceRightPanel,
  WorkspaceRightPanelBodyPortalContext,
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import {
  ToolsetsApi,
  type BuilderSidebarToolset,
  type RegistryToolsetRow,
  type ToolsetOauthConfigListRow,
} from '@/app/(main)/toolsets/api';
import { CONNECTOR_SECRET_MASK } from '@/app/(main)/workspace/connectors/constants';
import {
  apiErrorDetail,
  configureAuthFieldsForType,
  documentationLinksFromToolsetSchema,
  getToolsetAuthConfigFromSchema,
  oauthConfigureSeedValuesFromListRow,
  primaryHttpDocumentationUrl,
} from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import { isNoneAuthType, isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { toolNamesFromSchema } from '../utils/tool-names-from-schema';
import { toolsetRedirectUri } from '../utils/toolset-redirect-uri';

const NEW_OAUTH_VALUE = '__new__';

function buildOAuthAuthConfigForCreate(
  fields: AuthSchemaField[],
  values: Record<string, unknown>,
  opts: { stripEmptyClientSecret: boolean }
): Record<string, unknown> {
  const authConfig: Record<string, unknown> = { type: 'OAUTH' };
  for (const f of fields) {
    const ln = f.name.toLowerCase();
    if (ln === 'redirecturi') continue;
    const raw = values[f.name];
    if (opts.stripEmptyClientSecret && ln === 'clientsecret' && (!raw || !String(raw).trim())) {
      continue;
    }
    if (raw === undefined || raw === null || String(raw).trim() === '') continue;
    if (ln === 'scopes' && typeof raw === 'string') {
      authConfig[f.name] = raw
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
    } else {
      authConfig[f.name] = raw;
    }
  }
  return authConfig;
}

export interface ActionSetupPanelProps {
  open: boolean;
  registryRow: RegistryToolsetRow | null;
  onOpenChange: (open: boolean) => void;
  /** After a save: a new instance is passed in; an edit passes nothing. */
  onCreated: (instance?: Record<string, unknown>) => void;
  onNotify?: (message: string) => void;
  /** After creating an OAuth-backed instance, prompt admins that users must authenticate. */
  onCreatedUserAuthNotice?: () => void;
  /**
   * `personal`: the user's own action, which only they see and use, signed in with their own
   * OAuth app. `org` (default): an administrator's, for everyone.
   */
  scope?: 'org' | 'personal';
  /** A personal action to edit instead of creating one. */
  editInstance?: BuilderSidebarToolset | null;
  /** After `editInstance` is deleted. */
  onDeleted?: () => void;
}

export function ActionSetupPanel({
  open,
  registryRow,
  onOpenChange,
  onCreated,
  onNotify,
  onCreatedUserAuthNotice,
  scope = 'org',
  editInstance = null,
  onDeleted,
}: ActionSetupPanelProps) {
  const { t } = useTranslation();
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const personal = scope === 'personal';
  const editing = personal && Boolean(editInstance?.instanceId);
  const nestedModalHost = useWorkspaceDrawerNestedModalHost(editing);
  const toolsetType = registryRow?.name ?? '';
  const displayName = registryRow?.displayName || registryRow?.name || '';

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [instanceName, setInstanceName] = useState('');
  const [authType, setAuthType] = useState('NONE');
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});
  const [oauthAppValue, setOauthAppValue] = useState(NEW_OAUTH_VALUE);
  const [oauthAppName, setOauthAppName] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [oauthConfigs, setOauthConfigs] = useState<ToolsetOauthConfigListRow[]>([]);
  const [oauthConfigsLoading, setOauthConfigsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const lastHydratedOauthIdRef = useRef<string | null>(null);
  /** Editing: settings saved as secrets, shown blank and kept unless replaced. */
  const [storedSecrets, setStoredSecrets] = useState<string[]>([]);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const authOptions = useMemo(
    () =>
      registryRow?.supportedAuthTypes?.length
        ? registryRow.supportedAuthTypes.map((a) => String(a).toUpperCase())
        : ['NONE'],
    [registryRow?.supportedAuthTypes]
  );

  useEffect(() => {
    if (!open || !registryRow) return;
    setInstanceName(editInstance?.instanceName || registryRow.displayName || registryRow.name || '');
    setOauthAppName(displayName ? displayName : '');
    setAuthType((editInstance?.authType || authOptions[0] || 'NONE').toUpperCase());
    setFieldValues({});
    setStoredSecrets([]);
    setDeleteOpen(false);
    setOauthAppValue(NEW_OAUTH_VALUE);
    setClientId('');
    setClientSecret('');
    setError(null);
    setFieldErrors({});
    lastHydratedOauthIdRef.current = null;
  }, [open, registryRow, authOptions, editInstance?.instanceName, editInstance?.authType]);

  // Editing starts from the action's saved settings; a secret comes back masked and stays as it is.
  useEffect(() => {
    const instanceId = editing ? editInstance?.instanceId : undefined;
    if (!open || !instanceId) return;
    let cancelled = false;
    ToolsetsApi.getToolsetInstance(instanceId)
      .then((inst) => {
        if (cancelled) return;
        const saved = (inst.auth && typeof inst.auth === 'object' ? inst.auth : {}) as Record<string, unknown>;
        const masked = Object.keys(saved).filter((key) => saved[key] === CONNECTOR_SECRET_MASK);
        setStoredSecrets(masked);
        setFieldValues(Object.fromEntries(Object.entries(saved).filter(([key]) => !masked.includes(key))));
        if (typeof saved.clientId === 'string') setClientId(saved.clientId);
      })
      .catch((e) => {
        if (!cancelled) setError(apiErrorDetail(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, editing, editInstance?.instanceId]);

  useEffect(() => {
    // An edit keeps its auth type, and its saved settings were just loaded.
    if (editing) return;
    setOauthAppValue(NEW_OAUTH_VALUE);
    setOauthAppName(displayName ? displayName : '');
    setFieldValues({});
    setClientId('');
    setClientSecret('');
    setFieldErrors({});
    lastHydratedOauthIdRef.current = null;
  }, [authType, editing]);

  useEffect(() => {
    if (!open || !toolsetType) return;
    let cancelled = false;
    (async () => {
      setSchemaLoading(true);
      try {
        const raw = await ToolsetsApi.getToolsetRegistrySchema(toolsetType);
        if (!cancelled) setSchemaRaw(raw);
      } catch {
        if (!cancelled) setSchemaRaw(null);
      } finally {
        if (!cancelled) setSchemaLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, toolsetType]);

  useEffect(() => {
    // A personal action uses its owner's own OAuth app, never one of the organization's.
    if (!open || !toolsetType || !isOAuthType(authType) || personal) {
      setOauthConfigs([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setOauthConfigsLoading(true);
      try {
        const list = await ToolsetsApi.listToolsetOAuthConfigs(toolsetType);
        if (!cancelled) {
          setOauthConfigs(list.filter((c) => Boolean(c._id)));
        }
      } catch {
        if (!cancelled) setOauthConfigs([]);
      } finally {
        if (!cancelled) setOauthConfigsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, toolsetType, authType, personal]);

  const authConfigSchema = useMemo(() => getToolsetAuthConfigFromSchema(schemaRaw), [schemaRaw]);

  const toolsetOAuthCallbackUrl = useMemo(() => {
    if (typeof window === 'undefined' || !toolsetType.trim() || !isOAuthType(authType)) return '';
    return toolsetRedirectUri(window.location.origin, toolsetType);
  }, [toolsetType, authType]);

  /** Align with connector Authenticate tab: respect schema `displayRedirectUri` unless type is plain `OAUTH`. */
  const showOAuthRedirectUri = useMemo(() => {
    if (!isOAuthType(authType) || !toolsetOAuthCallbackUrl) return false;
    const upper = (authType || '').toUpperCase();
    if (upper === 'OAUTH') return true;
    const schemas = authConfigSchema?.schemas as
      | Record<string, { displayRedirectUri?: boolean } | undefined>
      | undefined;
    const entry = schemas?.[authType] ?? schemas?.[upper];
    const fromEntry = entry && typeof entry.displayRedirectUri === 'boolean' ? entry.displayRedirectUri : undefined;
    const fromRoot =
      authConfigSchema && typeof authConfigSchema.displayRedirectUri === 'boolean'
        ? authConfigSchema.displayRedirectUri
        : undefined;
    const displayRedirect = fromEntry ?? fromRoot;
    return displayRedirect !== false;
  }, [authType, authConfigSchema, toolsetOAuthCallbackUrl]);

  const copyOAuthRedirectUri = useCallback(async () => {
    if (!toolsetOAuthCallbackUrl) return;
    try {
      await navigator.clipboard.writeText(toolsetOAuthCallbackUrl);
      onNotify?.(t('workspace.actions.redirectUriCopied'));
    } catch {
      setError(t('workspace.actions.manage.copyFailed'));
    }
  }, [onNotify, t, toolsetOAuthCallbackUrl]);

  const oauthRedirectCardShell = {
    padding: 16,
    backgroundColor: 'var(--olive-2)',
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--olive-3)',
    width: '100%' as const,
    boxSizing: 'border-box' as const,
  };

  const manageFields = useMemo(
    () => configureAuthFieldsForType(authConfigSchema, authType),
    [authConfigSchema, authType]
  );

  /** CONFIGURE schema fields only; redirect URI is provider-setup only (legacy UI hides unless displayRedirectUri). */
  const schemaFieldsToRender = useMemo(
    () => manageFields.filter((f) => f.name.toLowerCase() !== 'redirecturi'),
    [manageFields]
  );

  const toolNames = useMemo(() => toolNamesFromSchema(schemaRaw), [schemaRaw]);

  /** A server named by host (a database) can't be held to a checked address: only admins add those. */
  const adminOnly = personal && manageFields.some((f) => ['host', 'hostname'].includes(f.name.toLowerCase()));

  /** Only show the picker when the org already has OAuth apps for this toolset (otherwise flow is implicit "new app"). */
  const showOAuthAppPicker = useMemo(
    () => !personal && isOAuthType(authType) && !oauthConfigsLoading && oauthConfigs.length > 0,
    [authType, oauthConfigs.length, oauthConfigsLoading, personal]
  );

  /** If the selected config disappears from the list, fall back to the new-app path. */
  useEffect(() => {
    if (!isOAuthType(authType) || oauthConfigsLoading || oauthAppValue === NEW_OAUTH_VALUE) return;
    if (oauthConfigs.some((c) => c._id === oauthAppValue)) return;
    setOauthAppValue(NEW_OAUTH_VALUE);
    lastHydratedOauthIdRef.current = null;
    setFieldValues({});
    setClientId('');
    setClientSecret('');
  }, [authType, oauthAppValue, oauthConfigs, oauthConfigsLoading]);

  /** When linking an existing OAuth app, hydrate editable fields from list API (matches legacy dialog). */
  useEffect(() => {
    if (!open || !isOAuthType(authType) || oauthAppValue === NEW_OAUTH_VALUE) {
      lastHydratedOauthIdRef.current = null;
      return;
    }
    if (!schemaFieldsToRender.length || !showOAuthAppPicker) return;
    if (lastHydratedOauthIdRef.current === oauthAppValue) return;
    const row = oauthConfigs.find((c) => c._id === oauthAppValue);
    if (!row) return;
    setFieldValues(oauthConfigureSeedValuesFromListRow(row, schemaFieldsToRender));
    lastHydratedOauthIdRef.current = oauthAppValue;
  }, [open, authType, oauthAppValue, oauthConfigs, schemaFieldsToRender, showOAuthAppPicker]);

  const handleOauthAppChange = useCallback((value: string) => {
    setOauthAppValue(value);
    lastHydratedOauthIdRef.current = null;
    if (value === NEW_OAUTH_VALUE) {
      setFieldValues({});
      setClientId('');
      setClientSecret('');
    }
  }, []);

  const handleFieldChange = useCallback((name: string, value: unknown) => {
    setFieldValues((prev) => ({ ...prev, [name]: value }));
    setFieldErrors((prev) => {
      if (!prev[name]) return prev;
      const n = { ...prev };
      delete n[name];
      return n;
    });
  }, []);

  const schemaFieldForDisplay = useCallback(
    (f: AuthSchemaField): AuthSchemaField => {
      if (storedSecrets.includes(f.name)) {
        return { ...f, required: false, placeholder: t('workspace.actions.manage.secretPlaceholder') };
      }
      const linked = showOAuthAppPicker && oauthAppValue !== NEW_OAUTH_VALUE;
      if (!linked) return f;
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
    [oauthAppValue, showOAuthAppPicker, storedSecrets, t]
  );

  const runValidation = useCallback((): boolean => {
    setFieldErrors({});
    setError(null);
    const next: Record<string, string> = {};
    const name = instanceName.trim();
    if (!name) {
      next.instanceName = t('workspace.actions.errors.instanceNameRequired');
    }

    const upper = (authType || 'NONE').toUpperCase();
    for (const f of schemaFieldsToRender) {
      const display = schemaFieldForDisplay(f);
      if (!display.required) continue;
      if (isOAuthType(upper) && showOAuthAppPicker && oauthAppValue !== NEW_OAUTH_VALUE) {
        const ln = f.name.toLowerCase();
        if (ln === 'clientid' || ln === 'clientsecret') continue;
      }
      const v = fieldValues[f.name];
      if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) {
        next[f.name] = t('workspace.actions.validation.fieldRequired', { field: f.displayName });
      }
    }

    if (
      isOAuthType(upper) &&
      (!showOAuthAppPicker || oauthAppValue === NEW_OAUTH_VALUE)
    ) {
      // A personal action's app has no name of its own: it isn't shared with anyone.
      if (!personal && !oauthAppName.trim()) {
        next.oauthAppName = t('workspace.actions.oauthAppNameRequired');
      }
      if (!schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientid') && !clientId.trim()) {
        next.clientId = t('workspace.actions.validation.fieldRequired', { field: t('workspace.actions.oauthClientId') });
      }
      if (
        !schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientsecret') &&
        !clientSecret.trim() &&
        !storedSecrets.includes('clientSecret')
      ) {
        next.clientSecret = t('workspace.actions.validation.fieldRequired', {
          field: t('workspace.actions.oauthClientSecret'),
        });
      }
    }

    if (Object.keys(next).length > 0) {
      setFieldErrors(next);
      requestAnimationFrame(() => {
        if (next.instanceName) {
          document.querySelector('[data-ph-action-instance-name]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else if (next.oauthAppName) {
          document.querySelector('[data-ph-action-oauth-app-name]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else if (next.clientId || next.clientSecret) {
          document.querySelector('[data-ph-action-oauth-credentials]')?.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
          });
        } else {
          const first = schemaFieldsToRender.find((f) => next[f.name])?.name;
          if (first) {
            document
              .querySelector(`[data-ph-field="${first}"]`)
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      });
      return false;
    }
    return true;
  }, [
    instanceName,
    authType,
    schemaFieldsToRender,
    schemaFieldForDisplay,
    fieldValues,
    showOAuthAppPicker,
    oauthAppValue,
    oauthAppName,
    clientId,
    clientSecret,
    personal,
    storedSecrets,
    t,
  ]);

  /** What a personal action stores: its toolset's setup fields, and for OAuth its own client. */
  const personalSettings = useCallback((): Record<string, unknown> => {
    const upper = (authType || 'NONE').toUpperCase();
    const settings: Record<string, unknown> = {};
    for (const f of schemaFieldsToRender) {
      const v = fieldValues[f.name];
      if (v !== undefined && v !== null && String(v).trim() !== '') {
        settings[f.name] = typeof v === 'string' ? v.trim() : v;
      }
    }
    if (isOAuthType(upper)) {
      if (clientId.trim() && !('clientId' in settings)) settings.clientId = clientId.trim();
      if (clientSecret.trim() && !('clientSecret' in settings)) settings.clientSecret = clientSecret.trim();
    }
    // Left blank, a saved secret is sent back masked, which keeps it.
    for (const key of storedSecrets) {
      if (!(key in settings)) settings[key] = CONNECTOR_SECRET_MASK;
    }
    return settings;
  }, [authType, clientId, clientSecret, fieldValues, schemaFieldsToRender, storedSecrets]);

  const handleSubmit = useCallback(async () => {
    if (schemaLoading) {
      setError(t('agentBuilder.loadingSchema'));
      return;
    }
    if (!runValidation()) {
      return;
    }
    const name = instanceName.trim();
    const upper = (authType || 'NONE').toUpperCase();
    const origin = typeof window !== 'undefined' ? window.location.origin : '';

    setSaving(true);
    setError(null);
    try {
      if (personal) {
        if (editing && editInstance?.instanceId) {
          await ToolsetsApi.updateToolsetInstance(editInstance.instanceId, {
            instanceName: name,
            authConfig: personalSettings(),
          });
          onNotify?.(t('workspace.actions.manage.saveSuccess'));
          onCreated();
        } else {
          const created = await ToolsetsApi.createToolsetInstance({
            instanceName: name,
            toolsetType: toolsetType.toLowerCase(),
            authType: upper,
            authConfig: personalSettings(),
            scope: 'personal',
          });
          onNotify?.(t('workspace.actions.personal.added'));
          onCreated(created);
        }
        onOpenChange(false);
        return;
      }
      if (isOAuthType(upper) && showOAuthAppPicker && oauthAppValue !== NEW_OAUTH_VALUE) {
        const authConfig = buildOAuthAuthConfigForCreate(schemaFieldsToRender, fieldValues, {
          stripEmptyClientSecret: true,
        });
        await ToolsetsApi.createToolsetInstance({
          instanceName: name,
          toolsetType: toolsetType.toLowerCase(),
          authType: upper,
          baseUrl: origin,
          oauthConfigId: oauthAppValue,
          oauthInstanceName: name,
          authConfig,
        });
      } else if (isOAuthType(upper) && (!showOAuthAppPicker || oauthAppValue === NEW_OAUTH_VALUE)) {
        const clientIdInSchema = schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientid');
        const clientSecretInSchema = schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientsecret');
        const authConfig = buildOAuthAuthConfigForCreate(schemaFieldsToRender, fieldValues, {
          stripEmptyClientSecret: false,
        });
        if (!clientIdInSchema && clientId.trim()) authConfig.clientId = clientId.trim();
        if (!clientSecretInSchema && clientSecret.trim()) authConfig.clientSecret = clientSecret.trim();
        await ToolsetsApi.createToolsetInstance({
          instanceName: name,
          toolsetType: toolsetType.toLowerCase(),
          authType: upper,
          baseUrl: origin,
          authConfig,
          oauthInstanceName: oauthAppName.trim(),
        });
      } else {
        const authPayload: Record<string, unknown> = {};
        for (const f of manageFields) {
          const ln = f.name.toLowerCase();
          if (ln === 'redirecturi') continue;
          const v = fieldValues[f.name];
          if (v !== undefined && v !== null && String(v).trim() !== '') {
            authPayload[f.name] = v;
          }
        }
        await ToolsetsApi.createToolsetInstance({
          instanceName: name,
          toolsetType: toolsetType.toLowerCase(),
          authType: upper,
          baseUrl: origin,
          authConfig: isNoneAuthType(upper) ? {} : authPayload,
          oauthInstanceName: name,
        });
      }

      onNotify?.(t('workspace.actions.createSuccess'));
      if (isOAuthType(upper)) {
        onCreatedUserAuthNotice?.();
      }
      onCreated();
      onOpenChange(false);
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setSaving(false);
    }
  }, [
    authType,
    clientId,
    clientSecret,
    fieldValues,
    instanceName,
    manageFields,
    oauthAppName,
    oauthAppValue,
    onCreated,
    onCreatedUserAuthNotice,
    onNotify,
    onOpenChange,
    runValidation,
    schemaFieldsToRender,
    showOAuthAppPicker,
    t,
    toolsetType,
    schemaLoading,
    personal,
    editing,
    editInstance?.instanceId,
    personalSettings,
  ]);

  const handleDelete = useCallback(async () => {
    if (!editInstance?.instanceId) return;
    setDeleting(true);
    setError(null);
    try {
      await ToolsetsApi.deleteToolsetInstance(editInstance.instanceId);
      onNotify?.(t('workspace.actions.personal.deleted'));
      setDeleteOpen(false);
      onDeleted?.();
      onOpenChange(false);
    } catch (e) {
      setDeleteOpen(false);
      setError(apiErrorDetail(e));
    } finally {
      setDeleting(false);
    }
  }, [editInstance?.instanceId, onDeleted, onNotify, onOpenChange, t]);

  const docLinksFromRegistry = useMemo(
    (): DocumentationLink[] => normalizeDocumentationLinks(registryRow?.documentationLinks),
    [registryRow?.documentationLinks]
  );

  const docLinks = useMemo(() => {
    const fromSchema = documentationLinksFromToolsetSchema(schemaRaw);
    // Schema path is normalized; registry rows from my-toolsets are normalized in toolsets/api mappers.
    return fromSchema.length ? fromSchema : docLinksFromRegistry;
  }, [schemaRaw, docLinksFromRegistry]);

  const docUrl = useMemo(() => primaryHttpDocumentationUrl(docLinks), [docLinks]);

  const headerActions =
    docUrl ? (
      <Flex align="center" gap="1">
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          type="button"
          aria-label={t('workspace.actions.documentation')}
          onClick={() => window.open(docUrl, '_blank', 'noopener,noreferrer')}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
        </IconButton>
      </Flex>
    ) : null;

  const panelIcon = toolsetType ? (
    <ConnectorIcon type={resolveConnectorType(toolsetType)} size={20} />
  ) : (
    <MaterialIcon name="bolt" size={20} color="var(--slate-12)" />
  );

  // Shown with the admin-only notice too: another of the toolset's auth types may be one a member can add.
  const authTypeField =
    authOptions.length > 1 && !editing ? (
      <FormField label={t('workspace.actions.authType')}>
        <Select.Root value={authType} onValueChange={setAuthType} size="2">
          <Select.Trigger style={{ width: '100%' }} />
          <Select.Content
            position="popper"
            container={panelBodyPortal ?? undefined}
            style={{ zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX }}
          >
            {authOptions.map((opt) => (
              <Select.Item key={opt} value={opt}>
                {opt}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </FormField>
    ) : null;

  return (
    <WorkspaceRightPanel
      open={open && Boolean(registryRow)}
      onOpenChange={onOpenChange}
      title={t('workspace.actions.configPanelTitle')}
      icon={panelIcon}
      headerActions={headerActions}
      primaryLabel={editing ? t('action.save') : t('action.create')}
      secondaryLabel={t('action.cancel')}
      primaryLoading={saving}
      primaryDisabled={saving || adminOnly}
      primaryTooltip={saving ? t('action.saving') : undefined}
      onPrimaryClick={() => void handleSubmit()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      {schemaLoading ? (
        <Flex align="center" justify="center" py="8" style={{ width: '100%' }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('agentBuilder.loadingSchema')} />
        </Flex>
      ) : adminOnly ? (
        <Flex direction="column" gap="4" style={{ minWidth: 0, padding: '4px 0' }}>
          {authTypeField}
          <Callout.Root color="gray" variant="surface" size="1">
            <Callout.Text size="2">{t('workspace.actions.personal.adminOnly', { name: displayName })}</Callout.Text>
          </Callout.Root>
        </Flex>
      ) : (
        <Flex direction="column" gap="6" style={{ minWidth: 0, padding: '4px 0' }}>
          {docLinks.length > 0 ? (
            <DocumentationSection
              links={docLinks}
              connectorType={toolsetType || undefined}
              connectorIconPath={registryRow?.iconPath ?? '/icons/connectors/default.svg'}
            />
          ) : null}

          <Box>
            <Text as="div" size="4" weight="bold" style={{ color: 'var(--slate-12)' }}>
              {displayName}
            </Text>
            {registryRow?.description ? (
              <Text as="div" size="2" color="gray" mt="1" style={{ lineHeight: 1.5 }}>
                {registryRow.description}
              </Text>
            ) : null}
          </Box>

          <Separator size="4" />

          <Text as="div" size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.actions.configurationHeading')}
          </Text>

          {showOAuthRedirectUri ? (
            <Flex direction="column" gap="4" style={oauthRedirectCardShell}>
              <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
                <Flex direction="column" gap="1">
                  <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                    {t('workspace.actions.redirectUri')}
                  </Text>
                  <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
                    {t('workspace.actions.redirectUriHint')}
                  </Text>
                </Flex>
                <Flex
                  align="center"
                  gap="0"
                  style={{
                    width: '100%',
                    minWidth: 0,
                    border: '1px solid var(--olive-4)',
                    borderRadius: 'var(--radius-2)',
                    background: 'var(--color-surface)',
                    paddingRight: 4,
                  }}
                >
                  <Box
                    asChild
                    style={{
                      flex: 1,
                      minWidth: 0,
                      padding: '10px 12px',
                      overflowX: 'auto',
                      overflowY: 'hidden',
                      fontSize: 12,
                      whiteSpace: 'nowrap',
                      lineHeight: 1.5,
                      fontFamily: 'var(--code-font-family, ui-monospace, monospace)',
                      color: 'var(--gray-12)',
                    }}
                  >
                    <code>{toolsetOAuthCallbackUrl}</code>
                  </Box>
                  <Tooltip content={t('workspace.actions.redirectUri')}>
                    <IconButton
                      type="button"
                      size="1"
                      variant="ghost"
                      color="gray"
                      radius="full"
                      style={{ flexShrink: 0, cursor: 'pointer' }}
                      aria-label={t('workspace.actions.redirectUri')}
                      onClick={() => void copyOAuthRedirectUri()}
                    >
                      <MaterialIcon name="content_copy" size={18} color="var(--gray-11)" />
                    </IconButton>
                  </Tooltip>
                </Flex>
              </Flex>
            </Flex>
          ) : null}

          <div data-ph-action-instance-name>
            <FormField label={t('workspace.actions.instanceName')} required error={fieldErrors.instanceName}>
              <TextField.Root
                value={instanceName}
                onChange={(e) => {
                  setInstanceName(e.target.value);
                  setFieldErrors((prev) => {
                    if (!prev.instanceName) return prev;
                    const n = { ...prev };
                    delete n.instanceName;
                    return n;
                  });
                }}
                color={fieldErrors.instanceName ? 'red' : undefined}
                placeholder={t('workspace.actions.instanceNamePlaceholder')}
              />
            </FormField>
          </div>

          {authTypeField}

          {showOAuthAppPicker ? (
            <FormField label={t('workspace.actions.oauthAppLabel')}>
              <Select.Root value={oauthAppValue} onValueChange={handleOauthAppChange} size="2">
                <Select.Trigger style={{ width: '100%' }} />
                <Select.Content
                  position="popper"
                  container={panelBodyPortal ?? undefined}
                  style={{ zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX }}
                >
                  <Select.Item value={NEW_OAUTH_VALUE}>{t('workspace.actions.oauthAppNew')}</Select.Item>
                  {oauthConfigs.map((c) => (
                    <Select.Item key={c._id} value={c._id}>
                      {c.oauthInstanceName || c._id}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            </FormField>
          ) : null}

          {isOAuthType(authType) && !personal && (!showOAuthAppPicker || oauthAppValue === NEW_OAUTH_VALUE) ? (
            <Flex data-ph-action-oauth-app-name direction="column" gap="1">
              <FormField
                label={t('workspace.actions.oauthAppNameLabel')}
                required
                error={fieldErrors.oauthAppName}
              >
                <TextField.Root
                  size="2"
                  value={oauthAppName}
                  onChange={(e) => {
                    setOauthAppName(e.target.value);
                    setFieldErrors((p) => {
                      if (!p.oauthAppName) return p;
                      const n = { ...p };
                      delete n.oauthAppName;
                      return n;
                    });
                  }}
                  placeholder={t('workspace.actions.oauthAppNamePlaceholder', { name: displayName })}
                  color={fieldErrors.oauthAppName ? 'red' : undefined}
                  aria-invalid={fieldErrors.oauthAppName ? true : undefined}
                  style={{ width: '100%' }}
                />
              </FormField>
              <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
                {t('workspace.actions.oauthAppNameHelper')}
              </Text>
            </Flex>
          ) : null}

          {schemaFieldsToRender.length > 0 ? (
            <Flex direction="column" gap="3">
              {schemaFieldsToRender.map((field) => (
                <SchemaFormField
                  key={field.name}
                  field={schemaFieldForDisplay(field) as SchemaField}
                  value={fieldValues[field.name]}
                  onChange={handleFieldChange}
                  error={fieldErrors[field.name]}
                  selectPortalZIndex={WORKSPACE_DRAWER_POPPER_Z_INDEX}
                />
              ))}
            </Flex>
          ) : !isNoneAuthType(authType) && !personal ? (
            <Callout.Root color="amber" variant="surface" size="1">
              <Callout.Text size="1">{t('agentBuilder.noCredentialFields')}</Callout.Text>
            </Callout.Root>
          ) : null}

          {isOAuthType(authType) && (!showOAuthAppPicker || oauthAppValue === NEW_OAUTH_VALUE) ? (
            <Flex data-ph-action-oauth-credentials direction="column" gap="3">
              {!schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientid') ? (
                <FormField
                  label={t('workspace.actions.oauthClientId')}
                  required
                  error={fieldErrors.clientId}
                >
                  <TextField.Root
                    value={clientId}
                    onChange={(e) => {
                      setClientId(e.target.value);
                      setFieldErrors((p) => {
                        if (!p.clientId) return p;
                        const n = { ...p };
                        delete n.clientId;
                        return n;
                      });
                    }}
                    color={fieldErrors.clientId ? 'red' : undefined}
                  />
                </FormField>
              ) : null}
              {!schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientsecret') ? (
                <FormField
                  label={t('workspace.actions.oauthClientSecret')}
                  required={!storedSecrets.includes('clientSecret')}
                  error={fieldErrors.clientSecret}
                >
                  <TextField.Root
                    type="password"
                    placeholder={
                      storedSecrets.includes('clientSecret') ? t('workspace.actions.manage.secretPlaceholder') : undefined
                    }
                    value={clientSecret}
                    onChange={(e) => {
                      setClientSecret(e.target.value);
                      setFieldErrors((p) => {
                        if (!p.clientSecret) return p;
                        const n = { ...p };
                        delete n.clientSecret;
                        return n;
                      });
                    }}
                    color={fieldErrors.clientSecret ? 'red' : undefined}
                  />
                </FormField>
              ) : null}
            </Flex>
          ) : null}

          {toolNames.length > 0 ? (
            <Box>
              <Text as="div" size="2" weight="medium" mb="2" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.actions.availableActions')}
              </Text>
              <Flex gap="2" wrap="wrap" style={{ rowGap: 8 }}>
                {toolNames.map((n) => (
                  <Badge key={n} size="1" color="gray" variant="soft">
                    {n}
                  </Badge>
                ))}
              </Flex>
            </Box>
          ) : null}

          {editing ? null : (
            <Callout.Root color="blue" variant="surface" size="1">
              <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                {t(personal ? 'workspace.actions.personal.setupCallout' : 'workspace.actions.setupInfoCallout')}
              </Callout.Text>
            </Callout.Root>
          )}

          {error ? (
            <Text size="2" color="red">
              {error}
            </Text>
          ) : null}

          {editing ? (
            <Flex direction="column" gap="2" align="start">
              <Separator size="4" />
              <Button type="button" variant="soft" color="red" onClick={() => setDeleteOpen(true)}>
                <MaterialIcon name="delete" size={16} color="var(--red-11)" />
                {t('workspace.actions.personal.delete')}
              </Button>
            </Flex>
          ) : null}
        </Flex>
      )}

      {editing && nestedModalHost ? (
        <AlertDialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
          <AlertDialog.Content container={nestedModalHost} style={{ maxWidth: 440 }}>
            <AlertDialog.Title>
              {t('workspace.actions.personal.deleteTitle', { name: editInstance?.instanceName || displayName })}
            </AlertDialog.Title>
            <AlertDialog.Description size="2">{t('workspace.actions.personal.deleteBody')}</AlertDialog.Description>
            <Flex gap="3" justify="end" mt="4">
              <AlertDialog.Cancel>
                <Button variant="soft" color="gray">
                  {t('action.cancel')}
                </Button>
              </AlertDialog.Cancel>
              <Button color="red" loading={deleting} onClick={() => void handleDelete()}>
                {t('workspace.actions.personal.delete')}
              </Button>
            </Flex>
          </AlertDialog.Content>
        </AlertDialog.Root>
      ) : null}
    </WorkspaceRightPanel>
  );
}
