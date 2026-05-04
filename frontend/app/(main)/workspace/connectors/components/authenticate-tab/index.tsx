'use client';

import React, { useContext, useEffect, useMemo, useRef } from 'react';
import { Flex, Text, Select, Box, Separator, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '../schema-form-field';
import { useConnectorsStore } from '../../store';
import {
  isNoneAuthType,
  isOAuthType,
  shouldRenderOAuthAuthSchemaField,
  resolveOAuthFieldVisibility,
} from '../../utils/auth-helpers';
import { DocumentationSection } from './documentation-section';
import { OAuthAppSelector } from './oauth-app-selector';
import { resolveAuthFields, formatAuthTypeName } from './helpers';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { ConnectorsApi } from '../../api';
import {
  nestedConfigFromOAuthRegistrationDoc,
  readAuthValueFromFlatRecord,
  readRegistrationValueForAuthField,
} from '../../utils/oauth-registration-values';
import { getConnectorInfoText, getConnectorDocumentationUrl } from '../../utils/connector-metadata';
import { useTranslation } from 'react-i18next';

export function AuthenticateTab() {
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const addToast = useToastStore((s) => s.addToast);
  const {
    connectorSchema,
    panelConnector,
    panelConnectorId,
    connectorConfig,
    registryConnectors,
    selectedAuthType,
    isAuthTypeImmutable: _isAuthTypeImmutable,
    formData,
    formErrors,
    conditionalDisplay,
    setAuthFormValue,
    setSelectedAuthType,
  } = useConnectorsStore();

  const { t } = useTranslation();

  const isCreateMode = !panelConnectorId;

  const connectorInfoText = useMemo(() => {
    const ty = panelConnector?.type;
    if (!ty) return null;
    const fromRegistry = registryConnectors.find((c) => c.type === ty);
    return getConnectorInfoText(fromRegistry ?? panelConnector);
  }, [registryConnectors, panelConnector]);

  const currentSchemaFields = useMemo(
    () => resolveAuthFields(connectorSchema?.auth, selectedAuthType),
    [connectorSchema, selectedAuthType]
  );
  const { linkedOAuthAppId, oauthFieldVisibility } = useMemo(
    () => resolveOAuthFieldVisibility(formData.auth, connectorConfig, isCreateMode, isAdmin),
    [formData.auth, connectorConfig, isCreateMode, isAdmin]
  );

  const authFieldsForForm = useMemo(() => {
    if (selectedAuthType !== 'OAUTH') return currentSchemaFields;
    return currentSchemaFields.filter((f) => f.name !== 'oauthConfigId');
  }, [currentSchemaFields, selectedAuthType]);

  const visibleAuthFields = useMemo(() => {
    return authFieldsForForm.filter((field) => {
      if (selectedAuthType === 'OAUTH') {
        if (!shouldRenderOAuthAuthSchemaField(field.name, oauthFieldVisibility)) {
          return false;
        }
      }
      if (conditionalDisplay[field.name] !== undefined) {
        return conditionalDisplay[field.name];
      }
      return true;
    });
  }, [authFieldsForForm, selectedAuthType, oauthFieldVisibility, conditionalDisplay]);

  const oauthCredentialFieldNames = useMemo(() => {
    const auth = connectorSchema?.auth;
    if (selectedAuthType !== 'OAUTH' || !auth) return [];
    return resolveAuthFields(auth, 'OAUTH')
      .map((f) => f.name)
      .filter((n) => n !== 'oauthConfigId');
  }, [connectorSchema, selectedAuthType]);

  const oauthCredentialHydratedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    const connectorType = panelConnector?.type;
    if (
      !connectorType ||
      isCreateMode ||
      selectedAuthType !== 'OAUTH' ||
      !linkedOAuthAppId ||
      !panelConnectorId
    ) {
      oauthCredentialHydratedKeyRef.current = null;
      return;
    }

    const key = `${panelConnectorId}:${linkedOAuthAppId}`;
    if (oauthCredentialHydratedKeyRef.current === key) return;
    if (!oauthCredentialFieldNames.length) {
      oauthCredentialHydratedKeyRef.current = key;
      return;
    }

    let cancelled = false;

    const fillEmptyFromSources = (nestedRegistration: Record<string, unknown>) => {
      const cfgAuth = useConnectorsStore.getState().connectorConfig?.config?.auth as
        | Record<string, unknown>
        | undefined;
      const setVal = useConnectorsStore.getState().setAuthFormValue;
      for (const name of oauthCredentialFieldNames) {
        const cur = useConnectorsStore.getState().formData.auth[name];
        const empty =
          cur === undefined ||
          cur === null ||
          (typeof cur === 'string' && String(cur).trim() === '');
        if (!empty) continue;
        const fromConfig = readAuthValueFromFlatRecord(cfgAuth, name);
        if (fromConfig) {
          setVal(name, fromConfig);
          continue;
        }
        const fromReg = readRegistrationValueForAuthField(nestedRegistration, name);
        if (fromReg) setVal(name, fromReg);
      }
    };

    void (async () => {
      let nested: Record<string, unknown> = {};
      try {
        const full = (await ConnectorsApi.getOAuthConfig(
          connectorType,
          linkedOAuthAppId
        )) as Record<string, unknown>;
        if (!cancelled && full && typeof full === 'object') {
          nested = nestedConfigFromOAuthRegistrationDoc(full);
        }
      } catch {
        /* list fallback */
      }

      if (!cancelled && Object.keys(nested).length === 0) {
        try {
          const res = await ConnectorsApi.listOAuthConfigs(connectorType, 1, 200);
          const apps = (res.oauthConfigs ?? []) as { _id: string; config?: Record<string, unknown> }[];
          const app = apps.find((a) => a._id === linkedOAuthAppId);
          if (app?.config && typeof app.config === 'object') {
            nested = app.config;
          }
        } catch {
          /* ignore */
        }
      }

      if (cancelled) return;
      fillEmptyFromSources(nested);
      oauthCredentialHydratedKeyRef.current = key;
    })();

    return () => {
      cancelled = true;
    };
  }, [
    isCreateMode,
    selectedAuthType,
    linkedOAuthAppId,
    panelConnectorId,
    panelConnector?.type,
    oauthCredentialFieldNames,
  ]);

  if (!connectorSchema || !panelConnector) {
    return null;
  }

  const authConfig = connectorSchema.auth;
  const supportedAuthTypes = authConfig?.supportedAuthTypes ?? [];
  const showAuthTypeSelector = isCreateMode && supportedAuthTypes.length > 1;

  const authFieldsDisabled =
    isProfileInitialized && !isCreateMode && isAdmin === false;

  const redirectPath =
    (authConfig?.schemas?.[selectedAuthType] as { redirectUri?: string } | undefined)?.redirectUri ||
    (authConfig as { redirectUri?: string } | undefined)?.redirectUri;
  const displayRedirect =
    (authConfig?.schemas?.[selectedAuthType] as { displayRedirectUri?: boolean } | undefined)
      ?.displayRedirectUri ?? authConfig?.displayRedirectUri;
  const callbackUrl =
    redirectPath && typeof window !== 'undefined'
      ? `${window.location.origin.replace(/\/$/, '')}/${redirectPath.replace(/^\//, '')}`
      : null;

  const docLinks = connectorSchema.documentationLinks ?? [];

  const emailVisibilityDocUrl = useMemo(() => {
    if (!panelConnector || (panelConnector.type !== 'Confluence' && panelConnector.type !== 'Jira')) {
      return null;
    }
    const fromRegistry = registryConnectors.find((c) => c.type === panelConnector.type);
    const baseUrl = getConnectorDocumentationUrl(fromRegistry ?? panelConnector, docLinks);
    if (!baseUrl) return null;
    return `${baseUrl}#prerequisite-email-visibility`;
  }, [panelConnector, registryConnectors, docLinks]);

  const showOAuthConnectionCard =
    isOAuthType(selectedAuthType) &&
    (selectedAuthType === 'OAUTH' || (Boolean(callbackUrl) && displayRedirect !== false));

  const mergeOAuthCredentialSurface = showOAuthConnectionCard && visibleAuthFields.length > 0;

  const configureCardShell = {
    padding: 16,
    backgroundColor: 'var(--olive-2)',
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--olive-3)',
    width: '100%' as const,
    boxSizing: 'border-box' as const,
  };

  const oauthConnectionCardInner = (
    <>
      {callbackUrl && displayRedirect !== false && (
        <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Redirect URL
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              {t('workspace.connectors.authTab.redirectUrlDescription')}
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
              <code>{callbackUrl}</code>
            </Box>
            <Tooltip content="Copy redirect URL">
              <IconButton
                type="button"
                size="1"
                variant="ghost"
                color="gray"
                radius="full"
                style={{ flexShrink: 0, cursor: 'pointer' }}
                aria-label="Copy redirect URL"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(callbackUrl);
                    addToast({
                      variant: 'success',
                      title: 'Redirect URL copied',
                      description: "Add it to your identity provider's allowed redirect list.",
                      duration: 2500,
                    });
                  } catch {
                    addToast({
                      variant: 'error',
                      title: 'Could not copy',
                      description: 'Copy the URL manually or allow clipboard access for this site.',
                      duration: 4000,
                    });
                  }
                }}
              >
                <MaterialIcon name="content_copy" size={18} color="var(--gray-11)" />
              </IconButton>
            </Tooltip>
          </Flex>
        </Flex>
      )}

      {callbackUrl && displayRedirect !== false && selectedAuthType === 'OAUTH' ? (
        <Separator size="4" style={{ width: '100%', maxWidth: '100%' }} />
      ) : null}

      {selectedAuthType === 'OAUTH' && (
        <>
          <OAuthAppSelector />
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
            {isCreateMode
              ? isAdmin === true
                ? t('workspace.connectors.authTab.oauthCreateHelperAdmin')
                : t('workspace.connectors.authTab.oauthCreateHelperNonAdmin')
              : isAdmin === true
                ? t('workspace.connectors.authTab.oauthEditHelperAdmin')
                : t('workspace.connectors.authTab.oauthEditHelperNonAdmin')}
          </Text>
        </>
      )}

      {selectedAuthType !== 'OAUTH' && callbackUrl && displayRedirect !== false && (
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
          {t('workspace.connectors.authTab.redirectUrlHelperNonOauth')}
        </Text>
      )}
    </>
  );

  const credentialsSubtext = (() => {
    if (isOAuthType(selectedAuthType) && isProfileInitialized) {
      if (isAdmin === true) {
        return t('workspace.connectors.authTab.oauthCredentialsSubtextAdmin', {
          name: panelConnector.name,
        });
      }
      if (isAdmin === false || isAdmin === null) {
        return t('workspace.connectors.authTab.oauthCredentialsSubtextNonAdmin');
      }
    }
    return t('workspace.connectors.authTab.credentialsSubtext', { name: panelConnector.name });
  })();

  const authCredentialBlockInner =
    visibleAuthFields.length === 0 ? null : (
      <>
        <Flex direction="column" gap="1">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.connectors.authTab.credentialsHeading', {
              name: formatAuthTypeName(selectedAuthType),
            })}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
            {credentialsSubtext}
          </Text>
        </Flex>

        <Flex direction="column" gap="5">
          {visibleAuthFields.map((field) => (
            <SchemaFormField
              key={field.name}
              field={field}
              value={formData.auth[field.name]}
              onChange={setAuthFormValue}
              error={formErrors[field.name]}
              disabled={authFieldsDisabled}
            />
          ))}
        </Flex>
      </>
    );

  return (
    <Flex direction="column" gap="6" style={{ padding: 'var(--space-1) 0' }}>
      {connectorInfoText ? (
        <Box
          style={{
            padding: 'var(--space-4)',
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--accent-a6)',
            backgroundColor: 'var(--accent-a2)',
          }}
        >
          <Flex align="start" gap="3">
            <MaterialIcon name="info" size={20} color="var(--accent-11)" style={{ flexShrink: 0 }} />
            <Text
              size="2"
              style={{
                color: 'var(--gray-12)',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
              }}
            >
              {connectorInfoText}
              {emailVisibilityDocUrl && (
                <>
                  {'\n\n'}
                  <a
                    href={emailVisibilityDocUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: 'var(--accent-11)',
                      textDecoration: 'underline',
                      cursor: 'pointer',
                    }}
                  >
                    {t('workspace.connectors.authTab.emailVisibilityDocLink')}
                  </a>
                </>
              )}
            </Text>
          </Flex>
        </Box>
      ) : null}

      {docLinks.length > 0 && (
        <DocumentationSection links={docLinks} connectorType={panelConnector.type} />
      )}

      {showAuthTypeSelector && (
        <Flex direction="column" gap="4" style={configureCardShell}>
          <Flex direction="column" gap="1">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Authentication method
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              Choose how this connector instance will authenticate to {panelConnector.name}.
            </Text>
          </Flex>
          <Select.Root
            value={supportedAuthTypes.includes(selectedAuthType) ? selectedAuthType : undefined}
            onValueChange={setSelectedAuthType}
          >
            <Select.Trigger
              style={{ width: '100%', height: 32 }}
              placeholder={t('workspace.connectors.authTab.methodPlaceholder')}
            />
            <Select.Content
              position="popper"
              style={{ zIndex: 10000 }}
              container={panelBodyPortal ?? undefined}
            >
              {supportedAuthTypes.map((type) => (
                <Select.Item key={type} value={type}>
                  {formatAuthTypeName(type)}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        </Flex>
      )}

      {mergeOAuthCredentialSurface ? (
        <Flex direction="column" gap="4" style={configureCardShell}>
          {oauthConnectionCardInner}
          <Separator size="4" style={{ width: '100%', maxWidth: '100%' }} />
          {authCredentialBlockInner}
        </Flex>
      ) : (
        <>
          {showOAuthConnectionCard && (
            <Flex direction="column" gap="4" style={configureCardShell}>
              {oauthConnectionCardInner}
            </Flex>
          )}

          {visibleAuthFields.length > 0 && (
            <Flex
              direction="column"
              gap={isOAuthType(selectedAuthType) ? '4' : '5'}
              style={isOAuthType(selectedAuthType) ? configureCardShell : undefined}
            >
              {authCredentialBlockInner}
            </Flex>
          )}
        </>
      )}

      {isNoneAuthType(selectedAuthType) && (
        <Flex
          align="center"
          gap="2"
          style={{
            backgroundColor: 'var(--green-a3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-3) var(--space-4)',
          }}
        >
          <MaterialIcon name="check_circle" size={16} color="var(--green-a11)" />
          <Text size="2" style={{ color: 'var(--green-a11)' }}>
            {t('workspace.connectors.authTab.noAuthRequired')}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
