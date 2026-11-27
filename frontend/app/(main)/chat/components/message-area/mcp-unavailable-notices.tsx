'use client';

import React, { useRef, useState } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import {
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
} from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import { browserOAuthBaseUrl } from '@/lib/api/frontend-public-url';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';
import type { MessagePart } from '../../types';
import { useChatStore } from '../../store';
import { useRenderSlotId } from './render-slot';
import { signedInAt } from './mcp-sign-in-card';

/** Why a server was left out (MCPNOTE-1); `unknown`: an older Node named the server without a reason. */
export type McpUnusedReason = 'sign_in' | 'not_approved' | 'unreachable' | 'no_tools' | 'flagged' | 'unknown';

export interface McpUnusedServer {
  name: string;
  /** `sign_in`: the instance to sign in to from here, when the person can redo it; `not_approved`: the server to approve. */
  instance?: string;
}

export interface McpUnusedGroup {
  reason: McpUnusedReason;
  servers: McpUnusedServer[];
}

const REASONS: ReadonlySet<string> = new Set(['sign_in', 'not_approved', 'unreachable', 'no_tools', 'flagged']);

const TEXT_KEYS: Record<McpUnusedReason, string> = {
  sign_in: 'agentActivity.notice.mcpUnused.signIn',
  not_approved: 'agentActivity.notice.mcpUnused.notApproved',
  unreachable: 'agentActivity.notice.mcpUnused.unreachable',
  no_tools: 'agentActivity.notice.mcpUnused.noTools',
  flagged: 'agentActivity.notice.mcpUnused.flagged',
  unknown: 'agentActivity.notice.mcpUnused.unknown',
};

/** Where the person fixes a server that isn't signed in or didn't answer, and where an admin approves its tools. */
const MY_SERVERS = '/workspace/mcp-servers/personal';
const APPROVE_TOOLS = '/workspace/mcp-servers/team';

/** One server waiting for approval opens on its own page at its admin section; several open the list. */
export function approveToolsHref(servers: McpUnusedServer[]): string {
  const instance = servers.length === 1 ? servers[0]?.instance : undefined;
  return instance ? `${APPROVE_TOOLS}?serverId=${encodeURIComponent(instance)}&tab=hub` : APPROVE_TOOLS;
}

/** Each server the run went without, once, grouped by why, in the order the run named them. */
export function mcpUnusedGroups(parts: MessagePart[] | undefined): McpUnusedGroup[] {
  const groups = new Map<McpUnusedReason, McpUnusedGroup>();
  const seen = new Set<string>();
  for (const part of parts ?? []) {
    if (part.type !== 'notice' || part.code !== 'mcp_unavailable' || !part.item || seen.has(part.item)) continue;
    seen.add(part.item);
    // The notice's `reason` is a string; the part type names the approval's object for that field.
    const said: unknown = part.reason;
    const reason = (typeof said === 'string' && REASONS.has(said) ? said : 'unknown') as McpUnusedReason;
    const group = groups.get(reason) ?? { reason, servers: [] };
    group.servers.push({
      name: part.item,
      ...((reason === 'sign_in' || reason === 'not_approved') && part.instance ? { instance: part.instance } : {}),
    });
    groups.set(reason, group);
  }
  return [...groups.values()];
}

/** D-MCP-5: each server whose tools the description scanner held back, with those tools, once. */
export function mcpHeldTools(parts: MessagePart[] | undefined): Array<{ server: string; tools: string[] }> {
  const held = new Map<string, Set<string>>();
  for (const part of parts ?? []) {
    if (part.type !== 'notice' || part.code !== 'mcp_tool_held' || !part.item || !Array.isArray(part.tools)) continue;
    const tools = held.get(part.item) ?? new Set<string>();
    for (const tool of part.tools) if (typeof tool === 'string' && tool) tools.add(tool);
    held.set(part.item, tools);
  }
  return [...held].filter(([, tools]) => tools.size > 0).map(([server, tools]) => ({ server, tools: [...tools] }));
}

/** "Atlassian Rovo wasn't used for this answer: sign in to it first." in the reader's language, one or several. */
export function mcpUnusedText(t: TFunction, language: string, group: McpUnusedGroup): string {
  const names = new Intl.ListFormat(language || 'en', { style: 'long', type: 'conjunction' }).format(
    group.servers.map((server) => server.name),
  );
  return t(TEXT_KEYS[group.reason], { count: group.servers.length, names });
}

/**
 * The MCP servers a run went without, said plainly, each group with what fixes it: sign in right here (the MCP
 * OAuth popup) when only the person's own sign-in is missing, else the page where they or an admin fix it.
 */
export function McpUnavailableNotices({ parts }: { parts: MessagePart[] | undefined }) {
  const { t, i18n } = useTranslation();
  const groups = mcpUnusedGroups(parts);
  const held = mcpHeldTools(parts);
  if (groups.length === 0 && held.length === 0) return null;
  const list = (names: string[]) =>
    new Intl.ListFormat(i18n.language || 'en', { style: 'long', type: 'conjunction' }).format(names);
  return (
    <>
      {groups.map((group) => (
        <McpUnusedNotice key={group.reason} group={group} />
      ))}
      {held.map(({ server, tools }) => (
        <Text key={`held:${server}`} as="p" size="1" role="note" data-testid="run-notice" style={{ color: 'var(--amber-11)', margin: 0 }}>
          {t('agentActivity.notice.mcpUnused.held', { count: tools.length, server, tools: list(tools) })}
        </Text>
      ))}
    </>
  );
}

function McpUnusedNotice({ group }: { group: McpUnusedGroup }) {
  const { t, i18n } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const touch = useIsMobile() ? { minHeight: 44 } : undefined;
  const signable = group.reason === 'sign_in' ? group.servers.filter((server) => server.instance !== undefined) : [];
  const unsignable = group.reason === 'sign_in' && signable.length < group.servers.length;
  const link =
    group.reason === 'not_approved'
      ? isAdmin
        ? { href: approveToolsHref(group.servers), label: t('agentActivity.notice.mcpUnused.approveTools', { count: group.servers.length }) }
        : null
      : group.reason !== 'sign_in' || unsignable
        ? { href: MY_SERVERS, label: t('agentActivity.notice.mcpUnused.openServers') }
        : null;
  return (
    <Text as="div" size="1" role="note" data-testid="run-notice" style={{ color: 'var(--amber-11)', margin: 0 }}>
      {mcpUnusedText(t, i18n.language, group)}
      {link && (
        <>
          {' '}
          <NextLink
            href={link.href}
            style={{ color: 'var(--accent-11)', textDecoration: 'underline', ...(touch ? { display: 'inline-flex', alignItems: 'center', ...touch } : {}) }}
          >
            {link.label}
          </NextLink>
        </>
      )}
      {signable.length > 0 && <McpNoticeSignIn servers={signable} touch={touch} />}
    </Text>
  );
}

/** Sign in to each server again from the answer, with the popup the MCP servers page uses. */
function McpNoticeSignIn({ servers, touch }: { servers: McpUnusedServer[]; touch: { minHeight: number } | undefined }) {
  const { t } = useTranslation();
  // Someone reading a conversation shared with them can't sign in for its owner.
  const renderSlotId = useRenderSlotId();
  const isOwner = useChatStore((s) => (renderSlotId ? s.slots[renderSlotId]?.isOwner ?? null : null)) !== false;
  const [signedIn, setSignedIn] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ instance: string; message: string } | null>(null);
  // The server being signed in to, and when its sign-in before this one was made: only a newer one is success.
  const target = useRef<{ instance: string; before: number | null } | null>(null);

  const { startOAuthPopup } = useMcpOAuthPopup({
    getAuthorizationUrl: async (instanceId) => {
      if (target.current) target.current.before = (await signedInAt({ instanceId, serverName: '', scopes: [] })) ?? 0;
      return McpServersApi.getOAuthAuthorizationUrl(instanceId, browserOAuthBaseUrl());
    },
    verifyAuthenticated: async () => {
      const current = target.current;
      if (!current || current.before === null) return false;
      const now = await signedInAt({ instanceId: current.instance, serverName: '', scopes: [] });
      return now !== null && now > current.before;
    },
    onVerified: () => {
      setBusy(null);
      const instance = target.current?.instance;
      if (instance) setSignedIn((prev) => new Set(prev).add(instance));
    },
    onFailed: (failure) => {
      setBusy(null);
      const instance = target.current?.instance;
      if (instance) setError({ instance, message: mcpOAuthFailureMessage(t, failure) });
    },
  });

  if (!isOwner) return null;

  const signIn = (instance: string) => {
    setError(null);
    setBusy(instance);
    target.current = { instance, before: null };
    // Called inside the click: the popup has to open before anything is awaited.
    void startOAuthPopup(instance);
  };

  return (
    <Flex direction="column" gap="1" mt="2">
      {servers.map((server) => {
        const instance = server.instance as string;
        return signedIn.has(instance) ? (
          <Flex key={instance} align="center" gap="1">
            <MaterialIcon name="check_circle" size={14} color="var(--green-11)" />
            <Text size="1" style={{ color: 'var(--green-11)' }}>
              {t('agentActivity.notice.mcpUnused.signedIn', { name: server.name })}
            </Text>
          </Flex>
        ) : (
          <Flex key={instance} direction="column" gap="1" align="start">
            <Button
              size="1"
              variant="soft"
              onClick={() => signIn(instance)}
              disabled={busy !== null}
              style={touch}
            >
              {busy === instance
                ? t('agentActivity.notice.mcpUnused.signingIn', { name: server.name })
                : t('agentActivity.notice.mcpUnused.signInTo', { name: server.name })}
            </Button>
            {error?.instance === instance && (
              <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
                {error.message}
              </Text>
            )}
          </Flex>
        );
      })}
    </Flex>
  );
}
