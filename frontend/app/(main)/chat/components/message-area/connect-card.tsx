'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { McpAuthDialog } from '@/app/(main)/workspace/mcp-servers/components';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { useMcpOAuthPopup } from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import { useToolsetOauthPopupFlow } from '@/app/(main)/agents/agent-builder/hooks/use-toolset-oauth-popup-flow';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import { isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';
import { interactionAction } from '../../interaction-wording';
import { rejectedTime } from '../../connect-cards';
import type { ApprovalDecision, ConnectTarget, PendingApproval } from '../../types';
import { activeCardStyle, PausedLine, refusalKey, TARGET } from './approval-card';

/** Where the person's sign-in for the item stands: what the Connect button does. */
type Signin =
  | { kind: 'toolset'; toolset: BuilderSidebarToolset }
  | { kind: 'mcp'; entry: McpMyServerEntry }
  | { kind: 'page' };

/** What the card shows while the owner connects: nothing yet, a sign-in window open, or one that did not finish. */
type Phase = 'idle' | 'connecting' | 'failed' | 'elsewhere';

/** The step's need in plain words: "This step needs your Jira account to search issues." */
export function connectNeed(t: TFunction, card: Pick<PendingApproval, 'toolName' | 'displayName' | 'reason'>, target: ConnectTarget): string {
  if (target.reason === 'sign_in_rejected') {
    return target.since
      ? t('connectCard.rejectedAt', { item: target.item, time: rejectedTime(target.since) })
      : t('connectCard.rejected', { item: target.item });
  }
  if (target.itemKind === 'mcp') return t('connectCard.needsMcp', { item: target.item });
  if (!card.toolName) return t('connectCard.needsAccount', { item: target.item });
  return t('connectCard.needsAccountTo', { item: target.item, action: interactionAction(t, card).toLocaleLowerCase() });
}

/** The plain activity line while a step waits (WP-64): "Waiting for you to reconnect GitHub". */
export function connectWaitingLine(t: TFunction, target: ConnectTarget, isOwner: boolean): string {
  const again = target.reason === 'sign_in_rejected';
  if (isOwner) return t(again ? 'connectCard.waitingReconnectYou' : 'connectCard.waitingConnectYou', { item: target.item });
  return t(again ? 'connectCard.waitingReconnectTitle' : 'connectCard.waitingTitle', { item: target.item });
}

/** One receipt line once the wait ended: connected, skipped, not in time, or the task ended first. */
export function connectReceipt(
  t: TFunction,
  card: Pick<PendingApproval, 'status' | 'connect'>,
): { tone: 'ok' | 'denied' | 'muted'; text: string; status: string } {
  const item = card.connect?.item ?? '';
  switch (card.status) {
    case 'approved':
      return { tone: 'ok', text: t('connectCard.receipt.connected', { item }), status: 'approved' };
    case 'denied':
      return { tone: 'muted', text: t('connectCard.receipt.skipped', { item }), status: 'denied' };
    case 'expired':
      return { tone: 'muted', text: t('connectCard.receipt.expired', { item }), status: 'expired' };
    default:
      return { tone: 'muted', text: t('connectCard.receipt.closed', { item }), status: 'closed' };
  }
}

/** The person's own sign-in for the item, read before they click: a popup opened after an await is blocked. */
async function findSignin(target: ConnectTarget): Promise<Signin> {
  if (!target.instanceId) return { kind: 'page' };
  if (target.itemKind === 'toolset') {
    const toolset = await ToolsetsApi.findMyToolsetByInstanceId(target.instanceId);
    return toolset ? { kind: 'toolset', toolset } : { kind: 'page' };
  }
  const { instances } = await McpServersApi.getMyMcpServers(false);
  const entry = instances.find((candidate) => candidate._id === target.instanceId);
  return entry ? { kind: 'mcp', entry } : { kind: 'page' };
}

/** Whether the item is connected now, by the same reads the Connect rows use. */
export async function isConnected(target: ConnectTarget): Promise<boolean> {
  if (!target.instanceId) return false;
  if (target.itemKind === 'toolset') {
    return Boolean((await ToolsetsApi.findMyToolsetByInstanceId(target.instanceId))?.isAuthenticated);
  }
  const { instances } = await McpServersApi.getMyMcpServers(false);
  return Boolean(instances.find((candidate) => candidate._id === target.instanceId)?.isAuthenticated);
}

export interface ConnectCardProps {
  card: PendingApproval;
  onDecide: (decision: Extract<ApprovalDecision, 'approved' | 'denied'>) => void;
  /** The viewer is the person the task runs for: only they can connect (the server checks too). */
  isOwner: boolean;
  active?: boolean;
  paused?: boolean;
}

/**
 * G-12: "Connect Jira to continue" under the step that waits. Connect opens the same sign-in the chat's Connect rows
 * use (an OAuth popup, else the item's own credential dialog); when it succeeds the card answers the wait itself and
 * the task carries on, so nobody asks again. Skip tells the task to go on without it. Someone else viewing may only
 * skip it for the owner. Plain words, no ids.
 */
export function ConnectCard({ card, onDecide, isOwner, active = false, paused = false }: ConnectCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const target = card.connect;
  const [signin, setSignin] = useState<Signin | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [notYet, setNotYet] = useState(false);
  const [toolsetDialog, setToolsetDialog] = useState(false);
  const [mcpDialog, setMcpDialog] = useState(false);
  const decidedRef = useRef(false);
  const sending = card.status === 'sending';

  useEffect(() => {
    if (!isOwner || !target) return;
    let live = true;
    findSignin(target)
      .then((found) => live && setSignin(found))
      .catch(() => live && setSignin({ kind: 'page' }));
    return () => {
      live = false;
    };
  }, [isOwner, target]);

  const carryOn = useCallback(() => {
    if (decidedRef.current) return;
    decidedRef.current = true;
    setPhase('idle');
    onDecide('approved');
  }, [onDecide]);

  const verify = useCallback(async () => (target ? isConnected(target) : false), [target]);
  const failed = useCallback(() => setPhase('failed'), []);

  const { beginOAuth, stopOAuthUi } = useToolsetOauthPopupFlow({
    t,
    verifyAuthenticated: verify,
    onVerified: carryOn,
    onIncomplete: failed,
    onOAuthPopupError: failed,
  });
  useEffect(() => stopOAuthUi, [stopOAuthUi]);
  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: verify,
    onVerified: carryOn,
    onFailed: failed,
  });

  if (!target) return null;
  const item = target.item;
  const refused = card.errorCode ? refusalKey(card.errorCode) ?? 'approvalCard.failed' : null;

  const openPage = () => {
    window.open(target.connectPath, '_blank', 'noopener');
    setPhase('elsewhere');
  };

  const connect = () => {
    setNotYet(false);
    if (signin?.kind === 'toolset') {
      const id = target.instanceId as string;
      if (!isOAuthType(signin.toolset.authType ?? '')) {
        setToolsetDialog(true);
        return;
      }
      setPhase('connecting');
      // Straight from the click: the popup opens before the sign-in address is fetched.
      void beginOAuth(
        'oauth_user_toolset',
        async () => {
          const result = await ToolsetsApi.getInstanceOAuthAuthorizationUrl(id, window.location.origin);
          if (!result.success || !result.authorizationUrl) throw new Error(t('agentBuilder.oauthUrlFailed'));
          return result.authorizationUrl;
        },
        { onTimeout: failed, onOpenError: failed },
      );
      return;
    }
    if (signin?.kind === 'mcp') {
      if (signin.entry.authMode === 'oauth') {
        setPhase('connecting');
        void startOAuthPopup(signin.entry._id);
        return;
      }
      setMcpDialog(true);
      return;
    }
    openPage();
  };

  const alreadyConnected = async () => {
    setNotYet(false);
    if (await verify().catch(() => false)) carryOn();
    else setNotYet(true);
  };

  const skip = () => onDecide('denied');

  const again = target.reason === 'sign_in_rejected';
  const title = isOwner
    ? t(again ? 'connectCard.reconnectTitle' : 'connectCard.title', { item })
    : connectWaitingLine(t, target, false);
  const body = !isOwner
    ? t('connectCard.othersBody', { item })
    : phase === 'connecting'
      ? t('connectCard.finishSigningIn', { item })
      : phase === 'failed' || phase === 'elsewhere'
        ? t('connectCard.connectedElsewhere')
        : connectNeed(t, card, target);
  const offerCarryOn = isOwner && (phase === 'failed' || phase === 'elsewhere');

  return (
    <Card
      role="region"
      aria-label={title}
      data-testid="connect-card"
      data-phase={phase}
      style={{ marginTop: 'var(--space-2)', ...activeCardStyle(active) }}
    >
      <Flex direction="column" gap="2">
        {paused ? <PausedLine /> : null}
        <Flex align="center" gap="2">
          <MaterialIcon name="link" size={18} color="var(--amber-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Text>
        </Flex>
        <Text size="2" color="gray">
          {body}
        </Text>
        {phase === 'failed' ? (
          <Text size="2" color="red" role="alert">
            {t('connectCard.signInFailed', { item })}
          </Text>
        ) : null}
        {notYet ? (
          <Text size="2" color="red" role="alert">
            {t('connectCard.notConnectedYet', { item })}
          </Text>
        ) : null}
        {refused ? (
          <Text size="2" color="red" role="alert">
            {t(refused)}
          </Text>
        ) : null}
        <Flex gap="2" justify="end" wrap="wrap" direction={isMobile ? 'column-reverse' : 'row'}>
          <Button variant="soft" color="gray" disabled={sending} onClick={skip} style={TARGET}>
            {t('connectCard.skip')}
          </Button>
          {offerCarryOn ? (
            <Button variant="soft" disabled={sending} onClick={() => void alreadyConnected()} style={TARGET}>
              {t('connectCard.alreadyConnected')}
            </Button>
          ) : null}
          {isOwner ? (
            <Button
              disabled={sending || phase === 'connecting' || signin === null}
              loading={phase === 'connecting'}
              onClick={connect}
              style={TARGET}
            >
              {phase === 'connecting'
                ? t('connectCard.connecting')
                : t(again ? 'connectCard.reconnect' : 'connectCard.connect', { item })}
            </Button>
          ) : null}
        </Flex>
      </Flex>
      {toolsetDialog && signin?.kind === 'toolset' ? (
        <UserToolsetConfigDialog
          toolset={signin.toolset}
          instanceId={target.instanceId as string}
          onClose={() => setToolsetDialog(false)}
          onSuccess={carryOn}
        />
      ) : null}
      {signin?.kind === 'mcp' ? (
        <McpAuthDialog
          instance={signin.entry}
          open={mcpDialog}
          onOpenChange={setMcpDialog}
          onAuthenticated={carryOn}
        />
      ) : null}
    </Card>
  );
}
