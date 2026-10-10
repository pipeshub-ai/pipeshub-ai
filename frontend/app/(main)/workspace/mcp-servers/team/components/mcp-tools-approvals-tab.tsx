'use client';

import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { McpDisabledCallout } from '../../components';
import { McpToolRulesList, type McpToolRulesEditor } from '../../components/mcp-tool-rules-editor';
import { usesSharedCredential, type McpConnectionState } from '../../connection-state';
import type { McpMyServerEntry, McpToolInfo } from '../../types';
import { MCP_TRANSPORT_LABELS } from '../../types';

/** The tool list the tab shows: read from the cache when the tab opens, live on Refresh. */
export type McpToolsLoad =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; tools: McpToolInfo[]; syncedAt?: number }
  | { status: 'error'; message: string; code: 'reauth' | 'unreachable' | 'error' };

interface McpToolsApprovalsTabProps {
  instance: McpMyServerEntry;
  state: McpConnectionState;
  load: McpToolsLoad;
  editor: McpToolRulesEditor;
  /** Rules shown but not changeable (an inherited server). */
  readOnly: boolean;
  isBusy: boolean;
  onRefresh: () => void;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
}

/** Who is signed in and how, then the server's tools with their approval rules. */
export function McpToolsApprovalsTab({
  instance,
  state,
  load,
  editor,
  readOnly,
  isBusy,
  onRefresh,
  onAuthenticate,
  onReauthenticate,
}: McpToolsApprovalsTabProps) {
  const { t } = useTranslation();
  const canListTools = state !== 'needs_connect' && state !== 'waiting_for_admin' && state !== 'shared_credential_missing';
  const listing = canListTools && (load.status === 'idle' || load.status === 'loading');
  // Without the list (no sign-in, or it failed) rules are still set by name, e.g. a company Deny.
  const showRules = !listing && !editor.loading && !editor.loadError;
  const showFooter = !readOnly && showRules;

  return (
    <Flex direction="column" style={{ height: '100%', minHeight: 0 }}>
      <Flex direction="column" gap="4" style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingBottom: 'var(--space-4)' }}>
        <McpDisabledCallout instance={instance} />
        <SignInCard
          instance={instance}
          state={state}
          isBusy={isBusy}
          onAuthenticate={onAuthenticate}
          onReauthenticate={onReauthenticate}
        />

        <Flex direction="column" gap="3">
          <Flex align="center" justify="between" gap="2">
            <Flex align="baseline" gap="2">
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.mcpServers.details.tools')}
              </Text>
              {load.status === 'ready' && load.syncedAt && (
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {t('workspace.mcpServers.configPanel.synced', { time: formatRelativeTime(load.syncedAt) })}
                </Text>
              )}
            </Flex>
            {canListTools && (
              <Tooltip content={t('workspace.mcpServers.configPanel.refreshTools')}>
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  aria-label={t('workspace.mcpServers.configPanel.refreshTools')}
                  disabled={load.status === 'loading' || editor.saving}
                  onClick={onRefresh}
                >
                  <MaterialIcon name="refresh" size={16} color="var(--gray-11)" />
                </IconButton>
              </Tooltip>
            )}
          </Flex>

          {!canListTools ? (
            <Text size="2" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.mcpServers.configPanel.signInForTools')}
            </Text>
          ) : listing ? (
            <FindingTools />
          ) : load.status === 'error' ? (
            <Flex
              direction="column"
              gap="2"
              role="alert"
              style={{
                padding: 'var(--space-3)',
                border: '1px solid var(--red-a6)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--red-a2)',
              }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--red-11)' }}>
                {t('workspace.mcpServers.configPanel.toolsError')}
              </Text>
              <Text size="1" style={{ color: 'var(--gray-11)' }}>
                {load.code === 'reauth' ? t('workspace.mcpServers.configPanel.toolsErrorSignIn') : load.message}{' '}
                {t('workspace.mcpServers.configPanel.rulesKept')}
              </Text>
              <Box>
                <Button size="1" variant="soft" color="gray" onClick={onRefresh}>
                  <MaterialIcon name="refresh" size={14} color="currentColor" />
                  {t('workspace.mcpServers.configPanel.tryAgain')}
                </Button>
              </Box>
            </Flex>
          ) : null}

          {showRules && (
            <Flex direction="column" gap="3">
              <Text size="1" style={{ color: 'var(--gray-11)' }}>
                {t(`workspace.mcpServers.toolRules.description.${editor.isCompany ? 'company' : 'personal'}`)}
              </Text>
              {editor.isCompany && (
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {t('workspace.mcpServers.toolRules.unattendedHint')}
                </Text>
              )}
              <McpToolRulesList editor={editor} readOnly={readOnly} toolsUnknown={load.status !== 'ready'} />
            </Flex>
          )}
        </Flex>
      </Flex>

      {showFooter && (
        <Flex
          align="center"
          justify="between"
          gap="3"
          data-testid="mcp-rules-footer"
          style={{
            borderTop: '1px solid var(--olive-3)',
            paddingTop: 'var(--space-3)',
            flexShrink: 0,
          }}
        >
          <Text size="1" style={{ color: editor.changes > 0 ? 'var(--amber-11)' : 'var(--gray-10)' }}>
            {editor.changes > 0
              ? t('workspace.mcpServers.configPanel.unsaved', { count: editor.changes })
              : t('workspace.mcpServers.configPanel.allSaved')}
          </Text>
          {editor.changes > 0 && (
            <Flex gap="2">
              <Button size="2" variant="soft" color="gray" disabled={editor.saving} onClick={editor.discard}>
                {t('workspace.mcpServers.configPanel.discard')}
              </Button>
              <LoadingButton
                size="2"
                loading={editor.saving}
                loadingLabel={t('common.saving')}
                onClick={() => void editor.save()}
              >
                {t('workspace.mcpServers.configPanel.saveRules')}
              </LoadingButton>
            </Flex>
          )}
        </Flex>
      )}
    </Flex>
  );
}

function FindingTools() {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="3" data-testid="mcp-finding-tools">
      <Flex align="center" gap="2">
        <Spinner size={14} />
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.mcpServers.configPanel.findingTools')}
        </Text>
      </Flex>
      <Text size="1" style={{ color: 'var(--gray-10)' }}>
        {t('workspace.mcpServers.configPanel.rulesUnlock')}
      </Text>
      {[0, 1, 2].map((row) => (
        <Box
          key={row}
          aria-hidden
          style={{ height: 52, borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-a3)', opacity: 1 - row * 0.25 }}
        />
      ))}
    </Flex>
  );
}

function SignInCard({
  instance,
  state,
  isBusy,
  onAuthenticate,
  onReauthenticate,
}: {
  instance: McpMyServerEntry;
  state: McpConnectionState;
  isBusy: boolean;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
}) {
  const { t } = useTranslation();
  const transport = MCP_TRANSPORT_LABELS[instance.transport];

  if (instance.authMode === 'none') {
    return (
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.mcpServers.configPanel.noSignIn', { transport })}
      </Text>
    );
  }

  if (state === 'needs_reconnect' || state === 'shared_credential_missing') {
    return (
      <Callout.Root color="amber" size="1">
        <Callout.Icon>
          <MaterialIcon name="warning" size={16} color="var(--amber-11)" />
        </Callout.Icon>
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium">
            {t(
              state === 'needs_reconnect'
                ? 'workspace.mcpServers.configPanel.signInExpired'
                : 'workspace.mcpServers.configPanel.sharedMissing'
            )}
          </Text>
          <Text size="1">{t('workspace.mcpServers.configPanel.signInExpiredBody')}</Text>
          <Box>
            <Button size="1" color="amber" disabled={isBusy} onClick={state === 'needs_reconnect' ? onReauthenticate : onAuthenticate}>
              {t(state === 'needs_reconnect' ? 'workspace.mcpServers.cta.reauthenticate' : 'workspace.mcpServers.cta.connect')}
            </Button>
          </Box>
        </Flex>
      </Callout.Root>
    );
  }

  const shared = usesSharedCredential(instance);
  const signedIn = instance.isAuthenticated;
  const when = instance.connectedAt ? formatRelativeTime(instance.connectedAt) : null;
  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      data-testid="mcp-sign-in-card"
      style={{
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-4)',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t(
            !signedIn
              ? 'workspace.mcpServers.configPanel.notSignedIn'
              : shared
                ? 'workspace.mcpServers.configPanel.sharedSignIn'
                : 'workspace.mcpServers.configPanel.signedIn'
          )}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {signedIn && when
            ? t('workspace.mcpServers.configPanel.signedInDetail', { transport, time: when })
            : signedIn
              ? transport
              : t('workspace.mcpServers.configPanel.notSignedInDetail')}
        </Text>
      </Flex>
      {state !== 'waiting_for_admin' && (
        <Button
          size="1"
          variant="soft"
          disabled={isBusy}
          onClick={signedIn ? onReauthenticate : onAuthenticate}
          style={{ flexShrink: 0 }}
        >
          {t(signedIn ? 'workspace.mcpServers.cta.reauthenticate' : 'workspace.mcpServers.cta.connect')}
        </Button>
      )}
    </Flex>
  );
}
