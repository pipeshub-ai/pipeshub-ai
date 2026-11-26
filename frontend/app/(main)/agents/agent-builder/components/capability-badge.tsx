'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { workspacePageLinks } from '@/config';
import { ControlPlaneApi, type GrantableItem } from '../../../workspace/harness-ui/control-plane-api';

/** Installation id → what a turn of the author's would see if the agent used it. */
export type Grantable = ReadonlyMap<string, GrantableItem>;

/**
 * The agent's grantable items (CPD-11 R22) for a saved agent. Null while
 * loading, for a new agent, and wherever the answer cannot be had (the pin
 * off, not the agent's editor, an outage): no badge is then better than a
 * wrong one.
 */
export function useGrantable(agentKey: string | null): Grantable | null {
  const [grantable, setGrantable] = useState<Grantable | null>(null);
  useEffect(() => {
    setGrantable(null);
    if (agentKey === null) return undefined;
    let live = true;
    ControlPlaneApi.grantable(agentKey)
      .then((items) => {
        if (live) setGrantable(new Map(items.map((item) => [item.installation, item])));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [agentKey]);
  return grantable;
}

/** The skill installation id a skill name has (the control plane's `skillInstallationId`). */
export const skillInstallationId = (name: string): string => `skill_${name.replace(/[^A-Za-z0-9_-]+/g, '-')}`;

type Badge = { key: string; href: string | null };

/** The badge for a state, and where its remedy is: a page the reader can open, or none (never a dead link). */
function badgeOf(item: GrantableItem, isAdmin: boolean): Badge | null {
  if (item.state === 'ready') return null;
  switch (item.reason) {
    case 'credential_missing':
    case 'credential_expired':
    case 'credential_revoked':
      return {
        key: 'needsConnection',
        href: item.kind === 'mcp' ? '/workspace/mcp-servers/personal' : item.kind === 'toolset' ? '/workspace/actions/personal' : null,
      };
    case 'not_approved':
      return {
        key: 'waitingApproval',
        href: !isAdmin ? null : item.kind === 'mcp' ? '/workspace/mcp-servers/team' : (workspacePageLinks['approval-policy'] ?? null),
      };
    case 'disabled':
      return { key: 'disabled', href: null };
    case 'not_visible':
      return { key: 'notShared', href: null };
    case 'policy_ceiling':
      return { key: 'blockedByPolicy', href: isAdmin ? '/workspace/ai-access' : null };
    default:
      return null;
  }
}

/** "● Needs your connection →" under a sidebar item a run of this agent could not use (CPD-11 U6). */
export function CapabilityBadge({ item }: { item: GrantableItem | undefined }) {
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const badge = item === undefined ? null : badgeOf(item, isAdmin);
  if (badge === null) return null;
  const label = t(`agentBuilder.capability.${badge.key}`);
  return (
    <Flex align="center" gap="1" data-capability-state={badge.key} style={{ padding: '0 8px 4px 36px', minWidth: 0 }}>
      <Text size="1" aria-hidden style={{ color: 'var(--amber-11)' }}>
        ●
      </Text>
      {badge.href === null ? (
        <Text size="1" style={{ color: 'var(--amber-11)' }}>
          {label}
        </Text>
      ) : (
        <Link href={badge.href} style={{ fontSize: 12, color: 'var(--amber-11)' }}>
          {label} →
        </Link>
      )}
    </Flex>
  );
}
