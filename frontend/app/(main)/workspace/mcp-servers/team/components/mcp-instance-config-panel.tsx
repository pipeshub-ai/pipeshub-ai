'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Callout, Flex, Grid, IconButton, RadioGroup, Text, TextField, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ShowStoredValuesButton } from '@/app/components/ui/show-stored-values-button';
import { toast } from '@/lib/store/toast-store';
import { useRevealScope, useSecretRevealAvailable } from '@/lib/hooks/use-secret-reveal-available';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { isProcessedError } from '@/lib/api';
import { isMcpInstanceReadOnly, McpInheritedCallout, McpManageAccess } from '@/config';
import { useWorkspaceDrawerNestedModalHost, WorkspaceRightPanel } from '../../../components/workspace-right-panel';
import {
  ConfirmationDialog,
  FormField,
  FormFieldTextField,
  SelectDropdown,
  TagInput,
  type TagItem,
} from '../../../components';
import { McpServersApi } from '../../api';
import { McpDisabledCallout, isMcpInstanceDisabled } from '../../components';
import { mcpConnectionState } from '../../connection-state';
import { freeServerName, serverNameKey } from '../../server-names';
import { McpPanelTitle } from './mcp-panel-header';
import { McpClientSecretPrefixHint } from './mcp-client-secret-prefix-hint';
import { McpLogoField } from './mcp-logo-field';
import { setMcpServerLogo, useMcpServerLogo } from '../../logo-store';
import { useMcpDeploymentStore } from '../../hub/deployment';
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
import { McpInstanceConfigExtras } from '@/config';
import { SecretTextField } from '@/app/components/ui/secret-text-field';

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
  /** `created` is true for a new server, which the page then opens. */
  onSaved: (saved: { id: string; created: boolean }) => void;
  /** Where a new instance is created; an existing one keeps its own. Defaults to `org`. */
  scope?: McpInstanceScope;
}

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
  scope = 'org',
}: McpInstanceConfigPanelProps) {
  const { t } = useTranslation();
  const { open, mode, editingInstance, prefillTemplate } = state;
  // The backend refuses a local command or a shared admin credential on a personal instance.
  const isPersonal = mode === 'edit' ? isPersonalMcpInstance(editingInstance) : scope === 'personal';

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
  const adminInstance =
    open && mode === 'edit' && editingInstance && !isReadOnly && !isPersonal
      ? {
          instanceId: editingInstance._id,
          transport: editingInstance.transport,
          insideOrganization: liveInstance?.insideOrganization === true,
        }
      : null;
  // Set by a Manage Access section that saves who may use an organization server.
  const manageAccessSave = useRef<((serverId: string) => Promise<void>) | null>(null);
  // The server a save in this panel created: a retry after a later step failed updates it instead of creating another.
  const createdInstanceId = useRef<string | null>(null);
  // Bumped whenever the panel opens on a server: a save started before that touches nothing after it.
  const saveSession = useRef(0);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [transport, setTransport] = useState<McpTransport>('stdio');
  const [authMode, setAuthMode] = useState<McpAuthMode>('none');
  const [useAdminAuth, setUseAdminAuth] = useState(false);
  const [command, setCommand] = useState('');
  const [argTags, setArgTags] = useState<TagItem[]>([]);
  const [requiredEnvTags, setRequiredEnvTags] = useState<TagItem[]>([]);
  const [egressHostTags, setEgressHostTags] = useState<TagItem[]>([]);
  const stdioRunners = useMcpDeploymentStore((s) => s.stdioRunners);
  const [url, setUrl] = useState('');
  const [headerName, setHeaderName] = useState('');
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [tokenUrl, setTokenUrl] = useState('');
  const [scopeTags, setScopeTags] = useState<TagItem[]>([]);
  const [connectTimeout, setConnectTimeout] = useState('');
  const [callTimeout, setCallTimeout] = useState('');
  const savedLogo = useMcpServerLogo(mode === 'edit' ? editingInstance?._id : null);
  /** undefined: unchanged; null: remove it; a data URL: the new logo. */
  const [logoDraft, setLogoDraft] = useState<string | null | undefined>(undefined);
  const [isReadingLogo, setIsReadingLogo] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
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

  useEffect(() => {
    if (!open) return;
    createdInstanceId.current = null;
    saveSession.current += 1;
    setIsSaving(false);
    setPendingImpact('none');
    setLogoDraft(undefined);
    setIsReadingLogo(false);
    if (editingInstance) {
      setName(editingInstance.name);
      setDescription(editingInstance.description ?? '');
      setTransport(editingInstance.transport);
      setAuthMode(editingInstance.authMode);
      setUseAdminAuth(editingInstance.useAdminAuth);
      setCommand(editingInstance.command ?? '');
      setArgTags(stringsToTags(editingInstance.args ?? []));
      setRequiredEnvTags(stringsToTags(editingInstance.requiredEnv ?? []));
      setEgressHostTags(stringsToTags(editingInstance.declaredEgressHosts ?? []));
      setUrl(editingInstance.url ?? '');
      setHeaderName(editingInstance.headerName ?? '');
      setAuthorizationUrl(editingInstance.authorizationUrl ?? '');
      setTokenUrl(editingInstance.tokenUrl ?? '');
      setScopeTags(stringsToTags(editingInstance.scopes ?? []));
      setConnectTimeout(editingInstance.connectTimeoutSeconds ? String(editingInstance.connectTimeoutSeconds) : '');
      setCallTimeout(editingInstance.callTimeoutSeconds ? String(editingInstance.callTimeoutSeconds) : '');
      setShowAdvanced(
        Boolean(
          editingInstance.description ||
            editingInstance.connectTimeoutSeconds ||
            editingInstance.callTimeoutSeconds ||
            editingInstance.authorizationUrl ||
            editingInstance.tokenUrl ||
            editingInstance.scopes?.length
        )
      );
    } else if (prefillTemplate) {
      setShowAdvanced(false);
      setName(freeServerName(prefillTemplate.displayName, instances.map((i) => i.name)));
      setDescription(prefillTemplate.description);
      setTransport(prefillTemplate.transport);
      setAuthMode(prefillTemplate.defaultAuthMode);
      setUseAdminAuth(false);
      setCommand(prefillTemplate.command ?? '');
      setArgTags(stringsToTags(prefillTemplate.args ?? []));
      setRequiredEnvTags(stringsToTags(prefillTemplate.requiredEnv ?? []));
      setEgressHostTags([]);
      setUrl(prefillTemplate.defaultUrl ?? '');
      setHeaderName('');
      setAuthorizationUrl(prefillTemplate.authorizationUrl ?? '');
      setTokenUrl(prefillTemplate.tokenUrl ?? '');
      setScopeTags(stringsToTags(prefillTemplate.defaultScopes ?? []));
      setConnectTimeout('');
      setCallTimeout('');
    } else {
      setShowAdvanced(false);
      setName('');
      setDescription('');
      setTransport(customStdioAllowedRef.current && !isPersonal ? 'stdio' : 'streamable_http');
      setAuthMode('none');
      setUseAdminAuth(false);
      setCommand('');
      setArgTags([]);
      setRequiredEnvTags([]);
      setEgressHostTags([]);
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
  const urlEditable = Boolean(resolvedTemplate?.urlEditable);
  const oauthProbeUrl = urlEditable || !isTemplateBased
    ? url.trim()
    : (resolvedTemplate?.defaultUrl ?? editingInstance?.url ?? '');

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
      isTemplateBased && !urlEditable ? 0 : DCR_PROBE_DEBOUNCE_MS
    );
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [open, authMode, isTemplateBased, urlEditable, oauthProbeUrl]);

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
      setShowOauthClientSecret(true);
    } catch {
      // The fields stay blank, which still saves as "keep the stored value".
    } finally {
      setRevealingOAuthClient(false);
    }
  };
  const oauthClientRequired =
    isOauthClientRequired(authMode, dcrSupported) || Boolean(resolvedTemplate?.requireOauthClient);
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

  // The same list the backend checks: on the team page the org's servers, on the personal page
  // the org's and the person's own. A server keeping its own name is never flagged.
  const nameTaken = useMemo(() => {
    const key = serverNameKey(name);
    if (!key || (editingInstance && key === serverNameKey(editingInstance.name))) return false;
    return instances.some((i) => i._id !== editingInstance?._id && serverNameKey(i.name) === key);
  }, [name, instances, editingInstance]);

  const isValid =
    !nameTaken &&
    connectTimeoutInput.valid &&
    callTimeoutInput.valid &&
    name.trim().length > 0 &&
    (isTemplateBased
      ? !urlEditable || url.trim().length > 0
      : transport === 'stdio'
        ? command.trim().length > 0
        : url.trim().length > 0) &&
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

  const saveLogoIfChanged = async (instanceId: string) => {
    if (isTemplateBased || logoDraft === undefined || logoDraft === savedLogo) return;
    try {
      if (logoDraft) {
        const { logo } = await McpServersApi.setLogo(instanceId, logoDraft);
        setMcpServerLogo(instanceId, logo);
      } else {
        await McpServersApi.removeLogo(instanceId);
        setMcpServerLogo(instanceId, null);
      }
    } catch (error) {
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.logoSaveError'), detail ? { description: detail } : undefined);
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
      ? urlEditable
        ? { url: url.trim() }
        : {}
      : transport === 'stdio'
        ? {
            command: command.trim(),
            args: tagsToStrings(argTags),
            requiredEnv: tagsToStrings(requiredEnvTags),
            egressHosts: tagsToStrings(egressHostTags),
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
    if (!isValid || isReadingLogo) return;
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
    const session = saveSession.current;
    // Every later step reads this session's form, so a save whose session ended stops here.
    const ended = () => session !== saveSession.current;
    setIsSaving(true);
    try {
      const payload = buildPayload();
      let instanceId: string;
      let reportSaved: () => void;
      if (mode === 'edit' && editingInstance) {
        const updated = await McpServersApi.updateInstance(editingInstance._id, payload);
        if (ended()) return;
        instanceId = updated._id;
        reportSaved = updated.credentialsReset
          ? () => toast.warning(t('workspace.mcpServers.toasts.credentialsReset'))
          : () => toast.success(t('workspace.mcpServers.toasts.updated'));
      } else {
        instanceId = createdInstanceId.current
          ? (await McpServersApi.updateInstance(createdInstanceId.current, payload))._id
          : (await McpServersApi.createInstance(payload))._id;
        if (ended()) return;
        createdInstanceId.current = instanceId;
        reportSaved = () => toast.success(t('workspace.mcpServers.toasts.created'));
      }

      await saveCredentialsIfProvided(instanceId);
      if (ended()) return;
      if (!isPersonal && manageAccessSave.current) {
        try {
          await manageAccessSave.current(instanceId);
        } catch (error) {
          if (ended()) return;
          // The server is saved but the access chosen here is not; stay open so a retry can apply it.
          const detail = isProcessedError(error) ? error.message : undefined;
          toast.error(t('workspace.mcpServers.toasts.accessSaveError'), detail ? { description: detail } : undefined);
          return;
        }
      }
      await saveLogoIfChanged(instanceId);
      if (ended()) return;

      reportSaved();
      onSaved({ id: instanceId, created: mode === 'create' });
      onOpenChange(false);
    } catch (error) {
      if (ended()) return;
      const detail = isProcessedError(error) ? error.message : undefined;
      toast.error(t('workspace.mcpServers.toasts.saveError'), detail ? { description: detail } : undefined);
    } finally {
      if (!ended()) setIsSaving(false);
    }
  };

  const connectionState = liveInstance ? mcpConnectionState(liveInstance, { isAdmin: !isPersonal }) : 'ready';

  const panelTitle =
    mode === 'edit'
      ? t('workspace.mcpServers.configPanel.editTitle')
      : prefillTemplate
        ? t('workspace.mcpServers.configPanel.addTemplateTitle', { name: prefillTemplate.displayName })
        : t('workspace.mcpServers.configPanel.createTitle');

  const apiTokenLabel = resolvedTemplate?.authHint?.label || t('workspace.mcpServers.form.apiToken');
  const apiTokenPlaceholder = resolvedTemplate?.authHint?.placeholder ?? undefined;
  const credentialsPlaceholder = mode === 'edit' ? t('form.leaveBlankToKeep') : undefined;

  const showFooter = mode === 'create' || !isReadOnly;
  // A provider that registers PipesHub by itself needs no OAuth client, so its fields wait under Advanced.
  const oauthClientOptional =
    authMode === 'oauth' && !oauthClientRequired && dcrSupported === true && !(mode === 'edit' && hasExistingOAuthClient);
  // Held open while it holds what blocks Save (so the reason stays visible) or a logo still being
  // read (closing it would unmount the field and drop the read).
  const advancedHeldOpen =
    !connectTimeoutInput.valid || !callTimeoutInput.valid || (oauthClientOptional && Boolean(oauthPairError)) || isReadingLogo;
  const advancedOpen = showAdvanced || advancedHeldOpen;

  const credentialsBox = (
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
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.mcpServers.form.credentialsHint')}
        </Text>
      </Flex>

      {authMode === 'api_token' && multiEnvAuth &&
        envKeys.map((envKey) => {
          const isRequired = effectiveRequiredEnv.includes(envKey);
          return (
            <FormField key={envKey} label={envKey} optional={!isRequired}>
              <SecretTextField
                size="2"
                masked={isSecretFieldName(envKey)}
                value={envValues[envKey] ?? ''}
                onChange={(e) => setEnvValues((prev) => ({ ...prev, [envKey]: e.target.value }))}
                placeholder={credentialsPlaceholder}
              />
            </FormField>
          );
        })}

      {authMode === 'api_token' && !multiEnvAuth && (
        <FormField label={apiTokenLabel} optional>
          <SecretTextField
            size="2"
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
          <SecretTextField
            size="2"
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
            <FormFieldTextField
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
            <SecretTextField
              size="2"
              masked={!showOauthClientSecret}
              value={oauthClientSecret}
              onChange={(e) => setOauthClientSecret(e.target.value)}
              placeholder={credentialsPlaceholder ?? t('workspace.mcpServers.oauthConfig.clientSecretPlaceholder')}
            >
              <TextField.Slot side="right">
                <IconButton
                  aria-label={showOauthClientSecret ? t('common.hideValue') : t('common.showValue')}
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
                    color="var(--gray-11)"
                  />
                </IconButton>
              </TextField.Slot>
            </SecretTextField>
            <McpClientSecretPrefixHint prefix={resolvedTemplate?.clientSecretPrefix} secret={oauthClientSecret} />
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
  );

  const configurationForm = (
    <Flex direction="column" gap="4">
      <McpInheritedCallout instance={editingInstance} />
      <McpDisabledCallout instance={liveInstance} />

      <FormField
        label={t('workspace.mcpServers.form.name')}
        required
        error={nameTaken ? t('workspace.mcpServers.form.nameTaken') : undefined}
      >
        <FormFieldTextField
          size="2"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={nameTaken || undefined}
        />
      </FormField>

      {!isTemplateBased && (
        <FormField label={t('workspace.mcpServers.form.transport')} required>
          <SelectDropdown
            value={transport}
            onChange={(v) => setTransport(v as McpTransport)}
            options={transportOptions}
          />
          {!customStdioAllowed && !isPersonal && mode === 'create' && (
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
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
            <FormFieldTextField
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
              <Text size="1" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.mcpServers.form.defaultTokenEnvHint', { name: 'API_TOKEN' })}
              </Text>
            )}
          </FormField>
          {stdioRunners && (
            <FormField label={t('workspace.mcpServers.form.egressHosts')} optional>
              <TagInput
                tags={egressHostTags}
                onTagsChange={setEgressHostTags}
                placeholder={t('workspace.mcpServers.form.egressHostsPlaceholder')}
              />
              <Text size="1" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.mcpServers.form.egressHostsHint')}
              </Text>
            </FormField>
          )}
        </>
      )}

      {(urlEditable || (!isTemplateBased && transport !== 'stdio')) && (
        <FormField label={t('workspace.mcpServers.form.url')} required>
          <FormFieldTextField
            size="2"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={urlEditable ? 'https://your-company.okta.com/mcp' : 'https://example.com/mcp'}
          />
          {urlEditable && resolvedTemplate?.authHint?.helpText && (
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {resolvedTemplate.authHint.helpText}
            </Text>
          )}
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
          <FormFieldTextField
            size="2"
            value={headerName}
            onChange={(e) => setHeaderName(e.target.value)}
            placeholder="Authorization"
          />
        </FormField>
      )}

      {canUseAdminAuth && (
        <FormField label={t('workspace.mcpServers.form.whoSignsIn')} required>
          <RadioGroup.Root
            value={useAdminAuth ? 'shared' : 'personal'}
            onValueChange={(v) => setUseAdminAuth(v === 'shared')}
            aria-label={t('workspace.mcpServers.form.whoSignsIn')}
          >
            <Flex direction="column" gap="2">
              {(['personal', 'shared'] as const).map((choice) => {
                const on = (choice === 'shared') === useAdminAuth;
                return (
                  <Text
                    as="label"
                    key={choice}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 'var(--space-3)',
                      padding: 'var(--space-3)',
                      borderRadius: 'var(--radius-2)',
                      border: `1px solid ${on ? 'var(--accent-7)' : 'var(--olive-4)'}`,
                      background: on ? 'var(--accent-2)' : 'var(--olive-2)',
                      cursor: 'pointer',
                    }}
                  >
                    <RadioGroup.Item value={choice} style={{ marginTop: 2, flexShrink: 0 }} />
                    <Flex direction="column" gap="1">
                      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                        {t(`workspace.mcpServers.form.signIn.${choice}`)}
                      </Text>
                      <Text size="1" style={{ color: 'var(--gray-11)' }}>
                        {t(`workspace.mcpServers.form.signIn.${choice}Hint`)}
                      </Text>
                    </Flex>
                  </Text>
                );
              })}
            </Flex>
          </RadioGroup.Root>
        </FormField>
      )}

      {authMode !== 'none' && !oauthClientOptional && credentialsBox}

      <Box>
        <button
          type="button"
          className="touch-hit"
          aria-expanded={advancedOpen}
          // While held open a click can't close it, and it stays open once the reason is fixed.
          onClick={() => setShowAdvanced(advancedHeldOpen || !advancedOpen)}
          style={{
            appearance: 'none',
            margin: 0,
            padding: 0,
            border: 'none',
            background: 'none',
            font: 'inherit',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <MaterialIcon name={advancedOpen ? 'expand_more' : 'chevron_right'} size={18} color="var(--gray-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.form.advanced')}
          </Text>
        </button>
      </Box>

      {advancedOpen && (
        <Flex direction="column" gap="4" style={{ paddingLeft: 'var(--space-3)', borderLeft: '2px solid var(--olive-4)' }}>
          {oauthClientOptional && credentialsBox}
          <FormField label={t('workspace.mcpServers.form.description')} optional>
            <FormFieldTextField size="2" value={description} onChange={(e) => setDescription(e.target.value)} />
          </FormField>

          {!isTemplateBased && !isReadOnly && (
            <McpLogoField
              key={editingInstance?._id ?? 'new'}
              logo={logoDraft === undefined ? savedLogo : logoDraft}
              onChange={setLogoDraft}
              onReadingChange={setIsReadingLogo}
            />
          )}

          {authMode === 'oauth' && !isTemplateBased && (
            <>
              <FormField label={t('workspace.mcpServers.form.authorizationUrl')} optional>
                <FormFieldTextField
                  size="2"
                  value={authorizationUrl}
                  onChange={(e) => setAuthorizationUrl(e.target.value)}
                  placeholder={t('workspace.mcpServers.form.dcrHint')}
                />
              </FormField>
              <FormField label={t('workspace.mcpServers.form.tokenUrl')} optional>
                <FormFieldTextField size="2" value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)} />
              </FormField>
              <FormField label={t('workspace.mcpServers.form.scopes')} optional>
                <TagInput tags={scopeTags} onTagsChange={setScopeTags} placeholder={t('workspace.mcpServers.form.scopesPlaceholder')} />
              </FormField>
            </>
          )}

          <Flex direction="column" gap="3">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.mcpServers.form.timeoutsHeading')}
            </Text>
            <Grid columns="2" gap="3">
              <FormField
                label={t('workspace.mcpServers.form.connectTimeout')}
                optional
                error={
                  connectTimeoutInput.valid
                    ? undefined
                    : t('workspace.mcpServers.form.timeoutRange', MCP_TIMEOUT_LIMITS.connect)
                }
              >
                <FormFieldTextField
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
                <FormFieldTextField
                  size="2"
                  type="number"
                  inputMode="numeric"
                  value={callTimeout}
                  onChange={(e) => setCallTimeout(e.target.value)}
                  placeholder="60"
                />
              </FormField>
            </Grid>
          </Flex>
        </Flex>
      )}

      {!isPersonal && (
        <McpManageAccess serverId={editingInstance?._id ?? null} disabled={isReadOnly} saveRef={manageAccessSave} />
      )}

      {adminInstance !== null && <McpInstanceConfigExtras {...adminInstance} />}
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
        onOpenChange={(next) => {
          // Closing mid-save would leave the server half set up (credentials, access, logo).
          if (next || !isSaving) onOpenChange(next);
        }}
        title={mode === 'edit' && liveInstance ? liveInstance.name : panelTitle}
        icon={mode === 'edit' && liveInstance ? undefined : 'hub'}
        titleNode={
          mode === 'edit' && liveInstance ? (
            <McpPanelTitle
              instance={liveInstance}
              state={isMcpInstanceDisabled(liveInstance) ? 'disabled' : connectionState}
              logo={savedLogo}
            />
          ) : undefined
        }
        primaryLabel={mode === 'edit' ? t('common.save') : t('common.create')}
        secondaryLabel={t('common.cancel')}
        primaryDisabled={!isValid || isReadingLogo}
        primaryLoading={isSaving}
        onPrimaryClick={handleSave}
        hideFooter={!showFooter}
      >
        {configurationForm}
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
        <Text size="1" style={{ color: 'var(--gray-11)', lineHeight: 1.5 }}>
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
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          <a href={documentationUrl} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-11)' }}>
            {t('workspace.mcpServers.oauthConfig.providerDocsLink')}
          </a>
        </Text>
      )}
    </Flex>
  );
}
