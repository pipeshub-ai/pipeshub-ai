'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Callout, Checkbox, Flex, IconButton, Tabs, Text, TextField, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ShowStoredValuesButton } from '@/app/components/ui/show-stored-values-button';
import { toast } from '@/lib/store/toast-store';
import { useRevealScope, useSecretRevealAvailable } from '@/lib/hooks/use-secret-reveal-available';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { apiClient, isProcessedError } from '@/lib/api';
import { isMcpInstanceReadOnly, McpInheritedCallout } from '@/config';
import { useWorkspaceDrawerNestedModalHost, WorkspaceRightPanel } from '../../../components/workspace-right-panel';
import { ConfirmationDialog, FormField, SelectDropdown, TagInput, type TagItem } from '../../../components';
import { McpServersApi } from '../../api';
import { McpDisabledCallout, McpDisconnectDialog, isMcpInstanceDisabled } from '../../components';
import { useToolRulesEditor, type McpToolRulesTarget } from '../../components/mcp-tool-rules-editor';
import { mcpConnectionState, usesSharedCredential, type McpConnectionState } from '../../connection-state';
import { useCopyText } from '@/lib/hooks/use-copy-text';
import { McpPanelTitle, McpServerActionsMenu, type McpMenuEntry } from './mcp-panel-header';
import { McpToolsApprovalsTab, type McpToolsLoad } from './mcp-tools-approvals-tab';
import {
  isOauthClientMissing,
  isOauthClientRequired,
  resolveDcrSupport,
  resolveMcpOAuthCallbackUrl,
  type DcrProbeState,
} from '../../oauth-dcr-requirement';
import {
  buildMultiEnvAuthPayload,
  isMultiEnvAuthComplete,
  isSecretFieldName,
  needsMultiEnvAuth,
} from '../../stdio-env-auth';
import { mcpEditCredentialImpact, type McpEditCredentialImpact } from '../../edit-impact';
import { ruleToolsFromInfo } from '../../tool-rules';
import type {
  McpAuthMode,
  McpInstanceScope,
  McpMyServerEntry,
  McpOAuthConfigResponse,
  McpServerInstancePayload,
  McpServerTemplate,
  McpTransport,
} from '../../types';
import {
  MCP_AUTH_MODE_LABELS,
  MCP_CUSTOM_STDIO_FLAG,
  MCP_TIMEOUT_LIMITS,
  MCP_TRANSPORT_LABELS,
  isPersonalMcpInstance,
} from '../../types';

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
  /** Operator setting from `GET /catalog`; custom STDIO servers are refused when false. */
  customStdioAllowed: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  onRequestDelete: (instance: McpMyServerEntry) => void;
  busyInstanceId: string | null;
  onAuthenticate: (instance: McpMyServerEntry) => void;
  onReauthenticate: (instance: McpMyServerEntry) => void;
  onDisconnect: (instance: McpMyServerEntry) => void;
  /** Where a new instance is created; an existing one keeps its own. Defaults to `org`. */
  scope?: McpInstanceScope;
}

type PanelTab = 'configuration' | 'tools';

// SSE is deliberately excluded here — custom servers can only be created as STDIO or
// Streamable HTTP; SSE remains a valid `McpTransport` value for existing/catalog instances.
const STDIO_OPTION = { value: 'stdio' as const, label: MCP_TRANSPORT_LABELS.stdio };
const HTTP_OPTION = { value: 'streamable_http' as const, label: MCP_TRANSPORT_LABELS.streamable_http };

const ALL_AUTH_MODES: McpAuthMode[] = ['none', 'api_token', 'oauth', 'headers'];

/** '' = use the default; otherwise a whole number of seconds within `limits`. */
function parseTimeout(raw: string, limits: { min: number; max: number }): { value: number | null; valid: boolean } {
  const text = raw.trim();
  if (!text) return { value: null, valid: true };
  const value = Number(text);
  return { value, valid: Number.isInteger(value) && value >= limits.min && value <= limits.max };
}

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
  customStdioAllowed,
  onOpenChange,
  onSaved,
  onRequestDelete,
  busyInstanceId,
  onAuthenticate,
  onReauthenticate,
  onDisconnect,
  scope = 'org',
}: McpInstanceConfigPanelProps) {
  const { t } = useTranslation();
  const { open, mode, editingInstance, prefillTemplate } = state;
  // The backend refuses a local command or a shared admin credential on a personal instance.
  const isPersonal = mode === 'edit' ? isPersonalMcpInstance(editingInstance) : scope === 'personal';

  const [activeTab, setActiveTab] = useState<PanelTab>('configuration');
  const [toolsLoad, setToolsLoad] = useState<McpToolsLoad>({ status: 'idle' });
  const [disconnectTarget, setDisconnectTarget] = useState<McpMyServerEntry | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [creatorName, setCreatorName] = useState<string | null>(null);
  const { copy } = useCopyText();

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
  const [connectTimeout, setConnectTimeout] = useState('');
  const [callTimeout, setCallTimeout] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [pendingImpact, setPendingImpact] = useState<McpEditCredentialImpact>('none');
  const nestedModalHost = useWorkspaceDrawerNestedModalHost(open);

  const [apiToken, setApiToken] = useState('');
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [headerValue, setHeaderValue] = useState('');
  const [oauthClientId, setOauthClientId] = useState('');
  const [oauthClientSecret, setOauthClientSecret] = useState('');

  // Read by the form reset below without being one of its deps: the catalog that carries
  // this flag can arrive after the drawer opens, and re-running the reset then would wipe
  // whatever the admin has already typed.
  const customStdioAllowedRef = useRef(customStdioAllowed);
  useEffect(() => {
    customStdioAllowedRef.current = customStdioAllowed;
  }, [customStdioAllowed]);

  // Bumped whenever the list is asked for again or the panel reopens: an older answer is dropped.
  const toolsRequest = useRef(0);

  useEffect(() => {
    if (!open) return;
    setActiveTab('configuration');
    toolsRequest.current += 1;
    setToolsLoad({ status: 'idle' });
    setConfirmDiscard(false);
    setPendingImpact('none');
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
      setConnectTimeout(editingInstance.connectTimeoutSeconds ? String(editingInstance.connectTimeoutSeconds) : '');
      setCallTimeout(editingInstance.callTimeoutSeconds ? String(editingInstance.callTimeoutSeconds) : '');
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
      setConnectTimeout('');
      setCallTimeout('');
    } else {
      setName('');
      setDescription('');
      setTransport(customStdioAllowedRef.current && !isPersonal ? 'stdio' : 'streamable_http');
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
      setConnectTimeout('');
      setCallTimeout('');
    }
    setApiToken('');
    setEnvValues({});
    setHeaderValue('');
    setOauthClientId('');
    setOauthClientSecret('');
  }, [open, editingInstance, prefillTemplate, isPersonal]);

  // An existing STDIO instance, or a STDIO form open when the setting turned off, keeps the
  // option so the dropdown still shows the selected value; saving it is blocked below.
  const transportOptions: { value: McpTransport; label: string }[] =
    !isPersonal && (customStdioAllowed || editingInstance?.transport === 'stdio' || transport === 'stdio')
      ? [STDIO_OPTION, HTTP_OPTION]
      : [HTTP_OPTION];
  const customStdioBlocked = !isTemplateBased && transport === 'stdio' && !customStdioAllowed;

  const availableAuthModes = resolvedTemplate?.supportedAuthModes?.length
    ? resolvedTemplate.supportedAuthModes
    : ALL_AUTH_MODES;

  const canUseAdminAuth = !isPersonal && (authMode === 'api_token' || authMode === 'headers');

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

  const revealAvailable = useSecretRevealAvailable(open && mode === 'edit' && authMode === 'oauth');
  // What a reveal put in the OAuth fields; Save only sends the pair once it differs.
  const [revealedOAuthClient, setRevealedOAuthClient] = useState<{
    clientId: string;
    clientSecret: string;
  } | null>(null);
  const oauthClientRevealed = revealedOAuthClient !== null;
  const [revealingOAuthClient, setRevealingOAuthClient] = useState(false);
  const [showOauthClientSecret, setShowOauthClientSecret] = useState(false);
  const isMobile = useIsMobile();
  useEffect(() => {
    setRevealedOAuthClient(null);
    setShowOauthClientSecret(false);
  }, [open, editingInstance?._id]);
  const beginReveal = useRevealScope(`${open}:${editingInstance?._id ?? ''}`);

  // Fills only the fields the admin has left empty.
  const handleRevealOAuthClient = async () => {
    if (!editingInstance) return;
    const stillCurrent = beginReveal();
    setRevealingOAuthClient(true);
    try {
      const stored = await McpServersApi.revealOAuthConfig(editingInstance._id);
      if (!stillCurrent()) return;
      setOauthClientId((prev) => prev || stored.clientId || '');
      setOauthClientSecret((prev) => prev || stored.clientSecret || '');
      setRevealedOAuthClient({
        clientId: stored.clientId || '',
        clientSecret: stored.clientSecret || '',
      });
    } catch {
      // The fields stay blank, which still saves as "keep the stored value".
    } finally {
      setRevealingOAuthClient(false);
    }
  };
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

  const connectTimeoutInput = parseTimeout(connectTimeout, MCP_TIMEOUT_LIMITS.connect);
  const callTimeoutInput = parseTimeout(callTimeout, MCP_TIMEOUT_LIMITS.call);

  const isValid =
    connectTimeoutInput.valid &&
    callTimeoutInput.valid &&
    name.trim().length > 0 &&
    (isTemplateBased ||
      (transport === 'stdio' ? command.trim().length > 0 : url.trim().length > 0)) &&
    !oauthPairError &&
    !oauthClientMissing &&
    !customStdioBlocked;

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
      } else if (
        authMode === 'oauth' &&
        oauthClientId.trim() &&
        oauthClientSecret.trim() &&
        !(
          revealedOAuthClient &&
          oauthClientId.trim() === revealedOAuthClient.clientId.trim() &&
          oauthClientSecret.trim() === revealedOAuthClient.clientSecret.trim()
        )
      ) {
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

  const buildPayload = (): McpServerInstancePayload => ({
    name: name.trim(),
    typeId: resolvedTemplate?.typeId ?? null,
    ...(mode === 'create' ? { scope: isPersonal ? 'personal' : 'org' } : {}),
    transport: resolvedTemplate?.transport ?? transport,
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
    connectTimeoutSeconds: connectTimeoutInput.value,
    callTimeoutSeconds: callTimeoutInput.value,
  });

  const handleSave = () => {
    if (!isValid) return;
    if (mode === 'edit' && editingInstance) {
      const impact = mcpEditCredentialImpact(editingInstance, buildPayload());
      if (impact !== 'none') {
        setPendingImpact(impact);
        return;
      }
    }
    void save();
  };

  const save = async () => {
    setIsSaving(true);
    try {
      const payload = buildPayload();
      let instanceId: string;
      if (mode === 'edit' && editingInstance) {
        const updated = await McpServersApi.updateInstance(editingInstance._id, payload);
        instanceId = updated._id;
        if (updated.credentialsReset) {
          toast.warning(t('workspace.mcpServers.toasts.credentialsReset'));
        } else {
          toast.success(t('workspace.mcpServers.toasts.updated'));
        }
      } else {
        const created = await McpServersApi.createInstance(payload);
        instanceId = created._id;
        toast.success(t('workspace.mcpServers.toasts.created'));
      }

      await saveCredentialsIfProvided(instanceId);

      onSaved();
      requestOpenChange(false);
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.saveError'), detail ? { description: detail } : undefined);
    } finally {
      setIsSaving(false);
    }
  };

  const liveId = liveInstance?._id;
  // Opening the tab reads the cached list; Refresh connects.
  const loadTools = useCallback(
    async (cached: boolean) => {
      if (!liveId) return;
      const request = ++toolsRequest.current;
      setToolsLoad({ status: 'loading' });
      try {
        const res = await McpServersApi.getInstanceTools(liveId, { cached });
        if (request !== toolsRequest.current) return;
        setToolsLoad({ status: 'ready', tools: res.tools, syncedAt: res.syncedAt ?? Date.now() });
      } catch (error) {
        if (request !== toolsRequest.current) return;
        const status = isProcessedError(error) ? error.statusCode : undefined;
        setToolsLoad({
          status: 'error',
          code: status === 409 ? 'reauth' : status === 502 ? 'unreachable' : 'error',
          message: isProcessedError(error) ? error.message : t('workspace.mcpServers.toasts.discoveryError'),
        });
      }
    },
    [liveId, t]
  );

  // How the server is doing, corrected by what the tool list just said.
  const connectionState: McpConnectionState = useMemo(() => {
    if (!liveInstance) return 'ready';
    const base = mcpConnectionState(liveInstance, { isAdmin: !isPersonal });
    if (base !== 'ready' || toolsLoad.status !== 'error') return base;
    if (toolsLoad.code === 'reauth') return usesSharedCredential(liveInstance) ? 'shared_credential_missing' : 'needs_reconnect';
    return toolsLoad.code === 'unreachable' ? 'unreachable' : base;
  }, [liveInstance, isPersonal, toolsLoad]);
  const canListTools =
    connectionState !== 'needs_connect' && connectionState !== 'waiting_for_admin' && connectionState !== 'shared_credential_missing';

  useEffect(() => {
    if (open && mode === 'edit' && activeTab === 'tools' && canListTools && toolsLoad.status === 'idle') {
      void loadTools(true);
    }
  }, [open, mode, activeTab, canListTools, toolsLoad.status, loadTools]);

  // A personal server's rules are its owner's; an organization server's are the company floor.
  const rulesTarget: McpToolRulesTarget = useMemo(
    () => ({ kind: isPersonal ? 'personal' : 'company', instanceId: liveId ?? '' }),
    [isPersonal, liveId]
  );
  const ruleTools = useMemo(() => ruleToolsFromInfo(toolsLoad.status === 'ready' ? toolsLoad.tools : []), [toolsLoad]);
  const rulesEditor = useToolRulesEditor(rulesTarget, ruleTools, open && mode === 'edit' && Boolean(liveId));

  // Unsaved rule changes are asked about before the panel closes.
  const requestOpenChange = (next: boolean) => {
    if (!next && mode === 'edit' && rulesEditor.changes > 0) {
      setConfirmDiscard(true);
      return;
    }
    onOpenChange(next);
  };

  // A new sign-in (Reauthenticate) makes a failed list worth reading again.
  const connectedAt = liveInstance?.connectedAt;
  useEffect(() => {
    setToolsLoad((prev) => (prev.status === 'error' ? { status: 'idle' } : prev));
  }, [connectedAt]);

  const createdBy = liveInstance?.createdBy;
  const creatorHidden = isMcpInstanceReadOnly(liveInstance);
  useEffect(() => {
    if (!open || !createdBy || creatorHidden) {
      setCreatorName(null);
      return;
    }
    let cancelled = false;
    apiClient
      .post('/api/v1/users/by-ids', { userIds: [createdBy] })
      .then(({ data }) => {
        if (cancelled) return;
        const users = Array.isArray(data) ? data : data?.users ?? [];
        const user = (users[0] ?? {}) as Record<string, unknown>;
        const fullName = String(user.name ?? user.fullName ?? '').trim();
        setCreatorName(fullName || createdBy);
      })
      .catch(() => {
        if (!cancelled) setCreatorName(createdBy);
      });
    return () => {
      cancelled = true;
    };
  }, [open, createdBy, creatorHidden]);

  const menuEntries: McpMenuEntry[] = [];
  if (mode === 'edit' && liveInstance) {
    const perPerson = liveInstance.authMode !== 'none' && !usesSharedCredential(liveInstance);
    const canDisconnect = perPerson && liveInstance.isAuthenticated;
    const canRemove = !isReadOnly;
    if (canDisconnect) {
      menuEntries.push({
        kind: 'item',
        id: 'reauthenticate',
        icon: 'autorenew',
        label: t('workspace.mcpServers.cta.reauthenticate'),
        onSelect: () => onReauthenticate(liveInstance),
      });
    }
    if (liveInstance.url) {
      const serverUrl = liveInstance.url;
      menuEntries.push({
        kind: 'item',
        id: 'copy-url',
        icon: 'content_copy',
        label: t('workspace.mcpServers.configPanel.menu.copyUrl'),
        onSelect: () =>
          void copy(serverUrl).then((ok) =>
            ok
              ? toast.success(t('workspace.mcpServers.configPanel.menu.copied'))
              : toast.error(t('workspace.mcpServers.configPanel.menu.copyFailed'))
          ),
      });
    }
    if (creatorName) {
      menuEntries.push({
        kind: 'info',
        id: 'added-by',
        icon: 'person',
        label: t('workspace.mcpServers.configPanel.menu.addedBy', { name: creatorName }),
      });
    }
    if (canDisconnect || canRemove) menuEntries.push({ kind: 'separator', id: 'sep' });
    if (canDisconnect) {
      menuEntries.push({
        kind: 'item',
        id: 'disconnect',
        icon: 'link_off',
        label: t('workspace.mcpServers.cta.disconnect'),
        description: t('workspace.mcpServers.configPanel.menu.disconnectHint'),
        danger: true,
        onSelect: () => setDisconnectTarget(liveInstance),
      });
    }
    if (canRemove && editingInstance) {
      const target = editingInstance;
      menuEntries.push({
        kind: 'item',
        id: 'remove',
        icon: 'delete',
        label: t('workspace.mcpServers.configPanel.menu.remove'),
        description: t(
          isPersonal ? 'workspace.mcpServers.configPanel.menu.removeHintPersonal' : 'workspace.mcpServers.configPanel.menu.removeHint'
        ),
        danger: true,
        onSelect: () => onRequestDelete(target),
      });
    }
  }

  const panelTitle =
    mode === 'edit'
      ? t('workspace.mcpServers.configPanel.editTitle')
      : t('workspace.mcpServers.configPanel.createTitle');

  const apiTokenLabel = resolvedTemplate?.authHint?.label || t('workspace.mcpServers.form.apiToken');
  const apiTokenPlaceholder = resolvedTemplate?.authHint?.placeholder ?? undefined;
  const credentialsPlaceholder = mode === 'edit' ? t('form.leaveBlankToKeep') : undefined;

  const showFooter = mode === 'create' || (activeTab === 'configuration' && !isReadOnly);

  const configurationForm = (
    <Flex direction="column" gap="4">
      <McpInheritedCallout instance={editingInstance} />
      <McpDisabledCallout instance={liveInstance} />

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
            options={transportOptions}
          />
          {!customStdioAllowed && !isPersonal && mode === 'create' && (
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.stdioPolicy.unavailableHint', { flag: MCP_CUSTOM_STDIO_FLAG })}
            </Text>
          )}
        </FormField>
      )}

      {!isTemplateBased && transport === 'stdio' && customStdioAllowed && (
        <Callout.Root color="amber" variant="surface" size="1">
          <Callout.Icon>
            <MaterialIcon name="warning" size={16} />
          </Callout.Icon>
          <Callout.Text size="1">{t('workspace.mcpServers.stdioPolicy.enabledWarning')}</Callout.Text>
        </Callout.Root>
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
          <FormField label={t('workspace.mcpServers.form.args')} optional>
            <TagInput tags={argTags} onTagsChange={setArgTags} placeholder={t('workspace.mcpServers.form.argsPlaceholder')} />
          </FormField>
          <FormField label={t('workspace.mcpServers.form.requiredEnv')} optional>
            <TagInput
              tags={requiredEnvTags}
              onTagsChange={setRequiredEnvTags}
              placeholder={t('workspace.mcpServers.form.requiredEnvPlaceholder')}
            />
            {authMode === 'api_token' && requiredEnvTags.length === 0 && (
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {t('workspace.mcpServers.form.defaultTokenEnvHint', { name: 'API_TOKEN' })}
              </Text>
            )}
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
                    type={isSecretFieldName(envKey) ? 'password' : 'text'}
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
                callbackUrl={resolveMcpOAuthCallbackUrl(dcrProbe, existingOAuthConfig)}
                dcrProbe={dcrProbe}
                dcrSupported={dcrSupported}
                documentationUrl={resolvedTemplate?.documentationUrl}
              />
              {mode === 'edit' &&
                hasExistingOAuthClient &&
                revealAvailable &&
                !isReadOnly &&
                !oauthClientRevealed && (
                  <Flex justify="end">
                    <ShowStoredValuesButton
                      loading={revealingOAuthClient}
                      onClick={() => void handleRevealOAuthClient()}
                    />
                  </Flex>
                )}
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
                  type={showOauthClientSecret ? 'text' : 'password'}
                  value={oauthClientSecret}
                  onChange={(e) => setOauthClientSecret(e.target.value)}
                  placeholder={credentialsPlaceholder ?? t('workspace.mcpServers.oauthConfig.clientSecretPlaceholder')}
                >
                  <TextField.Slot side="right">
                    <IconButton
                      type="button"
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={() => setShowOauthClientSecret((v) => !v)}
                      style={{ cursor: 'pointer', ...(isMobile ? { minWidth: 44, minHeight: 44 } : null) }}
                    >
                      <MaterialIcon
                        name={showOauthClientSecret ? 'visibility_off' : 'visibility'}
                        size={16}
                        color="var(--gray-10)"
                      />
                    </IconButton>
                  </TextField.Slot>
                </TextField.Root>
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

      <Flex direction="column" gap="3">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.mcpServers.form.timeoutsHeading')}
        </Text>
        <Flex gap="3">
          <FormField
            label={t('workspace.mcpServers.form.connectTimeout')}
            optional
            error={
              connectTimeoutInput.valid
                ? undefined
                : t('workspace.mcpServers.form.timeoutRange', MCP_TIMEOUT_LIMITS.connect)
            }
          >
            <TextField.Root
              size="2"
              type="number"
              inputMode="numeric"
              value={connectTimeout}
              onChange={(e) => setConnectTimeout(e.target.value)}
              placeholder="15"
            />
          </FormField>
          <FormField
            label={t('workspace.mcpServers.form.callTimeout')}
            optional
            error={
              callTimeoutInput.valid ? undefined : t('workspace.mcpServers.form.timeoutRange', MCP_TIMEOUT_LIMITS.call)
            }
          >
            <TextField.Root
              size="2"
              type="number"
              inputMode="numeric"
              value={callTimeout}
              onChange={(e) => setCallTimeout(e.target.value)}
              placeholder="60"
            />
          </FormField>
        </Flex>
      </Flex>

      {mode === 'create' && authMode !== 'none' && !useAdminAuth && (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.mcpServers.form.connectAfterCreateHint')}
        </Text>
      )}
    </Flex>
  );

  const impactMessageKey =
    pendingImpact === 'shared_credential'
      ? 'workspace.mcpServers.editImpact.sharedBody'
      : isPersonal
        ? 'workspace.mcpServers.editImpact.personalBody'
        : 'workspace.mcpServers.editImpact.body';

  return (
    <>
      <WorkspaceRightPanel
        open={open}
        onOpenChange={requestOpenChange}
        title={mode === 'edit' && liveInstance ? liveInstance.name : panelTitle}
        icon={mode === 'edit' && liveInstance ? undefined : 'hub'}
        titleNode={
          mode === 'edit' && liveInstance ? (
            <McpPanelTitle instance={liveInstance} state={isMcpInstanceDisabled(liveInstance) ? 'disabled' : connectionState} />
          ) : undefined
        }
        primaryLabel={mode === 'edit' ? t('common.save') : t('common.create')}
        secondaryLabel={t('common.cancel')}
        primaryDisabled={!isValid}
        primaryLoading={isSaving}
        onPrimaryClick={handleSave}
        hideFooter={!showFooter}
        headerActions={
          menuEntries.length > 0 ? <McpServerActionsMenu entries={menuEntries} container={nestedModalHost} /> : undefined
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
              <Tabs.Trigger value="tools">
                {t('workspace.mcpServers.configPanel.tabs.tools')}
              </Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="configuration" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
              {configurationForm}
            </Tabs.Content>

            <Tabs.Content value="tools" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              {liveInstance ? (
                <McpToolsApprovalsTab
                  instance={liveInstance}
                  state={connectionState}
                  load={toolsLoad}
                  editor={rulesEditor}
                  readOnly={isMcpInstanceReadOnly(liveInstance)}
                  isBusy={busyInstanceId === liveInstance._id}
                  onRefresh={() => void loadTools(false)}
                  onAuthenticate={() => onAuthenticate(liveInstance)}
                  onReauthenticate={() => onReauthenticate(liveInstance)}
                />
              ) : (
                <Text size="2" style={{ color: 'var(--gray-10)' }}>
                  {t('workspace.mcpServers.form.connectAfterCreateHint')}
                </Text>
              )}
            </Tabs.Content>
          </Tabs.Root>
        ) : (
          configurationForm
        )}
      </WorkspaceRightPanel>

      <ConfirmationDialog
        open={pendingImpact !== 'none'}
        onOpenChange={(next) => {
          if (!next) setPendingImpact('none');
        }}
        title={t('workspace.mcpServers.editImpact.title')}
        message={t(impactMessageKey)}
        confirmLabel={t('workspace.mcpServers.editImpact.confirm')}
        confirmVariant="danger"
        onConfirm={() => {
          setPendingImpact('none');
          void save();
        }}
        container={nestedModalHost}
      />

      <McpDisconnectDialog
        instance={disconnectTarget}
        onOpenChange={(next) => {
          if (!next) setDisconnectTarget(null);
        }}
        onConfirm={() => {
          if (disconnectTarget) onDisconnect(disconnectTarget);
          setDisconnectTarget(null);
        }}
        container={nestedModalHost}
      />

      <ConfirmationDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        title={t('workspace.mcpServers.configPanel.discardTitle')}
        message={t('workspace.mcpServers.configPanel.discardBody', { count: rulesEditor.changes })}
        confirmLabel={t('workspace.mcpServers.configPanel.discard')}
        confirmVariant="danger"
        onConfirm={() => {
          rulesEditor.discard();
          setConfirmDiscard(false);
          onOpenChange(false);
        }}
        container={nestedModalHost}
      />
    </>
  );
}

// ========================================
// OAuth callback URL card
// ========================================

function McpOAuthCallbackUrlCard({
  callbackUrl,
  dcrProbe,
  dcrSupported,
  documentationUrl,
}: {
  callbackUrl: string | null;
  dcrProbe: DcrProbeState;
  dcrSupported: boolean | null;
  documentationUrl?: string | null;
}) {
  const { t } = useTranslation();

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
