'use client';

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Badge,
  Box,
  Callout,
  Flex,
  IconButton,
  Select,
  Separator,
  Spinner,
  Text,
  TextField,
  Tooltip,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { AuthSchemaField, SchemaField } from '@/app/(main)/workspace/connectors/types';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import {
  WorkspaceRightPanel,
  WorkspaceRightPanelBodyPortalContext,
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { ToolsetsApi, type RegistryToolsetRow, type ToolsetOauthConfigListRow } from '@/app/(main)/toolsets/api';
import {
  apiErrorDetail,
  configureAuthFieldsForType,
  getToolsetAuthConfigFromSchema,
  oauthConfigureSeedValuesFromListRow,
  toolsetSchemaRoot,
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
    if (ln === 'redirecturi' || ln === 'baseurl') continue;
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
  onCreated: () => void;
  onNotify?: (message: string) => void;
  /** After creating an OAuth-backed instance, prompt admins that users must authenticate. */
  onCreatedUserAuthNotice?: () => void;
}

export function ActionSetupPanel({
  open,
  registryRow,
  onOpenChange,
  onCreated,
  onNotify,
  onCreatedUserAuthNotice,
}: ActionSetupPanelProps) {
  const { t } = useTranslation();
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const toolsetType = registryRow?.name ?? '';
  const displayName = registryRow?.displayName || registryRow?.name || '';

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [instanceName, setInstanceName] = useState('');
  const [authType, setAuthType] = useState('NONE');
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});
  const [oauthAppValue, setOauthAppValue] = useState(NEW_OAUTH_VALUE);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [oauthConfigs, setOauthConfigs] = useState<ToolsetOauthConfigListRow[]>([]);
  const [oauthConfigsLoading, setOauthConfigsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastHydratedOauthIdRef = useRef<string | null>(null);

  const authOptions = useMemo(
    () =>
      registryRow?.supportedAuthTypes?.length
        ? registryRow.supportedAuthTypes.map((a) => String(a).toUpperCase())
        : ['NONE'],
    [registryRow?.supportedAuthTypes]
  );

  useEffect(() => {
    if (!open || !registryRow) return;
    setInstanceName(registryRow.displayName || registryRow.name || '');
    setAuthType(authOptions[0] || 'NONE');
    setFieldValues({});
    setOauthAppValue(NEW_OAUTH_VALUE);
    setClientId('');
    setClientSecret('');
    setError(null);
    lastHydratedOauthIdRef.current = null;
  }, [open, registryRow, authOptions]);

  useEffect(() => {
    setOauthAppValue(NEW_OAUTH_VALUE);
    setFieldValues({});
    setClientId('');
    setClientSecret('');
    lastHydratedOauthIdRef.current = null;
  }, [authType]);

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
    if (!open || !toolsetType || !isOAuthType(authType)) {
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
  }, [open, toolsetType, authType]);

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

  /** Only show the picker when the org already has OAuth apps for this toolset (otherwise flow is implicit "new app"). */
  const showOAuthAppPicker = useMemo(
    () => isOAuthType(authType) && !oauthConfigsLoading && oauthConfigs.length > 0,
    [authType, oauthConfigs.length, oauthConfigsLoading]
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
  }, []);

  const schemaFieldForDisplay = useCallback(
    (f: AuthSchemaField): AuthSchemaField => {
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
    [oauthAppValue, showOAuthAppPicker, t]
  );

  const handleSubmit = useCallback(async () => {
    const name = instanceName.trim();
    if (!name) {
      setError(t('workspace.actions.errors.instanceNameRequired'));
      return;
    }
    const upper = (authType || 'NONE').toUpperCase();
    const origin = typeof window !== 'undefined' ? window.location.origin : '';

    setSaving(true);
    setError(null);
    try {
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
          oauthInstanceName: name,
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
    oauthAppValue,
    onCreated,
    onCreatedUserAuthNotice,
    onNotify,
    onOpenChange,
    schemaFieldsToRender,
    showOAuthAppPicker,
    t,
    toolsetType,
  ]);

  const docUrl = useMemo(() => {
    const root = toolsetSchemaRoot(schemaRaw) as Record<string, unknown> | null;
    const links = root?.documentationLinks as { url?: string }[] | undefined;
    const url = links?.[0]?.url;
    return typeof url === 'string' && url.startsWith('http') ? url : '';
  }, [schemaRaw]);

  const headerActions =
    docUrl ? (
      <IconButton
        variant="ghost"
        color="gray"
        size="1"
        type="button"
        aria-label={t('workspace.actions.documentation')}
        onClick={() => window.open(docUrl, '_blank', 'noopener,noreferrer')}
      >
        <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
      </IconButton>
    ) : null;

  const panelIcon =
    registryRow?.iconPath ? (
      <img
        src={registryRow.iconPath}
        alt=""
        width={20}
        height={20}
        style={{ objectFit: 'contain' }}
      />
    ) : (
      <MaterialIcon name="bolt" size={20} color="var(--slate-12)" />
    );

  return (
    <WorkspaceRightPanel
      open={open && Boolean(registryRow)}
      onOpenChange={onOpenChange}
      title={t('workspace.actions.configPanelTitle')}
      icon={panelIcon}
      headerActions={headerActions}
      primaryLabel={t('action.create')}
      secondaryLabel={t('action.cancel')}
      primaryLoading={saving}
      primaryDisabled={schemaLoading || saving}
      onPrimaryClick={() => void handleSubmit()}
      onSecondaryClick={() => onOpenChange(false)}
    >
      {schemaLoading ? (
        <Flex align="center" justify="center" py="8" gap="3">
          <Spinner size="3" />
          <Text size="2" color="gray">
            {t('agentBuilder.loadingSchema')}
          </Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="4" style={{ minWidth: 0 }}>
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

          <FormField label={t('workspace.actions.instanceName')} required>
            <TextField.Root
              value={instanceName}
              onChange={(e) => setInstanceName(e.target.value)}
              placeholder={t('workspace.actions.instanceNamePlaceholder')}
            />
          </FormField>

          {authOptions.length > 1 ? (
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
          ) : null}

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

          {schemaFieldsToRender.length > 0 ? (
            <Flex direction="column" gap="3">
              {schemaFieldsToRender.map((field) => (
                <SchemaFormField
                  key={field.name}
                  field={schemaFieldForDisplay(field) as SchemaField}
                  value={fieldValues[field.name]}
                  onChange={handleFieldChange}
                  selectPortalZIndex={WORKSPACE_DRAWER_POPPER_Z_INDEX}
                />
              ))}
            </Flex>
          ) : !isNoneAuthType(authType) ? (
            <Callout.Root color="amber" variant="surface" size="1">
              <Callout.Text size="1">{t('agentBuilder.noCredentialFields')}</Callout.Text>
            </Callout.Root>
          ) : null}

          {isOAuthType(authType) && (!showOAuthAppPicker || oauthAppValue === NEW_OAUTH_VALUE) ? (
            <Flex direction="column" gap="3">
              {!schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientid') ? (
                <FormField label={t('workspace.actions.oauthClientId')} required>
                  <TextField.Root value={clientId} onChange={(e) => setClientId(e.target.value)} />
                </FormField>
              ) : null}
              {!schemaFieldsToRender.some((f) => f.name.toLowerCase() === 'clientsecret') ? (
                <FormField label={t('workspace.actions.oauthClientSecret')} required>
                  <TextField.Root
                    type="password"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
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

          <Callout.Root color="blue" variant="surface" size="1">
            <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.actions.setupInfoCallout')}
            </Callout.Text>
          </Callout.Root>

          {error ? (
            <Text size="2" color="red">
              {error}
            </Text>
          ) : null}
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
