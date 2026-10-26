'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { isNoneAuthType, isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import { apiErrorDetail } from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import { useToolsetOauthPopupFlow } from '@/app/(main)/agents/agent-builder/hooks/use-toolset-oauth-popup-flow';
import { ConnectRow } from './connect-row';

/**
 * The person's actions that a sign-in keeps out of the chat. Ones with nothing to sign in to, and
 * ones already listed with their tools, are left out.
 */
export function actionsToConnect(
  toolsets: BuilderSidebarToolset[],
  listed: ReadonlySet<string>
): BuilderSidebarToolset[] {
  return toolsets.filter(
    (ts) =>
      Boolean(ts.instanceId) &&
      !ts.isAuthenticated &&
      !isNoneAuthType(ts.authType ?? '') &&
      !listed.has(ts.instanceId as string)
  );
}

/** The name the action's tool group gets once it is connected (see `buildCatalogToolGroups`). */
export function actionName(ts: BuilderSidebarToolset): string {
  return ts.instanceName?.trim() || ts.displayName || ts.name;
}

interface ToolsetConnectRowsProps {
  toolsets: BuilderSidebarToolset[];
  rowStyle: React.CSSProperties;
  renderIcon: (toolset: BuilderSidebarToolset) => React.ReactNode;
  /** After a sign-in succeeds: reload the list and turn the action's tools on. */
  onConnected: (instanceId: string) => void;
}

/** "Not connected" rows under the chat's actions, each signing in where it is. */
export function ToolsetConnectRows({ toolsets, rowStyle, renderIcon, onConnected }: ToolsetConnectRowsProps) {
  const { t } = useTranslation();
  const [busyId, setBusyId] = useState<string | null>(null);
  // The popup's checks run after later renders; they need the action being signed in to now.
  const busyRef = useRef<string | null>(null);
  const [dialogFor, setDialogFor] = useState<BuilderSidebarToolset | null>(null);

  const setBusy = useCallback((id: string | null) => {
    busyRef.current = id;
    setBusyId(id);
  }, []);

  const connectedToast = useCallback(
    (id: string) => {
      const ts = toolsets.find((x) => x.instanceId === id);
      toast.success(t('chat.agentResources.actionConnected', { name: ts ? actionName(ts) : '' }));
    },
    [toolsets, t]
  );

  const verifyAuthenticated = useCallback(async () => {
    const id = busyRef.current;
    return Boolean(id && (await ToolsetsApi.findMyToolsetByInstanceId(id))?.isAuthenticated);
  }, []);
  const onVerified = useCallback(() => {
    const id = busyRef.current;
    setBusy(null);
    if (!id) return;
    connectedToast(id);
    onConnected(id);
  }, [connectedToast, onConnected, setBusy]);
  const onIncomplete = useCallback(() => toast.error(t('agentBuilder.oauthSignInIncomplete')), [t]);
  const onOAuthPopupError = useCallback((message: string) => toast.error(message), []);

  const { authenticating, beginOAuth, stopOAuthUi } = useToolsetOauthPopupFlow({
    t,
    verifyAuthenticated,
    onVerified,
    onIncomplete,
    onOAuthPopupError,
  });
  // Leaving the tab mid sign-in closes the popup: nothing here would hear it finish.
  useEffect(() => stopOAuthUi, [stopOAuthUi]);
  // Every way a sign-in ends clears `authenticating`, including a popup closed before it loaded.
  const activeId = authenticating ? busyId : null;

  const connect = (ts: BuilderSidebarToolset) => {
    const id = ts.instanceId as string;
    if (!isOAuthType(ts.authType ?? '')) {
      setDialogFor(ts);
      return;
    }
    setBusy(id);
    // Straight from the click: a popup opened after an await is blocked.
    void beginOAuth(
      'oauth_user_toolset',
      async () => {
        const result = await ToolsetsApi.getInstanceOAuthAuthorizationUrl(id, window.location.origin);
        if (!result.success || !result.authorizationUrl) throw new Error(t('agentBuilder.oauthUrlFailed'));
        return result.authorizationUrl;
      },
      {
        onTimeout: () => toast.error(t('agentBuilder.authTimeout')),
        onOpenError: (e) => toast.error(apiErrorDetail(e)),
      }
    );
  };

  if (toolsets.length === 0) return null;

  return (
    <Flex direction="column" gap="1" data-testid="toolset-connect-rows">
      <Text size="1" style={{ color: 'var(--gray-11)', marginTop: 'var(--space-1)' }}>
        {t('chat.agentResources.notConnected')}
      </Text>
      {toolsets.map((ts) => (
        <ConnectRow
          key={ts.instanceId}
          testId="toolset-connect-row"
          rowStyle={rowStyle}
          icon={renderIcon(ts)}
          name={actionName(ts)}
          action={
            <Button
              type="button"
              size="1"
              variant="soft"
              loading={activeId === ts.instanceId}
              disabled={activeId !== null && activeId !== ts.instanceId}
              onClick={() => connect(ts)}
              style={{ flexShrink: 0 }}
            >
              {t('chat.agentResources.connect')}
            </Button>
          }
        />
      ))}
      {dialogFor ? (
        <UserToolsetConfigDialog
          toolset={dialogFor}
          instanceId={dialogFor.instanceId as string}
          onClose={() => setDialogFor(null)}
          onSuccess={() => {
            const id = dialogFor.instanceId as string;
            connectedToast(id);
            onConnected(id);
          }}
        />
      ) : null}
    </Flex>
  );
}
