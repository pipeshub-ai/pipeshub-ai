'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Avatar, Badge, Button, Flex, IconButton, Separator, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { apiClient } from '@/lib/api';
import { formatRelativeTime } from '@/lib/utils/formatters';
import { isNoneAuthType, isOAuthType } from '@/app/(main)/workspace/connectors/utils/auth-helpers';

export interface ToolsetInstanceRowCardProps {
  /** Team = admin catalog (org instances); personal = per-user credentials. */
  scope: 'team' | 'personal';
  instance: BuilderSidebarToolset;
  onAuthenticate: () => void;
  onConfigure: () => void;
  /** Admin workspace: open manage OAuth / delete panel. */
  onManage?: () => void;
}

export function ToolsetInstanceRowCard({
  scope,
  instance,
  onAuthenticate,
  onConfigure,
  onManage,
}: ToolsetInstanceRowCardProps) {
  const { t } = useTranslation();
  /** Org instance label — primary in per-instance lists (type detail / admin instances). */
  const primaryTitle =
    (instance.instanceName || '').trim() || instance.displayName || instance.toolsetType || '';
  /** Integration / toolset name for subtitle when it differs from the instance name. */
  const integrationSubtitle = (instance.displayName || instance.toolsetType || '').trim();
  const ok = instance.isAuthenticated;
  const authTypeUpper = (instance.authType || 'NONE').toUpperCase();
  /**
   * End-user sign-in (personal) or org OAuth on the team list. Team + non-OAuth is configured in Manage only
   * (same predicate drives the amber badge and the Authenticate button).
   */
  const showUserAuthFlow =
    !ok &&
    !isNoneAuthType(authTypeUpper) &&
    (isOAuthType(authTypeUpper) || scope === 'personal');

  const [enabledByName, setEnabledByName] = useState<string | null>(null);
  const [enabledByAvatar, setEnabledByAvatar] = useState<string | null>(null);

  useEffect(() => {
    if (!instance.createdBy) return;
    let cancelled = false;

    async function fetchUserData() {
      try {
        const { data } = await apiClient.post('/api/v1/users/by-ids', {
          userIds: [instance.createdBy],
        });
        if (cancelled) return;

        const users = Array.isArray(data) ? data : data.users ?? [];
        if (users.length > 0) {
          const user = users[0] as Record<string, unknown>;
          const fullName = (user.name as string) ?? (user.fullName as string) ?? '';
          const userId = (user.id as string) ?? (user._id as string) ?? instance.createdBy;
          const parts = fullName.trim().split(/\s+/);
          const displayName =
            parts.length > 1 ? `${parts[0]} ${parts[parts.length - 1][0]}` : parts[0] || '';
          setEnabledByName(displayName);
          if (userId) setEnabledByAvatar(`/api/v1/users/${userId}/dp`);
        }
      } catch {
        /* ignore */
      }
    }

    void fetchUserData();
    return () => {
      cancelled = true;
    };
  }, [instance.createdBy]);

  const ts = instance.updatedAtTimestamp ?? instance.createdAtTimestamp;
  const when = ts ? formatRelativeTime(ts) : '';

  const toolTags = (instance.tools || []).map((x) => x.name).filter(Boolean);

  return (
    <Flex
      direction="column"
      style={{
        backgroundColor: 'var(--gray-2)',
        border: '1px solid var(--gray-3)',
        borderRadius: 'var(--radius-4)',
        overflow: 'hidden',
      }}
    >
      <Flex align="center" gap="3" style={{ padding: 16, minWidth: 0 }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--gray-a3)',
            flexShrink: 0,
            overflow: 'hidden',
          }}
        >
          {instance.toolsetType || instance.name ? (
            <ConnectorIcon type={instance.toolsetType || instance.name} size={22} />
          ) : (
            <MaterialIcon name="bolt" size={20} color="var(--gray-11)" />
          )}
        </Flex>
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }} truncate>
            {primaryTitle}
          </Text>
          {integrationSubtitle && integrationSubtitle.toLowerCase() !== primaryTitle.toLowerCase() ? (
            <Text size="1" color="gray" truncate>
              {integrationSubtitle}
            </Text>
          ) : null}
        </Flex>
        <Flex
          align="center"
          gap="3"
          wrap="wrap"
          justify="end"
          style={{ flexShrink: 0, rowGap: 'var(--space-2)' }}
        >
          {showUserAuthFlow ? (
            <Badge size="3" color="amber" variant="soft">
              <MaterialIcon name="vpn_key" size={14} color="var(--amber-11)" />
              <Text as="span" size="2" weight="medium" style={{ color: 'var(--amber-11)' }}>
                {t('workspace.actions.instanceCard.badgeAuthNeeded')}
              </Text>
            </Badge>
          ) : ok ? (
            <Badge size="3" color="green" variant="soft">
              <MaterialIcon name="check" size={14} color="var(--green-11)" />
              <Text as="span" size="2" weight="medium" style={{ color: 'var(--green-11)' }}>
                {t('workspace.actions.instanceCard.badgeAuthenticated')}
              </Text>
            </Badge>
          ) : null}
          {showUserAuthFlow ? (
            <Button size="2" variant="soft" color="gray" onClick={onAuthenticate}>
              {t('workspace.actions.instanceCard.authenticateCta')}
            </Button>
          ) : null}
          {onManage ? (
            <IconButton
              type="button"
              variant="soft"
              color="gray"
              size="2"
              aria-label={t('workspace.actions.instanceCard.manage')}
              onClick={onManage}
            >
              <MaterialIcon name="settings" size={18} color="var(--gray-11)" />
            </IconButton>
          ) : ok ? (
            <Button size="2" variant="soft" color="gray" onClick={onConfigure}>
              {t('workspace.actions.instanceCard.configureCta')}
            </Button>
          ) : null}
        </Flex>
      </Flex>

      {toolTags.length > 0 ? (
        <>
          <Separator size="4" m="0" />
          <Flex direction="column" gap="2" style={{ padding: '12px 16px 16px' }}>
            <Text size="1" weight="medium" style={{ color: 'var(--gray-11)', letterSpacing: '0.04em' }}>
              {t('workspace.actions.instanceCard.availableActionsLabel')}
            </Text>
            <Flex gap="2" wrap="wrap">
              {toolTags.map((tag) => (
                <Badge key={tag} size="1" variant="soft" color="gray">
                  {tag}
                </Badge>
              ))}
            </Flex>
          </Flex>
        </>
      ) : null}

      {enabledByName ? (
        <>
          <Separator size="4" m="0" />
          <Flex align="center" gap="3" style={{ padding: '12px 16px 16px' }}>
            <Text size="1" weight="medium" style={{ color: 'var(--gray-11)', letterSpacing: '0.04em' }}>
              {t('workspace.actions.instanceCard.enabledByLabel')}
            </Text>
            <Flex align="center" gap="2">
              <Avatar
                size="1"
                src={enabledByAvatar ?? undefined}
                fallback={enabledByName?.[0] ?? '?'}
                radius="full"
              />
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {enabledByName}
              </Text>
              {when ? (
                <Text size="1" color="gray">
                  {when}
                </Text>
              ) : null}
            </Flex>
          </Flex>
        </>
      ) : null}
    </Flex>
  );
}
