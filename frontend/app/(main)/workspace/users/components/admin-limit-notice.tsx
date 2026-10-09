'use client';

import React from 'react';
import { Callout, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useAdminLimitStatus } from '../use-admin-limit-status';
import type { AdminLimitStatus } from '../types';

export interface AdminLimitNotice {
  kind: 'upcoming' | 'due';
  adminCount: number;
  enforcementDate: Date;
  retainedAdminEmail: string | null;
}

/**
 * What to tell an admin whose org has more admins than Community Edition
 * allows: before the enforcement date it is a heads-up, after it the extra
 * admins are made members on the next restart. Nothing for anyone else.
 */
export function getAdminLimitNotice(
  status: AdminLimitStatus | null,
  isAdmin: boolean | null,
  now: Date = new Date()
): AdminLimitNotice | null {
  if (isAdmin !== true || !status?.overLimit) return null;
  const enforcementDate = new Date(status.enforcementDate);
  if (Number.isNaN(enforcementDate.getTime())) return null;
  return {
    kind: now < enforcementDate ? 'upcoming' : 'due',
    adminCount: status.adminCount,
    enforcementDate,
    retainedAdminEmail: status.retainedAdminEmail,
  };
}

interface AdminLimitNoticeBannerProps {
  style?: React.CSSProperties;
}

/** Not dismissible: it stays until the org is down to one admin. */
export function AdminLimitNoticeBanner({ style }: AdminLimitNoticeBannerProps) {
  const { t, i18n } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const status = useAdminLimitStatus();

  const notice = getAdminLimitNotice(status, isAdmin);
  if (!notice) return null;

  const date = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'long',
    timeZone: 'UTC',
  }).format(notice.enforcementDate);
  const retainedAdmin =
    notice.retainedAdminEmail ?? t('workspace.users.adminLimitNotice.oneAdmin', 'one admin');
  const values = { date, count: notice.adminCount, retainedAdmin };

  return (
    <Callout.Root color="orange" variant="surface" size="1" style={{ width: '100%', ...style }}>
      <Callout.Icon>
        <MaterialIcon name="info" size={16} />
      </Callout.Icon>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium">
          {t(`workspace.users.adminLimitNotice.${notice.kind}Title`, values)}
        </Text>
        <Text size="2">{t(`workspace.users.adminLimitNotice.${notice.kind}Description`, values)}</Text>
      </Flex>
    </Callout.Root>
  );
}
