'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertDialog,
  Badge,
  Button,
  Callout,
  Flex,
  IconButton,
  Separator,
  Text,
  TextField,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  ToolsetsApi,
  type BuilderSidebarToolset,
  type ToolsetOauthConfigListRow,
} from '@/app/(main)/toolsets/api';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { SchemaField } from '@/app/(main)/workspace/connectors/types';
import {
  apiErrorDetail,
  configureAuthFieldsForType,
  getToolsetAuthConfigFromSchema,
} from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import { toolNamesFromSchema } from '../utils/tool-names-from-schema';
import { isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
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
  const nestedModalHost = useWorkspaceDrawerNestedModalHost(true);
  const { t } = useTranslation();
  const instanceId = instance.instanceId ?? '';
  const toolsetType = (instance.toolsetType || '').trim();
  const authType = (instance.authType || 'NONE').toUpperCase();
  const oauth = isOAuthType(authType);

  const [schemaRaw, setSchemaRaw] = useState<unknown>(null);
  const [oauthConfigs, setOauthConfigs] = useState<ToolsetOauthConfigListRow[]>([]);

  const [instanceName, setInstanceName] = useState(instance.instanceName || instance.displayName || '');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [initialClientId, setInitialClientId] = useState('');
  const [initialClientSecret, setInitialClientSecret] = useState('');
  const [secretInitiallySet, setSecretInitiallySet] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  /** OAuth configure fields from schema except client id/secret (those stay explicit for admins). */
  const [oauthExtraValues, setOauthExtraValues] = useState<Record<string, unknown>>({});

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const toolNames = useMemo(() => {
    const fromSchema = toolNamesFromSchema(schemaRaw);
    if (fromSchema.length) return fromSchema;
    return (instance.tools || []).map((x) => x.name).filter(Boolean);
  }, [schemaRaw, instance.tools]);

  const oauthConfigureExtras = useMemo(() => {
    if (!oauth) return [];
    const ac = getToolsetAuthConfigFromSchema(schemaRaw);
    return configureAuthFieldsForType(ac, 'OAUTH').filter(
      (f) => !['clientid', 'clientsecret'].includes(f.name.toLowerCase())
    );
  }, [oauth, schemaRaw]);

  const redirectUri = useMemo(() => {
    if (typeof window === 'undefined') return '';
    return toolsetRedirectUri(window.location.origin, toolsetType);
  }, [toolsetType]);

  const linkedOauthName = useMemo(() => {
    const row = resolveLinkedOauthConfig(oauthConfigs, instance);
    return (
      row?.oauthInstanceName || instance.instanceName || instance.displayName || ''
    );
  }, [instance, oauthConfigs]);

  useEffect(() => {
    setInstanceName(instance.instanceName || instance.displayName || '');
  }, [instance.instanceName, instance.displayName]);

  useEffect(() => {
    setOauthExtraValues({});
  }, [instance.instanceId]);

  useEffect(() => {
    if (!toolsetType) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const s = await ToolsetsApi.getToolsetRegistrySchema(toolsetType);
        if (!cancelled) setSchemaRaw(s);
      } catch {
        if (!cancelled) setSchemaRaw(null);
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
      try {
        const list = await ToolsetsApi.listToolsetOAuthConfigs(toolsetType);
        if (cancelled) return;
        setOauthConfigs(list);
        const row = resolveLinkedOauthConfig(list, instance);
        const cid = row?.clientId?.trim() ?? '';
        setClientId(cid);
        setInitialClientId(cid);
        const loadedSecret = row?.clientSecret?.trim() ?? '';
        setClientSecret(loadedSecret);
        setInitialClientSecret(loadedSecret);
        setSecretInitiallySet(Boolean(row?.clientSecretSet) || loadedSecret.length > 0);
      } catch {
        if (!cancelled) setOauthConfigs([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    oauth,
    toolsetType,
    instance.instanceId,
    instance.oauthConfigId,
    instance.instanceName,
    instance.displayName,
  ]);

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

  const showOauthImpactCallout =
    oauth &&
    (clientId.trim() !== initialClientId.trim() ||
      clientSecret.trim() !== initialClientSecret.trim());

  const copyRedirect = useCallback(async () => {
    if (!redirectUri) return;
    try {
      await navigator.clipboard.writeText(redirectUri);
      onNotify?.(t('workspace.actions.redirectUriCopied'));
    } catch {
      setError(t('workspace.actions.manage.copyFailed'));
    }
  }, [onNotify, redirectUri, t]);

  const handleSave = useCallback(async () => {
    if (!instanceId) return;
    const name = instanceName.trim();
    if (!name) {
      setError(t('workspace.actions.errors.instanceNameRequired'));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (oauth) {
        const authConfig: Record<string, unknown> = { type: 'OAUTH' };
        oauthConfigureExtras.forEach((f) => {
          const v = oauthExtraValues[f.name];
          if (v !== undefined && v !== null && String(v).trim() !== '') {
            authConfig[f.name] = v;
          }
        });
        if (clientId.trim()) authConfig.clientId = clientId.trim();
        if (clientSecret.trim()) authConfig.clientSecret = clientSecret.trim();
        const res = await ToolsetsApi.updateToolsetInstance(instanceId, {
          instanceName: name,
          authConfig,
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
      await ToolsetsApi.updateToolsetInstance(instanceId, { instanceName: name });
      onNotify?.(t('workspace.actions.manage.saveSuccess'));
      onSaved();
      onClose();
    } catch (e) {
      setError(apiErrorDetail(e));
    } finally {
      setSaving(false);
    }
  }, [
    clientId,
    clientSecret,
    instanceId,
    instanceName,
    oauth,
    oauthConfigureExtras,
    oauthExtraValues,
    onClose,
    onNotify,
    onSaved,
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

          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" color="gray">
              {t('workspace.actions.manage.oauthAppHeading')}
            </Text>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {linkedOauthName}
            </Text>
          </Flex>
        </>
      ) : null}

      <Flex direction="column" gap="2">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.actions.instanceName')}
        </Text>
        <TextField.Root
          value={instanceName}
          onChange={(e) => setInstanceName(e.target.value)}
          placeholder={t('workspace.actions.instanceNamePlaceholder')}
        />
      </Flex>

      {oauth ? (
        <>
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.actions.oauthClientId')}
            </Text>
            <TextField.Root value={clientId} onChange={(e) => setClientId(e.target.value)} />
            <Text size="1" color="gray">
              {t('workspace.actions.manage.clientIdHint')}
            </Text>
          </Flex>
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.actions.oauthClientSecret')}
            </Text>
            <TextField.Root
              type={showSecret ? 'text' : 'password'}
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder={
                secretInitiallySet ? t('workspace.actions.manage.secretPlaceholder') : undefined
              }
            >
              <TextField.Slot side="right">
                <IconButton
                  type="button"
                  variant="ghost"
                  color="gray"
                  size="1"
                  aria-label={showSecret ? t('workspace.actions.manage.hideSecret') : t('workspace.actions.manage.showSecret')}
                  onClick={() => setShowSecret((s) => !s)}
                >
                  <MaterialIcon name={showSecret ? 'visibility_off' : 'visibility'} size={16} color="var(--gray-11)" />
                </IconButton>
              </TextField.Slot>
            </TextField.Root>
            <Text size="1" color="gray">
              {t('workspace.actions.manage.clientSecretHint')}
            </Text>
          </Flex>
          {oauthConfigureExtras.length > 0 ? (
            <Flex direction="column" gap="3" mt="1">
              {oauthConfigureExtras.map((field) => (
                <SchemaFormField
                  key={field.name}
                  field={field as SchemaField}
                  value={oauthExtraValues[field.name]}
                  onChange={(name, val) => setOauthExtraValues((p) => ({ ...p, [name]: val }))}
                  selectPortalZIndex={WORKSPACE_DRAWER_POPPER_Z_INDEX}
                />
              ))}
            </Flex>
          ) : null}
        </>
      ) : (
        <Callout.Root color="blue">
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Callout.Text>{t('workspace.actions.manage.nonOAuthHint')}</Callout.Text>
        </Callout.Root>
      )}

      {oauth && showOauthImpactCallout ? (
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
        <Button type="button" variant="soft" color="gray" loading={authenticating} onClick={() => void handleAuthenticate()}>
          {t('workspace.actions.cta.authenticate')}
        </Button>
        <Button type="button" color="jade" loading={saving} onClick={() => void handleSave()}>
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
