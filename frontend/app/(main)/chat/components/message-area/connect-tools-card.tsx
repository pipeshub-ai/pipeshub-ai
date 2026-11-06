'use client';

import React, { useCallback, useContext, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { McpAuthDialog } from '@/app/(main)/workspace/mcp-servers/components';
import {
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
} from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { connectableHere, type UnavailableTool } from '../../unavailable-tools';
import { AnswerSlotContext, answerSlotIdNow } from './answer-slot-scope';
import { ToolLogo } from './tool-logo';

async function findMyMcpEntry(instanceId: string): Promise<McpMyServerEntry | null> {
  const { instances } = await McpServersApi.getMyMcpServers(false);
  return instances.find((entry) => entry._id === instanceId) ?? null;
}

const keyOf = (item: UnavailableTool) => `${item.kind}:${item.instanceId}`;

/** Past this many rows the rest fold behind "Show N more": the card never scrolls inside itself. */
const FOLD_AFTER = 4;
const ROW_HEIGHT = 'var(--space-7)';
const LOGO_SIZE = 20;
const BUTTON_HEIGHT = 'calc(var(--space-5) + var(--space-1))';
/** Wide enough for the longest status on two lines; a narrow card gives the name the rest. */
const STATUS_COLUMN = 'min(calc(var(--space-9) * 3), 45%)';
const CLIP: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
const TWO_LINES: React.CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
  lineHeight: 'var(--line-height-1)',
};

export interface ConnectToolsCardProps {
  items: UnavailableTool[];
  agentId?: string;
  /** The agent runs with its own credentials: only an admin can connect them. */
  serviceAccount: boolean;
  /** The question asked again by "Try again". */
  question: string;
  /** The run stopped on these tools; otherwise the answer went on without them. */
  blocked: boolean;
  testId?: string;
}

/** Lists the tools a run could not use, with a Connect button for each one the person can connect here. */
export function ConnectToolsCard({ items, agentId, serviceAccount, question, blocked, testId }: ConnectToolsCardProps) {
  const { t } = useTranslation();
  const [connected, setConnected] = useState<ReadonlySet<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tokenEntry, setTokenEntry] = useState<McpMyServerEntry | null>(null);
  const [toolsetDialog, setToolsetDialog] = useState<{ toolset: BuilderSidebarToolset; item: UnavailableTool } | null>(
    null,
  );
  const [resent, setResent] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const mcpTargetRef = useRef<string | null>(null);
  const toolsetTargetRef = useRef<UnavailableTool | null>(null);
  const scopedSlotId = useContext(AnswerSlotContext);

  const markConnected = useCallback(
    (key: string) => setConnected((prev) => new Set(prev).add(key)),
    [],
  );
  const setError = useCallback(
    (key: string, message: string | null) =>
      setErrors((prev) => {
        const next = { ...prev };
        if (message) next[key] = message;
        else delete next[key];
        return next;
      }),
    [],
  );

  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: async () => {
      const instanceId = mcpTargetRef.current;
      return instanceId ? Boolean((await findMyMcpEntry(instanceId))?.isAuthenticated) : false;
    },
    onVerified: () => {
      setBusyKey(null);
      if (mcpTargetRef.current) markConnected(`mcp:${mcpTargetRef.current}`);
    },
    onFailed: (failure) => {
      setBusyKey(null);
      if (mcpTargetRef.current) setError(`mcp:${mcpTargetRef.current}`, mcpOAuthFailureMessage(t, failure));
    },
  });

  const toolsetFailed = useCallback(
    (message?: string) => {
      setBusyKey(null);
      const target = toolsetTargetRef.current;
      if (target) setError(keyOf(target), message || t('chat.connectTools.toolsetUnavailable'));
    },
    [setError, t],
  );

  const connectMcp = (item: UnavailableTool) => {
    if (item.authMode === 'oauth') {
      mcpTargetRef.current = item.instanceId;
      // Called inside the click: the popup has to open before anything is awaited.
      void startOAuthPopup(item.instanceId);
      return;
    }
    void findMyMcpEntry(item.instanceId)
      .then((entry) => {
        if (entry) setTokenEntry(entry);
        else setError(keyOf(item), t('chat.mcpConnect.unavailable'));
      })
      .catch(() => setError(keyOf(item), t('chat.mcpConnect.unavailable')))
      .finally(() => setBusyKey(null));
  };

  const connectToolset = async (item: UnavailableTool) => {
    toolsetTargetRef.current = item;
    let toolset: BuilderSidebarToolset | undefined;
    try {
      toolset = await ToolsetsApi.findMyToolsetByInstanceId(item.instanceId);
    } catch {
      toolset = undefined;
    }
    if (!toolset) {
      toolsetFailed();
      return;
    }
    // The sign-in dialog runs every auth type, OAuth included, with the same popup handling as the agent builder.
    setBusyKey(null);
    setToolsetDialog({ toolset, item });
  };

  const connect = (item: UnavailableTool) => {
    setError(keyOf(item), null);
    setBusyKey(keyOf(item));
    if (item.kind === 'mcp') connectMcp(item);
    else void connectToolset(item);
  };

  const tryAgain = () => {
    const slotId = answerSlotIdNow(scopedSlotId);
    if (!slotId) return;
    const request = buildStreamChatRequestForSlot(slotId, question);
    if (!request) return;
    setResent(true);
    void streamMessageForSlot(slotId, question, request);
  };

  const fixable = items.filter((item) => connectableHere(item, serviceAccount));
  const allFixableConnected = fixable.length > 0 && fixable.every((item) => connected.has(keyOf(item)));
  // A stopped run would stop again on anything still missing; an answer that went on is worth asking again.
  const ready = allFixableConnected && (!blocked || fixable.length === items.length);
  const allMcp = items.every((item) => item.kind === 'mcp');
  const title = serviceAccount
    ? t('chat.connectTools.serviceAccountTitle')
    : blocked
      ? allMcp
        ? t('chat.mcpConnect.title')
        : t('chat.connectTools.blockedTitle')
      : t('chat.connectTools.noticeTitle');
  const tokenEntryId = tokenEntry?._id;

  const statusText = (item: UnavailableTool): string => {
    if (item.problem === 'not_found') return t('chat.mcpConnect.removed');
    if (item.sharedCredential && !serviceAccount) return t('chat.mcpConnect.waitingForAdmin');
    return t('chat.connectTools.askAdmin');
  };

  const rowFor = (item: UnavailableTool, first: boolean) => {
    const key = keyOf(item);
    const error = errors[key];
    return (
      <Box
        key={key}
        data-testid="connect-tools-row"
        style={first ? undefined : { borderTop: '1px solid var(--border-subtle)' }}
      >
        <Flex data-row-line align="center" gap="3" style={{ height: ROW_HEIGHT }}>
          <Flex align="center" justify="center" style={{ width: LOGO_SIZE, flexShrink: 0 }}>
            <ToolLogo item={item} size={LOGO_SIZE} />
          </Flex>
          <Flex direction="column" justify="center" style={{ flex: 1, minWidth: 0 }}>
            <Text
              size="2"
              data-testid="connect-tools-name"
              title={item.displayName}
              style={{ ...CLIP, color: 'var(--text-primary)', lineHeight: 'var(--line-height-1)' }}
            >
              {item.displayName}
            </Text>
            <Text size="1" style={{ ...CLIP, color: 'var(--text-secondary)' }}>
              {item.kind === 'mcp' ? t('agentBuilder.mcpServerDefaultName') : t('chat.connectTools.kindAction')}
            </Text>
          </Flex>
          {/* One column for every row's Connect, Connected or status, so they line up down the card. */}
          <Flex data-testid="connect-tools-status" align="center" justify="end" style={{ width: STATUS_COLUMN, flexShrink: 0 }}>
            {connected.has(key) ? (
              <Flex align="center" gap="1">
                <MaterialIcon name="check_circle" size={14} color="var(--emerald-a11)" />
                <Text size="1" style={{ color: 'var(--emerald-a11)' }}>
                  {t('chat.mcpConnect.connected')}
                </Text>
              </Flex>
            ) : connectableHere(item, serviceAccount) ? (
              <Button
                size="1"
                variant="soft"
                className="touch-size"
                onClick={() => connect(item)}
                disabled={busyKey !== null}
                style={{ height: BUTTON_HEIGHT }}
              >
                {busyKey === key ? t('chat.mcpConnect.connecting') : t('chat.mcpConnect.connect')}
              </Button>
            ) : (
              <Text size="1" title={statusText(item)} style={{ ...TWO_LINES, color: 'var(--text-secondary)', textAlign: 'right' }}>
                {statusText(item)}
              </Text>
            )}
          </Flex>
        </Flex>
        {error && (
          <Text as="p" size="1" role="alert" style={{ color: 'var(--red-11)', margin: 0, paddingBottom: 'var(--space-2)' }}>
            {error}
          </Text>
        )}
      </Box>
    );
  };

  // Actions first, then MCP servers; each kind under its own name only when both are there.
  const groups = [
    { kind: 'toolset' as const, label: t('chat.agentResources.actionsTab'), items: items.filter((item) => item.kind !== 'mcp') },
    { kind: 'mcp' as const, label: t('agentBuilder.mcpServersSection'), items: items.filter((item) => item.kind === 'mcp') },
  ].filter((group) => group.items.length > 0);
  const labelled = groups.length > 1;
  const folded = !showAll && items.length > FOLD_AFTER;
  let budget = folded ? FOLD_AFTER : items.length;
  const shownGroups = groups.flatMap((group) => {
    const shown = group.items.slice(0, budget);
    budget -= shown.length;
    return shown.length > 0 ? [{ ...group, items: shown }] : [];
  });

  return (
    <Box
      data-testid={testId ?? 'connect-tools'}
      style={{
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--surface-subtle)',
        overflow: 'hidden',
        marginTop: blocked ? undefined : 'var(--space-3)',
      }}
    >
      <Box style={{ padding: 'var(--space-3) var(--space-3) var(--space-1)' }}>
        <Flex align="center" gap="2" mb="1">
          <Flex
            data-testid="connect-tools-mark"
            align="center"
            justify="center"
            style={{
              width: 'var(--space-5)',
              height: 'var(--space-5)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--amber-a3)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="link_off" size={14} color="var(--amber-11)" />
          </Flex>
          <Text size="2" weight="medium" style={{ color: 'var(--text-primary)' }}>
            {title}
          </Text>
        </Flex>

        {shownGroups.map((group) => (
          <Box key={group.kind}>
            {labelled && (
              <Text
                as="div"
                size="1"
                weight="medium"
                data-testid="connect-tools-group"
                style={{ color: 'var(--text-secondary)', paddingTop: 'var(--space-2)' }}
              >
                {group.label}
              </Text>
            )}
            {group.items.map((item, i) => rowFor(item, i === 0))}
          </Box>
        ))}

        {items.length > FOLD_AFTER && (
          <Button
            type="button"
            size="1"
            variant="ghost"
            color="gray"
            className="touch-size"
            aria-expanded={showAll}
            onClick={() => setShowAll((v) => !v)}
            style={{ marginBlock: 'var(--space-1)' }}
          >
            {showAll ? t('agentBuilder.showLess') : t('chat.connectTools.showMore', { count: items.length - FOLD_AFTER })}
          </Button>
        )}

        {serviceAccount && (
          <Flex direction="column" gap="1" py="2">
            <Text size="1" style={{ color: 'var(--text-secondary)' }}>
              {t('chat.connectTools.serviceAccount', { tools: items.map((item) => item.displayName).join(', ') })}
            </Text>
            {agentId && (
              <Text size="1">
                <a href={`/agents/edit?agentKey=${encodeURIComponent(agentId)}`} style={{ color: 'var(--accent-11)' }}>
                  {t('chat.mcpConnect.openAgentBuilder')}
                </a>
              </Text>
            )}
          </Flex>
        )}
      </Box>

      {ready && !resent && (
        <Flex
          data-testid="connect-tools-ready"
          align="center"
          justify="between"
          gap="3"
          style={{
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--emerald-a2)',
            padding: 'var(--space-2) var(--space-3)',
          }}
        >
          <Text size="1" style={{ color: 'var(--text-secondary)' }}>
            {blocked ? t('chat.mcpConnect.allConnected') : t('chat.connectTools.connectedAskAgain')}
          </Text>
          <Button size="1" className="touch-size" onClick={tryAgain} style={{ height: BUTTON_HEIGHT, flexShrink: 0 }}>
            {t('chat.mcpConnect.tryAgain')}
          </Button>
        </Flex>
      )}

      <McpAuthDialog
        instance={tokenEntry}
        open={tokenEntry !== null}
        onOpenChange={(open) => {
          if (!open) setTokenEntry(null);
        }}
        onAuthenticated={() => {
          if (tokenEntryId) markConnected(`mcp:${tokenEntryId}`);
        }}
      />

      {toolsetDialog && (
        <UserToolsetConfigDialog
          toolset={toolsetDialog.toolset}
          instanceId={toolsetDialog.item.instanceId}
          onClose={() => setToolsetDialog(null)}
          onSuccess={() => markConnected(keyOf(toolsetDialog.item))}
        />
      )}
    </Box>
  );
}
