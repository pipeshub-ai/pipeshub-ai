'use client';

import React, { useContext, useEffect, useMemo, useRef } from 'react';
import { Flex, Text, Select, Box, Separator, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '../schema-form-field';
import { useConnectorsStore } from '../../store';
import { isNoneAuthType, isOAuthType } from '../../utils/auth-helpers';
import { DocumentationSection } from './documentation-section';
import { OAuthAppSelector, OAuthAppInUseReadonly } from './oauth-app-selector';
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
import { useTranslation } from 'react-i18next';

// ========================================
// Component
// ========================================

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
    selectedAuthType,
    isAuthTypeImmutable: _isAuthTypeImmutable,
    formData,
    formErrors,
    conditionalDisplay,
    setAuthFormValue,
    setSelectedAuthType,
  } = useConnectorsStore();

  const { t } = useTranslation();

  if (!connectorSchema || !panelConnector) return null;

  const isCreateMode = !panelConnectorId;
  const authConfig = connectorSchema?.auth;
  const supportedAuthTypes = authConfig?.supportedAuthTypes ?? [];
  const showAuthTypeSelector = isCreateMode && supportedAuthTypes.length > 1;

  // Resolve current auth schema fields based on selected auth type
  const currentSchemaFields = resolveAuthFields(authConfig, selectedAuthType);
  const linkedOAuthAppId = useMemo(() => {
    const fromForm = (formData.auth.oauthConfigId as string | undefined)?.trim();
    if (fromForm) return fromForm;
    const auth = connectorConfig?.config?.auth as { oauthConfigId?: string } | undefined;
    return typeof auth?.oauthConfigId === 'string' ? auth.oauthConfigId.trim() : '';
  }, [formData.auth.oauthConfigId, connectorConfig?.config?.auth]);

  const authFieldsForForm = useMemo(() => {
    if (selectedAuthType !== 'OAUTH') return currentSchemaFields;
    // `oauthConfigId` is chosen via OAuth app picker / saved binding — not a free-form schema field here.
    return currentSchemaFields.filter((f) => f.name !== 'oauthConfigId');
  }, [currentSchemaFields, selectedAuthType]);

  const oauthCredentialFieldNames = useMemo(() => {
    if (selectedAuthType !== 'OAUTH' || !authConfig) return [];
    return resolveAuthFields(authConfig, 'OAUTH')
      .map((f) => f.name)
      .filter((n) => n !== 'oauthConfigId');
  }, [authConfig, selectedAuthType]);

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

  const docLinks = connectorSchema?.documentationLinks ?? [];

  const showOAuthConnectionCard =
    isOAuthType(selectedAuthType) &&
    (selectedAuthType === 'OAUTH' || (Boolean(callbackUrl) && displayRedirect !== false));

  /** One olive card for redirect + app binding + schema credentials (avoids a large gap between shells). */
  const mergeOAuthCredentialSurface = showOAuthConnectionCard && authFieldsForForm.length > 0;

  const configureCardShell = {
    padding: 16,
    backgroundColor: 'var(--olive-2)',
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--olive-3)',
    width: '100%' as const,
    boxSizing: 'border-box' as const,
  };

  if (!connectorSchema || !panelConnector) {
    return null;
  }

  const oauthConnectionCardInner = (
    <>
      {callbackUrl && displayRedirect !== false && (
        <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Redirect URL
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              Add this exact URL to the allowed redirect list on your identity provider (same path as the legacy app).
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
          {isCreateMode ? <OAuthAppSelector /> : <OAuthAppInUseReadonly />}
          {isCreateMode ? (
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              If saved OAuth apps exist for this connector, pick one above; otherwise enter client credentials below.
              Register this redirect URL in your IdP first.
            </Text>
          ) : (
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              Re-run consent from the Authorize tab if your identity provider requires it.
            </Text>
          )}
        </>
      )}

      {selectedAuthType !== 'OAUTH' && callbackUrl && displayRedirect !== false && (
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
          Register the redirect URL in your identity provider, then complete the fields below.
        </Text>
      )}
    </>
  );

  const authCredentialBlockInner = (
    <>
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {formatAuthTypeName(selectedAuthType)} credentials
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
          Enter your {panelConnector.name} authentication details
        </Text>
      </Flex>

      <Flex direction="column" gap="5">
        {authFieldsForForm.map((field) => {
          const isVisible =
            conditionalDisplay[field.name] !== undefined ? conditionalDisplay[field.name] : true;

          return (
            <SchemaFormField
              key={field.name}
              field={field}
              value={formData.auth[field.name]}
              onChange={setAuthFormValue}
              visible={isVisible}
              error={formErrors[field.name]}
              disabled={authFieldsDisabled}
            />
          );
        })}
      </Flex>
    </>
  );

  return (
    <Flex direction="column" gap="6" style={{ padding: 'var(--space-1) 0' }}>
      {/* ── A. Setup Documentation ── */}
      {docLinks.length > 0 && (
        <DocumentationSection
          links={docLinks}
          connectorIconPath={panelConnector.iconPath}
        />
      )}

      {/* ── Auth Type Selector (create mode only, multiple auth types) ── */}
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

      {/* OAuth redirect + app + credentials: one card when merged (no double gap / double border). */}
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

          {authFieldsForForm.length > 0 && (
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

      {/* OAuth consent runs on the Authorize tab after the instance id exists. */}

      {/* ── For NONE auth type, show info message ── */}
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
