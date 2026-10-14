'use client';

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { McpAuthDialog, isMcpInstanceDisabled } from '@/app/(main)/workspace/mcp-servers/components';
import {
  mcpConnectionState,
  mcpStatusLook,
  type McpConnectionState,
} from '@/app/(main)/workspace/mcp-servers/connection-state';
import {
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
} from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import type { McpMyServerEntry, McpServerTemplate } from '@/app/(main)/workspace/mcp-servers/types';

type SignInState = Extract<
  McpConnectionState,
  'needs_connect' | 'needs_reconnect' | 'waiting_for_admin' | 'shared_credential_missing'
>;

// The ones this person can fix here come first.
const SIGN_IN_ORDER: Record<SignInState, number> = {
  needs_reconnect: 0,
  needs_connect: 1,
  shared_credential_missing: 2,
  waiting_for_admin: 3,
};
// Radix's size-1 checkbox: the connected rows' names line up with these.
const CHECKBOX_SPACE = 'calc(var(--space-4) * 0.875)';

export interface McpServerToConnect {
  entry: McpMyServerEntry;
  state: SignInState;
}

/**
 * The person's servers that a sign-in keeps out of the chat: never connected, expired, or waiting
 * for an administrator's shared key, those they can connect first. Servers already listed with
 * their tools, and disabled ones, are left out.
 */
export function mcpServersToConnect(
  entries: McpMyServerEntry[],
  { isAdmin, listed }: { isAdmin: boolean; listed: ReadonlySet<string> }
): McpServerToConnect[] {
  const servers: McpServerToConnect[] = [];
  for (const entry of entries) {
    if (listed.has(entry._id) || isMcpInstanceDisabled(entry)) continue;
    const state = mcpConnectionState(entry, { isAdmin });
    if (state in SIGN_IN_ORDER) servers.push({ entry, state: state as SignInState });
  }
  return servers.sort((a, b) => SIGN_IN_ORDER[a.state] - SIGN_IN_ORDER[b.state]);
}

interface McpConnectRowsProps {
  servers: McpServerToConnect[];
  rowStyle: React.CSSProperties;
  /** After a sign-in succeeds: reload the list and turn the server's tools on. */
  onConnected: (instanceId: string) => void;
}

/** "Not connected" rows under the chat's MCP servers, each signing in where it is. */
export function McpConnectRows({ servers, rowStyle, onConnected }: McpConnectRowsProps) {
  const { t } = useTranslation();
  const [busyId, setBusyId] = useState<string | null>(null);
  // The popup's checks run after later renders; they need the server being signed in to now.
  const busyRef = useRef<string | null>(null);
  const [dialog, setDialog] = useState<{ instance: McpMyServerEntry; template: McpServerTemplate | null } | null>(
    null
  );

  const setBusy = (id: string | null) => {
    busyRef.current = id;
    setBusyId(id);
  };

  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: async () => {
      const res = await McpServersApi.getMyMcpServers(false);
      return Boolean(res.instances.find((i) => i._id === busyRef.current)?.isAuthenticated);
    },
    onVerified: () => {
      const id = busyRef.current;
      setBusy(null);
      toast.success(t('workspace.mcpServers.toasts.authenticated'));
      if (id) onConnected(id);
    },
    onFailed: (failure) => {
      setBusy(null);
      toast.error(mcpOAuthFailureMessage(t, failure));
    },
  });

  const connect = (entry: McpMyServerEntry) => {
    if (entry.authMode === 'oauth') {
      setBusy(entry._id);
      // Straight from the click: a popup opened after an await is blocked.
      void startOAuthPopup(entry._id);
      return;
    }
    setDialog({ instance: entry, template: null });
    if (entry.typeId) {
      // Only the key's label and documentation link come from the catalog; the dialog works without.
      McpServersApi.getCatalogTemplate(entry.typeId)
        .then((template) => setDialog((open) => (open?.instance._id === entry._id ? { ...open, template } : open)))
        .catch(() => undefined);
    }
  };

  if (servers.length === 0) return null;

  return (
    <Flex direction="column" gap="1" data-testid="mcp-connect-rows">
      <Text size="1" style={{ color: 'var(--gray-11)', marginTop: 'var(--space-1)' }}>
        {t('workspace.mcpServers.status.notConnected')}
      </Text>
      {servers.map(({ entry, state }) => {
        const canConnect = state === 'needs_connect' || state === 'needs_reconnect';
        return (
          <Flex
            key={entry._id}
            align="center"
            justify="between"
            gap="2"
            data-testid="mcp-connect-row"
            style={{ ...rowStyle, padding: 'var(--space-2)' }}
          >
            <Flex align="center" gap="2" style={{ minWidth: 0 }}>
              <span aria-hidden style={{ width: CHECKBOX_SPACE, flexShrink: 0 }} />
              <MaterialIcon name="hub" size={18} color="var(--gray-9)" />
              <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
                {entry.name}
              </Text>
            </Flex>
            {canConnect ? (
              <Button
                type="button"
                size="1"
                variant="soft"
                loading={busyId === entry._id}
                disabled={busyId !== null && busyId !== entry._id}
                onClick={() => connect(entry)}
                style={{ flexShrink: 0 }}
              >
                {t(state === 'needs_reconnect' ? 'workspace.mcpServers.cta.reconnect' : 'workspace.mcpServers.cta.connect')}
              </Button>
            ) : (
              <Badge size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
                {t(mcpStatusLook(state).key)}
              </Badge>
            )}
          </Flex>
        );
      })}
      <McpAuthDialog
        instance={dialog?.instance ?? null}
        template={dialog?.template ?? null}
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        onAuthenticated={() => {
          if (dialog) onConnected(dialog.instance._id);
        }}
      />
    </Flex>
  );
}
