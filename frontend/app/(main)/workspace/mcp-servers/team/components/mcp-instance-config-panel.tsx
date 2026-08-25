'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Avatar, Badge, Box, Checkbox, Flex, IconButton, Tabs, Text, TextField, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { apiClient, isProcessedError } from '@/lib/api';
import { isMcpInstanceReadOnly, McpInheritedCallout } from '@/config';
import { WorkspaceRightPanel } from '../../../components/workspace-right-panel';
import { FormField, SelectDropdown, TagInput, type TagItem } from '../../../components';
import { McpServersApi } from '../../api';
import {
  isOauthClientMissing,
  isOauthClientRequired,
  resolveDcrSupport,
  type DcrProbeState,
} from '../../oauth-dcr-requirement';
import {
  buildMultiEnvAuthPayload,
  isMultiEnvAuthComplete,
  needsMultiEnvAuth,
} from '../../stdio-env-auth';
import type {
  McpAuthMode,
  McpMyServerEntry,
  McpOAuthConfigResponse,
  McpServerInstancePayload,
  McpServerTemplate,
  McpToolInfo,
  McpTransport,
} from '../../types';
import { MCP_AUTH_MODE_LABELS, MCP_TRANSPORT_LABELS } from '../../types';

/** Matches the redirect URI the backend builds — see `_build_oauth_authorization_url` in
 * `backend/python/app/api/routes/mcp_servers.py` (trailing slash included). */
function mcpOAuthCallbackUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return `${window.location.origin.replace(/\/$/, '')}/mcp-servers/oauth/callback/`;
}

const DCR_PROBE_DEBOUNCE_MS = 500;

interface ConfigPanelState {
  open: boolean;
  mode: 'create' | 'edit';
  editingInstance: McpMyServerEntry | null;
  prefillTemplate: McpServerTemplate | null;
}

interface McpInstanceConfigPanelProps {
  state: ConfigPanelState;
  templates: McpServerTemplate[];
  /** Live org instances — keeps connection status fresh after OAuth completes while the drawer is open. */
  instances: McpMyServerEntry[];
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  onRequestDelete: (instance: McpMyServerEntry) => void;
  busyInstanceId: string | null;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onDisconnect: (instance: McpMyServerEntry) => void;
}

type PanelTab = 'configuration' | 'connection';

type ToolsResult = { tools: McpToolInfo[]; error?: string; loading: boolean };

// SSE is deliberately excluded here — custom servers can only be created as STDIO or
// Streamable HTTP; SSE remains a valid `McpTransport` value for existing/catalog instances.
const TRANSPORT_OPTIONS: { value: McpTransport; label: string }[] = [
  { value: 'stdio', label: MCP_TRANSPORT_LABELS.stdio },
  { value: 'streamable_http', label: MCP_TRANSPORT_LABELS.streamable_http },
];

const ALL_AUTH_MODES: McpAuthMode[] = ['none', 'api_token', 'oauth', 'headers'];

function tagsToStrings(tags: TagItem[]): string[] {
  return tags.map((t) => t.value).filter(Boolean);
}

function stringsToTags(values: string[]): TagItem[] {
  return values.map((v, i) => ({ id: `${v}-${i}`, value: v, isValid: true }));
}

export function McpInstanceConfigPanel({
  state,
  templates,
  instances,
  onOpenChange,
  onSaved,
  onRequestDelete,
  busyInstanceId,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
}: McpInstanceConfigPanelProps) {
  const { t } = useTranslation();
  const { open, mode, editingInstance, prefillTemplate } = state;

  const [activeTab, setActiveTab] = useState<PanelTab>('configuration');
  const [toolsResult, setToolsResult] = useState<ToolsResult | undefined>(undefined);

  const liveInstance = useMemo(
    () => (editingInstance ? instances.find((i) => i._id === editingInstance._id) ?? editingInstance : null),
    [editingInstance, instances]
  );

  const resolvedTemplate = useMemo(() => {
    if (prefillTemplate) return prefillTemplate;
    if (editingInstance?.typeId) return templates.find((tpl) => tpl.typeId === editingInstance.typeId) ?? null;
    return null;
  }, [prefillTemplate, editingInstance, templates]);

  const isTemplateBased = Boolean(resolvedTemplate);
  const isReadOnly = isMcpInstanceReadOnly(editingInstance);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [transport, setTransport] = useState<McpTransport>('stdio');
  const [authMode, setAuthMode] = useState<McpAuthMode>('none');
  const [useAdminAuth, setUseAdminAuth] = useState(false);
  const [command, setCommand] = useState('');
  const [argTags, setArgTags] = useState<TagItem[]>([]);
  const [requiredEnvTags, setRequiredEnvTags] = useState<TagItem[]>([]);
  const [url, setUrl] = useState('');
  const [headerName, setHeaderName] = useState('');
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [tokenUrl, setTokenUrl] = useState('');
  const [scopeTags, setScopeTags] = useState<TagItem[]>([]);
  const [isSaving, setIsSaving] = useState(false);

  const [apiToken, setApiToken] = useState('');
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [headerValue, setHeaderValue] = useState('');
  const [oauthClientId, setOauthClientId] = useState('');
  const [oauthClientSecret, setOauthClientSecret] = useState('');

  useEffect(() => {
    if (!open) return;
    setActiveTab('configuration');
    setToolsResult(undefined);
    if (editingInstance) {
      setName(editingInstance.name);
      setDescription(editingInstance.description ?? '');
      setTransport(editingInstance.transport);
      setAuthMode(editingInstance.authMode);
      setUseAdminAuth(editingInstance.useAdminAuth);
      setCommand(editingInstance.command ?? '');
      setArgTags(stringsToTags(editingInstance.args ?? []));
      setRequiredEnvTags(stringsToTags(editingInstance.requiredEnv ?? []));
      setUrl(editingInstance.url ?? '');
      setHeaderName(editingInstance.headerName ?? '');
      setAuthorizationUrl(editingInstance.authorizationUrl ?? '');
      setTokenUrl(editingInstance.tokenUrl ?? '');
      setScopeTags(stringsToTags(editingInstance.scopes ?? []));
    } else if (prefillTemplate) {
      setName(prefillTemplate.displayName);
      setDescription(prefillTemplate.description);
      setTransport(prefillTemplate.transport);
      setAuthMode(prefillTemplate.defaultAuthMode);
      setUseAdminAuth(false);
      setCommand(prefillTemplate.command ?? '');
      setArgTags(stringsToTags(prefillTemplate.args ?? []));
      setRequiredEnvTags(stringsToTags(prefillTemplate.requiredEnv ?? []));
      setUrl(prefillTemplate.defaultUrl ?? '');
      setHeaderName('');
      setAuthorizationUrl(prefillTemplate.authorizationUrl ?? '');
      setTokenUrl(prefillTemplate.tokenUrl ?? '');
      setScopeTags(stringsToTags(prefillTemplate.defaultScopes ?? []));
    } else {
      setName('');
      setDescription('');
      setTransport('stdio');
      setAuthMode('none');
      setUseAdminAuth(false);
      setCommand('');
      setArgTags([]);
      setRequiredEnvTags([]);
      setUrl('');
      setHeaderName('');
      setAuthorizationUrl('');
      setTokenUrl('');
      setScopeTags([]);
    }
    setApiToken('');
    setEnvValues({});
    setHeaderValue('');
    setOauthClientId('');
    setOauthClientSecret('');
  }, [open, editingInstance, prefillTemplate]);

  const availableAuthModes = resolvedTemplate?.supportedAuthModes?.length
    ? resolvedTemplate.supportedAuthModes
    : ALL_AUTH_MODES;

  const canUseAdminAuth = authMode === 'api_token' || authMode === 'headers';

  const effectiveRequiredEnv = isTemplateBased
    ? (resolvedTemplate?.requiredEnv ?? [])
    : tagsToStrings(requiredEnvTags);
  const effectiveOptionalEnv = isTemplateBased ? (resolvedTemplate?.optionalEnv ?? []) : [];
  const multiEnvAuth = authMode === 'api_token' && needsMultiEnvAuth(effectiveRequiredEnv);
  const envKeys = multiEnvAuth ? [...effectiveRequiredEnv, ...effectiveOptionalEnv] : [];

  // The URL to probe for live OAuth metadata — for catalog servers this is fixed (their
  // defaultUrl/stored url); for custom servers it's whatever the admin is currently typing.
  const oauthProbeUrl = isTemplateBased ? (resolvedTemplate?.defaultUrl ?? editingInstance?.url ?? '') : url.trim();

  const [dcrProbe, setDcrProbe] = useState<DcrProbeState>({ status: 'idle' });

  useEffect(() => {
    if (!open || authMode !== 'oauth' || !oauthProbeUrl) {
      setDcrProbe({ status: 'idle' });
      return undefined;
    }
    let cancelled = false;
    setDcrProbe({ status: 'loading' });
    const handle = setTimeout(
      () => {
        void McpServersApi.discoverOAuthMetadata(oauthProbeUrl)
          .then((result) => {
            if (!cancelled) setDcrProbe({ status: 'done', result });
          })
          .catch(() => {
            if (!cancelled) setDcrProbe({ status: 'error' });
          });
      },
      isTemplateBased ? 0 : DCR_PROBE_DEBOUNCE_MS
    );
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [open, authMode, isTemplateBased, oauthProbeUrl]);

  const dcrSupported = resolveDcrSupport(dcrProbe, resolvedTemplate?.supportsDcr);

  const [existingOAuthConfig, setExistingOAuthConfig] = useState<McpOAuthConfigResponse | null>(null);

  useEffect(() => {
    if (!open || mode !== 'edit' || !editingInstance || authMode !== 'oauth') {
      setExistingOAuthConfig(null);
      return undefined;
    }
    let cancelled = false;
    void McpServersApi.getOAuthConfig(editingInstance._id)
      .then((res) => {
        if (!cancelled) setExistingOAuthConfig(res);
      })
      .catch(() => {
        if (!cancelled) setExistingOAuthConfig(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open, mode, editingInstance, authMode]);

  const hasExistingOAuthClient = existingOAuthConfig?.configured ?? Boolean(editingInstance?.hasOAuthClientConfig);
  const oauthClientRequired = isOauthClientRequired(authMode, dcrSupported);
  const oauthClientMissing = isOauthClientMissing(
    oauthClientRequired,
    hasExistingOAuthClient,
    oauthClientId,
    oauthClientSecret
  );

  const oauthPairError =
    authMode === 'oauth' && Boolean(oauthClientId.trim()) !== Boolean(oauthClientSecret.trim())
      ? t('workspace.mcpServers.form.oauthPairRequired')
      : undefined;

  const isValid =
    name.trim().length > 0 &&
    (isTemplateBased ||
      (transport === 'stdio'
        ? command.trim().length > 0 &&
          tagsToStrings(argTags).length > 0 &&
          tagsToStrings(requiredEnvTags).length > 0
        : url.trim().length > 0)) &&
    !oauthPairError &&
    !oauthClientMissing;

  const saveCredentialsIfProvided = async (instanceId: string) => {
    try {
      if (authMode === 'api_token' && multiEnvAuth) {
        const anyFilled = envKeys.some((key) => Boolean(envValues[key]?.trim()));
        if (anyFilled) {
          if (!isMultiEnvAuthComplete(effectiveRequiredEnv, envValues)) {
            toast.error(t('workspace.mcpServers.toasts.credentialsSaveError'), {
              description: t('workspace.mcpServers.form.multiEnvIncomplete'),
            });
            return;
          }
          await McpServersApi.authenticate(
            instanceId,
            buildMultiEnvAuthPayload(effectiveRequiredEnv, effectiveOptionalEnv, envValues)
          );
        }
      } else if (authMode === 'api_token' && apiToken.trim()) {
        await McpServersApi.authenticate(instanceId, { apiToken: apiToken.trim() });
      } else if (authMode === 'headers' && headerValue.trim()) {
        await McpServersApi.authenticate(instanceId, {
          headerName: headerName.trim() || undefined,
          headerValue: headerValue.trim(),
        });
      } else if (authMode === 'oauth' && oauthClientId.trim() && oauthClientSecret.trim()) {
        await McpServersApi.updateOAuthConfig(instanceId, {
          clientId: oauthClientId.trim(),
          clientSecret: oauthClientSecret.trim(),
        });
      }
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(
        t('workspace.mcpServers.toasts.credentialsSaveError'),
        detail ? { description: detail } : undefined
      );
    }
  };

  const handleSave = async () => {
    if (!isValid) return;
    setIsSaving(true);
    try {
      const payload: McpServerInstancePayload = {
        name: name.trim(),
        typeId: resolvedTemplate?.typeId ?? null,
        transport,
        authMode,
        useAdminAuth: canUseAdminAuth ? useAdminAuth : false,
        description: description.trim() || null,
        ...(isTemplateBased
          ? {}
          : transport === 'stdio'
            ? {
                command: command.trim(),
                args: tagsToStrings(argTags),
                requiredEnv: tagsToStrings(requiredEnvTags),
              }
            : { url: url.trim() }),
        ...(authMode === 'headers' ? { headerName: headerName.trim() || null } : {}),
        ...(authMode === 'oauth' && !isTemplateBased
          ? {
              authorizationUrl: authorizationUrl.trim() || null,
              tokenUrl: tokenUrl.trim() || null,
              scopes: tagsToStrings(scopeTags),
            }
          : {}),
      };

      let instanceId: string;
      if (mode === 'edit' && editingInstance) {
        const updated = await McpServersApi.updateInstance(editingInstance._id, payload);
        instanceId = updated._id;
        toast.success(t('workspace.mcpServers.toasts.updated'));
      } else {
        const created = await McpServersApi.createInstance(payload);
        instanceId = created._id;
        toast.success(t('workspace.mcpServers.toasts.created'));
      }

      await saveCredentialsIfProvided(instanceId);

      onSaved();
      onOpenChange(false);
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.saveError'), detail ? { description: detail } : undefined);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDiscoverTools = async () => {
    if (!liveInstance) return;
    setToolsResult({ tools: [], loading: true });
    try {
      const res = await McpServersApi.getInstanceTools(liveInstance._id);
      setToolsResult({ tools: res.tools, loading: false });
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : t('workspace.mcpServers.toasts.discoveryError');
      setToolsResult({ tools: [], error: detail, loading: false });
    }
  };

  const panelTitle =
    mode === 'edit'
      ? t('workspace.mcpServers.configPanel.editTitle')
      : t('workspace.mcpServers.configPanel.createTitle');

  const apiTokenLabel = resolvedTemplate?.authHint?.label || t('workspace.mcpServers.form.apiToken');
  const apiTokenPlaceholder = resolvedTemplate?.authHint?.placeholder ?? undefined;
  const credentialsPlaceholder = mode === 'edit' ? t('workspace.mcpServers.form.leaveBlankToKeep') : undefined;

  const showFooter = mode === 'create' || (activeTab === 'configuration' && !isReadOnly);

  const configurationForm = (
    <Flex direction="column" gap="4">
      <McpInheritedCallout instance={editingInstance} />

      <FormField label={t('workspace.mcpServers.form.name')} required>
        <TextField.Root size="2" value={name} onChange={(e) => setName(e.target.value)} />
      </FormField>

      <FormField label={t('workspace.mcpServers.form.description')} optional>
        <TextField.Root size="2" value={description} onChange={(e) => setDescription(e.target.value)} />
      </FormField>

      {!isTemplateBased && (
        <FormField label={t('workspace.mcpServers.form.transport')} required>
          <SelectDropdown
            value={transport}
            onChange={(v) => setTransport(v as McpTransport)}
            options={TRANSPORT_OPTIONS}
          />
        </FormField>
      )}

      {!isTemplateBased && transport === 'stdio' && (
        <>
          <FormField label={t('workspace.mcpServers.form.command')} required>
            <TextField.Root
              size="2"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="npx"
            />
          </FormField>
          <FormField label={t('workspace.mcpServers.form.args')} required>
            <TagInput tags={argTags} onTagsChange={setArgTags} placeholder={t('workspace.mcpServers.form.argsPlaceholder')} />
          </FormField>
          <FormField label={t('workspace.mcpServers.form.requiredEnv')} required>
            <TagInput
              tags={requiredEnvTags}
              onTagsChange={setRequiredEnvTags}
              placeholder={t('workspace.mcpServers.form.requiredEnvPlaceholder')}
            />
          </FormField>
        </>
      )}

      {!isTemplateBased && transport !== 'stdio' && (
        <FormField label={t('workspace.mcpServers.form.url')} required>
          <TextField.Root
            size="2"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/mcp"
          />
        </FormField>
      )}

      <FormField label={t('workspace.mcpServers.form.authMode')} required>
        <SelectDropdown
          value={authMode}
          onChange={(v) => setAuthMode(v as McpAuthMode)}
          options={availableAuthModes.map((m) => ({ value: m, label: MCP_AUTH_MODE_LABELS[m] }))}
        />
      </FormField>

      {authMode === 'headers' && (
        <FormField label={t('workspace.mcpServers.form.headerName')} optional>
          <TextField.Root
            size="2"
            value={headerName}
            onChange={(e) => setHeaderName(e.target.value)}
            placeholder="Authorization"
          />
        </FormField>
      )}

      {authMode === 'oauth' && !isTemplateBased && (
        <>
          <FormField label={t('workspace.mcpServers.form.authorizationUrl')} optional>
            <TextField.Root
              size="2"
              value={authorizationUrl}
              onChange={(e) => setAuthorizationUrl(e.target.value)}
              placeholder={t('workspace.mcpServers.form.dcrHint')}
            />
          </FormField>
          <FormField label={t('workspace.mcpServers.form.tokenUrl')} optional>
            <TextField.Root size="2" value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)} />
          </FormField>
          <FormField label={t('workspace.mcpServers.form.scopes')} optional>
            <TagInput tags={scopeTags} onTagsChange={setScopeTags} placeholder={t('workspace.mcpServers.form.scopesPlaceholder')} />
          </FormField>
        </>
      )}

      {canUseAdminAuth && (
        <Flex align="center" gap="2">
          <Checkbox checked={useAdminAuth} onCheckedChange={(v) => setUseAdminAuth(v === true)} />
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.mcpServers.form.useAdminAuth')}
          </Text>
        </Flex>
      )}

      {authMode !== 'none' && (
        <Flex
          direction="column"
          gap="3"
          style={{
            padding: 'var(--space-3)',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--olive-4)',
            backgroundColor: 'var(--olive-2)',
          }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.mcpServers.form.credentialsHeading')}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.form.credentialsHint')}
            </Text>
          </Flex>

          {authMode === 'api_token' && multiEnvAuth &&
            envKeys.map((envKey) => {
              const isRequired = effectiveRequiredEnv.includes(envKey);
              return (
                <FormField key={envKey} label={envKey} optional={!isRequired}>
                  <TextField.Root
                    size="2"
                    type={envKey.toLowerCase().includes('token') || envKey.toLowerCase().includes('key') ? 'password' : 'text'}
                    value={envValues[envKey] ?? ''}
                    onChange={(e) => setEnvValues((prev) => ({ ...prev, [envKey]: e.target.value }))}
                    placeholder={credentialsPlaceholder}
                  />
                </FormField>
              );
            })}

          {authMode === 'api_token' && !multiEnvAuth && (
            <FormField label={apiTokenLabel} optional>
              <TextField.Root
                size="2"
                type="password"
                value={apiToken}
                onChange={(e) => setApiToken(e.target.value)}
                placeholder={credentialsPlaceholder ?? apiTokenPlaceholder}
              />
            </FormField>
          )}

          {authMode === 'headers' && (
            <FormField
              label={t('workspace.mcpServers.form.headerValue', { headerName: headerName || 'Authorization' })}
              optional
            >
              <TextField.Root
                size="2"
                type="password"
                value={headerValue}
                onChange={(e) => setHeaderValue(e.target.value)}
                placeholder={credentialsPlaceholder}
              />
            </FormField>
          )}

          {authMode === 'oauth' && (
            <>
              <McpOAuthCallbackUrlCard
                dcrProbe={dcrProbe}
                dcrSupported={dcrSupported}
                documentationUrl={resolvedTemplate?.documentationUrl}
              />
              <FormField
                label={t('workspace.mcpServers.oauthConfig.clientId')}
                required={oauthClientRequired}
                optional={!oauthClientRequired}
                error={oauthPairError}
              >
                <TextField.Root
                  size="2"
                  value={oauthClientId}
                  onChange={(e) => setOauthClientId(e.target.value)}
                  placeholder={credentialsPlaceholder ?? t('workspace.mcpServers.oauthConfig.clientIdPlaceholder')}
                />
              </FormField>
              <FormField
                label={t('workspace.mcpServers.oauthConfig.clientSecret')}
                required={oauthClientRequired}
                optional={!oauthClientRequired}
              >
                <TextField.Root
                  size="2"
                  type="password"
                  value={oauthClientSecret}
                  onChange={(e) => setOauthClientSecret(e.target.value)}
                  placeholder={credentialsPlaceholder ?? t('workspace.mcpServers.oauthConfig.clientSecretPlaceholder')}
                />
              </FormField>
              {mode === 'edit' && hasExistingOAuthClient && (
                <Text size="1" style={{ color: 'var(--amber-11)' }}>
                  {t('workspace.mcpServers.oauthConfig.alreadyConfigured')}
                  {existingOAuthConfig?.clientId ? ` (${existingOAuthConfig.clientId})` : ''}
                </Text>
              )}
            </>
          )}
        </Flex>
      )}

      {mode === 'create' && authMode !== 'none' && !useAdminAuth && (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.mcpServers.form.connectAfterCreateHint')}
        </Text>
      )}
    </Flex>
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={panelTitle}
      icon="hub"
      primaryLabel={mode === 'edit' ? t('common.save') : t('common.create')}
      secondaryLabel={t('common.cancel')}
      primaryDisabled={!isValid}
      primaryLoading={isSaving}
      onPrimaryClick={handleSave}
      hideFooter={!showFooter}
      headerActions={
        mode === 'edit' && editingInstance && !isReadOnly ? (
          <button
            type="button"
            onClick={() => onRequestDelete(editingInstance)}
            aria-label={t('workspace.mcpServers.cta.delete')}
            style={{
              appearance: 'none',
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              padding: 6,
            }}
          >
            <MaterialIcon name="delete" size={18} color="var(--red-11)" />
          </button>
        ) : undefined
      }
    >
      {mode === 'edit' ? (
        <Tabs.Root
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as PanelTab)}
          style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}
        >
          <Tabs.List
            style={{ borderBottom: '1px solid var(--olive-3)', marginBottom: 'var(--space-4)', flexShrink: 0 }}
          >
            <Tabs.Trigger value="configuration">
              {t('workspace.mcpServers.configPanel.tabs.configuration')}
            </Tabs.Trigger>
            <Tabs.Trigger value="connection">
              {t('workspace.mcpServers.configPanel.tabs.connection')}
            </Tabs.Trigger>
          </Tabs.List>

          <Tabs.Content value="configuration" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            {configurationForm}
          </Tabs.Content>

          <Tabs.Content value="connection" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <ConnectionAndToolsTab
              instance={liveInstance}
              isBusy={liveInstance ? busyInstanceId === liveInstance._id : false}
              toolsResult={toolsResult}
              onAuthenticate={onAuthenticate}
              onReauthenticate={onReauthenticate}
              onDisconnect={onDisconnect}
              onDiscoverTools={() => void handleDiscoverTools()}
            />
          </Tabs.Content>
        </Tabs.Root>
      ) : (
        configurationForm
      )}
    </WorkspaceRightPanel>
  );
}

// ========================================
// OAuth callback URL card
// ========================================

function McpOAuthCallbackUrlCard({
  dcrProbe,
  dcrSupported,
  documentationUrl,
}: {
  dcrProbe: DcrProbeState;
  dcrSupported: boolean | null;
  documentationUrl?: string | null;
}) {
  const { t } = useTranslation();
  const callbackUrl = useMemo(() => mcpOAuthCallbackUrl(), []);

  const hint =
    dcrProbe.status === 'loading'
      ? t('workspace.mcpServers.oauthConfig.dcrProbing')
      : dcrSupported === true
        ? t('workspace.mcpServers.oauthConfig.dcrSupportedHint')
        : dcrSupported === false
          ? t('workspace.mcpServers.oauthConfig.dcrUnsupportedHint')
          : t('workspace.mcpServers.oauthConfig.dcrUnknownHint');

  const handleCopy = async () => {
    if (!callbackUrl) return;
    try {
      await navigator.clipboard.writeText(callbackUrl);
      toast.success(t('workspace.mcpServers.oauthConfig.callbackUrlCopied'));
    } catch {
      /* clipboard permissions vary by browser/context — silently no-op */
    }
  };

  return (
    <Flex direction="column" gap="2">
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.mcpServers.oauthConfig.callbackUrlLabel')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.5 }}>
          {hint}
        </Text>
      </Flex>
      <Flex
        align="center"
        style={{
          border: '1px solid var(--olive-4)',
          borderRadius: 'var(--radius-2)',
          background: 'var(--color-surface)',
          paddingRight: 4,
        }}
      >
        <Box
          style={{
            flex: 1,
            minWidth: 0,
            padding: '8px 10px',
            overflowX: 'auto',
            fontSize: 12,
            whiteSpace: 'nowrap',
            fontFamily: 'var(--code-font-family, ui-monospace, monospace)',
            color: 'var(--gray-12)',
          }}
        >
          {callbackUrl ?? ''}
        </Box>
        <Tooltip content={t('workspace.mcpServers.oauthConfig.copyCallbackUrl')}>
          <IconButton
            type="button"
            size="1"
            variant="ghost"
            color="gray"
            radius="full"
            style={{ flexShrink: 0, cursor: 'pointer' }}
            aria-label={t('workspace.mcpServers.oauthConfig.copyCallbackUrl')}
            onClick={() => void handleCopy()}
          >
            <MaterialIcon name="content_copy" size={14} color="var(--gray-11)" />
          </IconButton>
        </Tooltip>
      </Flex>
      {documentationUrl && (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          <a href={documentationUrl} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-11)' }}>
            {t('workspace.mcpServers.oauthConfig.providerDocsLink')}
          </a>
        </Text>
      )}
    </Flex>
  );
}

// ========================================
// Connection & Tools tab
// ========================================

function ConnectionAndToolsTab({
  instance,
  isBusy,
  toolsResult,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
  onDiscoverTools,
}: {
  instance: McpMyServerEntry | null;
  isBusy: boolean;
  toolsResult?: ToolsResult;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onDisconnect: (instance: McpMyServerEntry) => void;
  onDiscoverTools: () => void;
}) {
  const { t } = useTranslation();

  const isReadOnly = isMcpInstanceReadOnly(instance);
  const [createdByName, setCreatedByName] = useState<string | null>(null);
  const [createdByAvatar, setCreatedByAvatar] = useState<string | null>(null);

  useEffect(() => {
    if (!instance?.createdBy || isReadOnly) {
      setCreatedByName(null);
      setCreatedByAvatar(null);
      return;
    }
    let cancelled = false;
    const createdBy = instance.createdBy;

    async function fetchCreatedByUser() {
      try {
        const { data } = await apiClient.post('/api/v1/users/by-ids', {
          userIds: [createdBy],
        });
        if (cancelled) return;

        const users = Array.isArray(data) ? data : data.users ?? [];
        if (users.length > 0) {
          const user = users[0] as Record<string, unknown>;
          const fullName = (user.name as string) ?? (user.fullName as string) ?? '';
          const userId = (user.id as string) ?? (user._id as string) ?? createdBy;
          setCreatedByName(fullName.trim() || createdBy);
          if (userId) setCreatedByAvatar(`/api/v1/users/${userId}/dp`);
        }
      } catch {
        /* ignore — fall back to the raw id */
      }
    }

    void fetchCreatedByUser();
    return () => {
      cancelled = true;
    };
  }, [instance?.createdBy, isReadOnly]);

  if (!instance) {
    return (
      <Text size="2" style={{ color: 'var(--gray-10)' }}>
        {t('workspace.mcpServers.form.connectAfterCreateHint')}
      </Text>
    );
  }

  const managedByAdmin = instance.useAdminAuth;
  const needsAuth = instance.authMode !== 'none' && !managedByAdmin;
  const isReady = !needsAuth || instance.isAuthenticated;
  const hasDiscoveredTools = Boolean(
    toolsResult && !toolsResult.loading && !toolsResult.error && toolsResult.tools.length > 0
  );

  return (
    <Flex direction="column" gap="4" style={{ height: '100%', minHeight: 0 }}>
      {!isReadOnly && (
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <Text size="1" weight="medium" style={{ color: 'var(--gray-10)' }}>
            {t('workspace.mcpServers.details.createdBy', { defaultValue: 'Created by' })}
          </Text>
          <Avatar
            size="1"
            src={createdByAvatar ?? undefined}
            fallback={createdByName?.[0] ?? '?'}
            radius="full"
          />
          <Text size="2" style={{ color: 'var(--gray-12)' }}>
            {createdByName ?? instance.createdBy}
          </Text>
        </Flex>
      )}

      {needsAuth && (
        <Flex
          direction="column"
          gap="3"
          style={{
            padding: 'var(--space-3)',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--olive-4)',
            backgroundColor: 'var(--olive-2)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" justify="between" gap="2">
            <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {instance.isAuthenticated
                  ? t('workspace.mcpServers.details.authBannerAuthenticatedTitle')
                  : t('workspace.mcpServers.details.authBannerTitle')}
              </Text>
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {instance.isAuthenticated
                  ? t('workspace.mcpServers.details.authBannerAuthenticatedDescription')
                  : t('workspace.mcpServers.details.authBannerDescription')}
              </Text>
            </Flex>
            {instance.isAuthenticated ? (
              <Badge color="green" size="1">
                {t('workspace.mcpServers.status.ready')}
              </Badge>
            ) : (
              <Badge color="amber" size="1">
                {t('workspace.mcpServers.status.notConnected')}
              </Badge>
            )}
          </Flex>

          <Flex align="center" gap="2">
            {instance.isAuthenticated ? (
              <>
                <ActionButton
                  icon="autorenew"
                  label={t('workspace.mcpServers.cta.reauthenticate')}
                  onClick={() => onReauthenticate(instance)}
                  disabled={isBusy}
                />
                <ActionButton
                  icon="link_off"
                  label={t('workspace.mcpServers.cta.disconnect')}
                  danger
                  onClick={() => onDisconnect(instance)}
                  disabled={isBusy}
                />
              </>
            ) : (
              <ActionButton
                icon="link"
                label={t('workspace.mcpServers.cta.connect')}
                onClick={() => onAuthenticate(instance)}
                disabled={isBusy}
              />
            )}
          </Flex>
        </Flex>
      )}

      {!needsAuth && (
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <Badge color="green" size="1">
            {t('workspace.mcpServers.status.ready')}
          </Badge>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {managedByAdmin
              ? t('workspace.mcpServers.sharedAuth')
              : t('workspace.mcpServers.details.authBannerAuthenticatedTitle')}
          </Text>
        </Flex>
      )}

      <Flex
        direction="column"
        gap="3"
        style={{
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-2)',
          border: '1px solid var(--olive-4)',
          backgroundColor: 'var(--olive-2)',
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', flexShrink: 0 }}>
          {t('workspace.mcpServers.details.tools')}
        </Text>

        {!isReady ? (
          <ToolsPanelEmptyState>
            <Text size="2" style={{ color: 'var(--gray-9)' }}>
              {t('workspace.mcpServers.details.discoveryNeedsAuth')}
            </Text>
          </ToolsPanelEmptyState>
        ) : toolsResult?.loading ? (
          <ToolsPanelEmptyState>
            <Text size="2" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.details.discovering')}
            </Text>
          </ToolsPanelEmptyState>
        ) : toolsResult?.error ? (
          <ToolsPanelEmptyState>
            <Flex direction="column" align="center" gap="2">
              <Text size="2" style={{ color: 'var(--red-11)' }}>
                {toolsResult.error || t('workspace.mcpServers.details.discoveryError')}
              </Text>
              <ActionButton
                icon="refresh"
                label={t('workspace.mcpServers.details.retry')}
                onClick={onDiscoverTools}
              />
            </Flex>
          </ToolsPanelEmptyState>
        ) : hasDiscoveredTools ? (
          <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <Flex align="center" justify="between" gap="2" style={{ flexShrink: 0 }}>
              <Text size="2" style={{ color: 'var(--accent-11)' }}>
                {t('workspace.mcpServers.details.discoveredTools', { count: toolsResult?.tools.length ?? 0 })}
              </Text>
              <ActionButton
                icon="refresh"
                label={t('workspace.mcpServers.details.retry')}
                onClick={onDiscoverTools}
              />
            </Flex>
            <Flex
              direction="column"
              gap="2"
              style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
            >
              {toolsResult?.tools.map((tool) => (
                <Flex
                  key={tool.namespacedName || tool.name}
                  direction="column"
                  gap="0"
                  style={{
                    padding: 'var(--space-2)',
                    borderRadius: 'var(--radius-2)',
                    backgroundColor: 'var(--gray-a2)',
                  }}
                >
                  <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                    {tool.name}
                  </Text>
                  {tool.description && (
                    <Text size="1" style={{ color: 'var(--gray-10)' }}>
                      {tool.description}
                    </Text>
                  )}
                </Flex>
              ))}
            </Flex>
          </Flex>
        ) : (
          <ToolsPanelEmptyState>
            <ActionButton
              icon="travel_explore"
              label={t('workspace.mcpServers.details.discoverTools')}
              onClick={onDiscoverTools}
            />
          </ToolsPanelEmptyState>
        )}
      </Flex>
    </Flex>
  );
}

/** Centers a lone status message / CTA button inside the (now full-height) tools card,
 * instead of letting `ActionButton`'s own `flex: 1` — meant for its two-button row usage —
 * stretch to fill all the leftover vertical space.
 */
function ToolsPanelEmptyState({ children }: { children: ReactNode }) {
  return (
    <Flex align="center" justify="center" style={{ flex: 1, minHeight: 0 }}>
      <Box>{children}</Box>
    </Flex>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  danger,
  disabled,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const color = danger ? 'var(--red-11)' : 'var(--accent-11)';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: `1px solid ${danger ? 'var(--red-a6)' : 'var(--accent-a6)'}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-2)',
        flex: 1,
        height: 'var(--space-6)',
        padding: '0 var(--space-4)',
        borderRadius: 'var(--radius-2)',
        opacity: disabled ? 0.6 : 1,
        backgroundColor: isHovered && !disabled
          ? danger
            ? 'var(--red-a4)'
            : 'var(--accent-a4)'
          : danger
            ? 'var(--red-a3)'
            : 'var(--accent-a3)',
        boxShadow: isFocused ? `0 0 0 2px ${danger ? 'var(--red-8)' : 'var(--accent-8)'}` : 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={16} color={color} />
      <span style={{ fontSize: 14, fontWeight: 500, lineHeight: '20px', color }}>{label}</span>
    </button>
  );
}
