'use client';

import React, { useState } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import { Box, Callout, Flex, IconButton, Link, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { daysUntil, formatDay, reminderStep, useLicenseNotice, type ConnectorNotice } from './use-license-notice';

const LICENSE_PAGE = '/workspace/license/';
const DISMISSED_KEY = 'pipeshub.license.graceReminderDismissed';

const readDismissed = (): string | null => {
  try {
    return window.localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
};

function Banner({
  color,
  icon,
  children,
  onDismiss,
}: {
  color: 'amber' | 'red' | 'blue' | 'gray';
  icon: string;
  children: React.ReactNode;
  onDismiss?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Callout.Root color={color} size="1" role="status" style={{ borderRadius: 0 }}>
      <Callout.Icon>
        <MaterialIcon name={icon} size={16} />
      </Callout.Icon>
      <Flex align="start" justify="between" gap="2" style={{ width: '100%' }}>
        <Box style={{ minWidth: 0 }}>{children}</Box>
        {onDismiss && (
          <IconButton size="1" variant="ghost" color="gray" onClick={onDismiss} aria-label={t('licenseNotices.dismiss')}>
            <MaterialIcon name="close" size={16} />
          </IconButton>
        )}
      </Flex>
    </Callout.Root>
  );
}

const LicensePageLink = ({ children }: { children: React.ReactNode }) => (
  <Link asChild size="2" weight="medium">
    <NextLink href={LICENSE_PAGE}>{children}</NextLink>
  </Link>
);

function syncWord(t: (key: string) => string, status: string | null): string {
  if (status === 'QUEUED') return t('licenseNotices.restoring.queued');
  if (status === 'SYNCING' || status === 'FULL_SYNCING') return t('licenseNotices.restoring.syncing');
  return t('licenseNotices.restoring.waiting');
}

/**
 * The license notices above every page, each only when it applies: a development key (everyone),
 * the upgrade grace countdown and limited connectors (admins), and restoring access (admins, and
 * members whose results are short of a connector someone else created).
 */
export function LicenseBanners() {
  const { t, i18n } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const notice = useLicenseNotice();
  const [dismissed, setDismissed] = useState(readDismissed);
  if (!notice) return null;

  const graceEnd = notice.upgradeGrace?.endsAt;
  const graceKey = notice.upgradeGrace?.key ?? 'missing';
  const keyDate = notice.upgradeGrace?.keyDate ?? '';
  const day = (iso: string) => formatDay(iso, i18n.language);
  const daysLeft = graceEnd ? daysUntil(graceEnd) : 0;
  const reminder = graceEnd ? `${graceEnd}|${reminderStep(daysLeft)}` : null;
  const showGrace = isAdmin && reminder !== null && dismissed !== reminder;
  const { limited, restoring, items } = notice.connectors;
  const restoringItems: ConnectorNotice[] = (items ?? []).filter((c) => c.state === 'restoring');

  const dismissGrace = () => {
    try {
      window.localStorage.setItem(DISMISSED_KEY, reminder);
    } catch {
      // Not remembered: it shows again on the next page load.
    }
    setDismissed(reminder);
  };

  const banners: React.ReactNode[] = [];
  if (notice.notForProduction) {
    banners.push(
      <Banner key="dev" color="amber" icon="construction">
        <Text size="2">{t('licenseNotices.developmentKey')}</Text>
      </Banner>,
    );
  }
  if (showGrace) {
    banners.push(
      <Banner key="grace" color={daysLeft <= 3 ? 'red' : 'amber'} icon="schedule" onDismiss={dismissGrace}>
        <Text size="2">
          {graceKey === 'startsLater' ? (
            t('licenseNotices.graceKeyStartsLater', { start: day(keyDate), date: day(graceEnd) })
          ) : graceKey === 'ended' ? (
            <>
              {t('licenseNotices.graceKeyEnded', { ended: day(keyDate), date: day(graceEnd) })}{' '}
              <LicensePageLink>{t('licenseNotices.openLicensePage')}</LicensePageLink>
            </>
          ) : (
            <>
              {t('licenseNotices.grace', { count: daysLeft, date: day(graceEnd) })}{' '}
              <LicensePageLink>{t('licenseNotices.installKey')}</LicensePageLink>
            </>
          )}
        </Text>
      </Banner>,
    );
  }
  if (isAdmin && limited > 0) {
    banners.push(
      <Banner key="limited" color="red" icon="lock">
        <Text size="2">
          {t('licenseNotices.limited', { count: limited })}{' '}
          <LicensePageLink>{t('licenseNotices.installKey')}</LicensePageLink>
        </Text>
      </Banner>,
    );
  }
  if (restoring > 0) {
    banners.push(
      <Banner key="restoring" color="blue" icon="sync">
        <Text size="2" as="div">
          {isAdmin ? t('licenseNotices.restoring.admin', { count: restoring }) : t('licenseNotices.restoring.member')}
        </Text>
        {isAdmin && restoringItems.length > 0 && (
          <Text size="1" as="div" style={{ color: 'var(--slate-11)', marginTop: 2 }}>
            {restoringItems.map((c) => `${c.name}: ${syncWord(t, c.status)}`).join(' · ')}
          </Text>
        )}
      </Banner>,
    );
  }
  if (!banners.length) return null;
  return (
    <Flex direction="column" data-testid="license-banners" style={{ flexShrink: 0 }}>
      {banners}
    </Flex>
  );
}
