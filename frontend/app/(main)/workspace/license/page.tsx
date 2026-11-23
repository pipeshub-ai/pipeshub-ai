'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Badge, Box, Button, Callout, Flex, Heading, Link, Text, TextArea } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useFrontendPublicUrl } from '@/lib/hooks/use-frontend-public-url';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { SettingsSection } from '@/app/(main)/workspace/components/settings-section';
import { LicenseApi, licenseErrorReason, procurementLink, PROCUREMENT_PATHS } from './api';
import type { LicenseStatusView, LicenseView } from './types';
import { installErrorMessage, STATUS_COLOR, statusLabel, typeLabel, upgradeGraceEnd } from './license-text';

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Flex justify="between" gap="3" wrap="wrap">
      <Text size="2" style={{ color: 'var(--slate-11)' }}>{label}</Text>
      <Text as="div" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{children}</Text>
    </Flex>
  );
}

/** The key in force (or the one that ended last), or with `pending` a key dated ahead that has not started yet. */
function LicenseDetails({ view, license, pending = false }: { view: LicenseStatusView; license: LicenseView; pending?: boolean }) {
  const { t, i18n } = useTranslation();
  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language, { dateStyle: 'medium' });
  return (
    <>
      {pending && (
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.license.startsOn', { date: date(license.startDate) })}
        </Text>
      )}
      <Detail label={t('workspace.license.fields.type')}>
        <Flex gap="2" align="center" wrap="wrap" justify="end">
          {typeLabel(t, license.type)}
          {license.notForProduction && (
            <Badge color="amber" variant="soft">{t('workspace.license.notForProduction')}</Badge>
          )}
        </Flex>
      </Detail>
      <Detail label={t('workspace.license.fields.licensee')}>{license.licensee}</Detail>
      <Detail label={t('workspace.license.fields.seats')}>
        {license.seats === 'unlimited' ? t('workspace.license.unlimitedSeats') : license.seats}
      </Detail>
      <Detail label={t('workspace.license.fields.endDate')}>{date(license.endDate)}</Detail>
      {(view.status === 'grace' || view.status === 'expiring') && (
        <Detail label={t('workspace.license.fields.graceEndsAt')}>{date(license.graceEndsAt)}</Detail>
      )}
      {!pending && view.upcoming && (
        <Detail label={t('workspace.license.fields.upcoming')}>{date(view.upcoming.startDate)}</Detail>
      )}
    </>
  );
}

export default function LicensePage() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const publicUrl = useFrontendPublicUrl();
  const fileInput = useRef<HTMLInputElement>(null);

  const [view, setView] = useState<LicenseStatusView | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [keyText, setKeyText] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) router.replace('/workspace/general');
  }, [isProfileInitialized, isAdmin, router]);

  const load = useCallback(async () => {
    try {
      setView(await LicenseApi.getStatus());
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === true) void load();
  }, [isProfileInitialized, isAdmin, load]);

  const install = async () => {
    setInstallError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(keyText);
    } catch {
      setInstallError(t('workspace.license.errors.malformed'));
      return;
    }
    setInstalling(true);
    try {
      const result = await LicenseApi.install(parsed);
      setView(result.status);
      setKeyText('');
      // The banners and connector notices change with the key.
      revalidateSwrNamespace(SWR_NS.licenseNotice);
      addToast({
        variant: 'success',
        title: result.alreadyInstalled
          ? t('workspace.license.toasts.alreadyInstalled')
          : t('workspace.license.toasts.installed'),
      });
    } catch (error) {
      setInstallError(
        installErrorMessage(t, licenseErrorReason(error), (error as { message?: string })?.message ?? ''),
      );
    } finally {
      setInstalling(false);
    }
  };

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    setKeyText(await file.text());
    setInstallError(null);
  };

  if (!isProfileInitialized || isAdmin !== true) return null;

  if (!view && !loadFailed) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language, { dateStyle: 'medium' });
  const graceEnd = view && upgradeGraceEnd(view);
  const syncOff = view?.permissionSync?.allowed === false;
  const inForce = !!view && (view.status === 'active' || view.status === 'expiring' || view.status === 'grace');
  // No key in force and none waiting to start: none installed, or the last one ended.
  const noKey = !!view && !inForce && !view.upcoming;
  const keyLacksSync = inForce && !view.license?.features.includes('permissionSync');
  const address = publicUrl ?? undefined;

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer maxWidth={760}>
        <Flex direction="column" gap="5">
          <Box>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.license.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}>
              {t('workspace.license.subtitle')}
            </Text>
          </Box>

          {loadFailed && (
            <Callout.Root color="red">
              <Callout.Text>{t('workspace.license.loadFailed')}</Callout.Text>
            </Callout.Root>
          )}

          {graceEnd && (
            <Callout.Root color="amber">
              <Callout.Icon><MaterialIcon name="schedule" size={16} /></Callout.Icon>
              <Callout.Text>{t('workspace.license.upgradeGrace', { date: date(graceEnd) })}</Callout.Text>
            </Callout.Root>
          )}

          {syncOff && (noKey || keyLacksSync) && (
            <Callout.Root color="red">
              <Callout.Icon><MaterialIcon name="lock" size={16} /></Callout.Icon>
              <Callout.Text>
                {noKey ? t('workspace.license.permissionSyncOff') : t('workspace.license.keyLacksPermissionSync')}
              </Callout.Text>
            </Callout.Root>
          )}

          {view && (
            <SettingsSection
              title={t('workspace.license.statusTitle')}
              rightAction={
                <Badge color={STATUS_COLOR[view.status] ?? 'gray'} variant="soft">
                  {statusLabel(t, view.status)}
                </Badge>
              }
            >
              {view.license ? (
                <LicenseDetails view={view} license={view.license} />
              ) : (
                view.upcoming && <LicenseDetails view={view} license={view.upcoming} pending />
              )}
              <Detail label={t('workspace.license.fields.permissionSync')}>
                {syncOff ? t('workspace.license.off') : t('workspace.license.on')}
              </Detail>
              {!view.permissionSync && (
                <Text size="1" style={{ color: 'var(--slate-11)' }}>{t('workspace.license.notEnforced')}</Text>
              )}
            </SettingsSection>
          )}

          <SettingsSection title={t('workspace.license.installTitle')} description={t('workspace.license.installDescription')}>
            <Flex direction="column" gap="3">
              <TextArea
                aria-label={t('workspace.license.keyLabel')}
                placeholder={t('workspace.license.keyPlaceholder')}
                value={keyText}
                onChange={(e) => setKeyText(e.target.value)}
                rows={6}
                style={{ fontFamily: 'monospace' }}
              />
              <input
                ref={fileInput}
                type="file"
                accept=".json,.lic,.txt,application/json,text/plain"
                hidden
                data-testid="license-file-input"
                onChange={(e) => void readFile(e.target.files?.[0])}
              />
              {installError && (
                <Callout.Root color="red" role="alert">
                  <Callout.Text>{installError}</Callout.Text>
                </Callout.Root>
              )}
              <Flex gap="2" wrap="wrap">
                <Button variant="outline" color="gray" onClick={() => fileInput.current?.click()}>
                  <MaterialIcon name="upload_file" size={16} />
                  {t('workspace.license.upload')}
                </Button>
                <Button onClick={() => void install()} disabled={!keyText.trim() || installing} loading={installing}>
                  {t('workspace.license.install')}
                </Button>
              </Flex>
            </Flex>
          </SettingsSection>

          <Flex gap="4" wrap="wrap">
            {noKey && (
              <Link href={procurementLink(PROCUREMENT_PATHS.trial, { url: address })} target="_blank" rel="noopener noreferrer" size="2">
                {t('workspace.license.getTrial')}
              </Link>
            )}
            {graceEnd && (
              <Link
                href={procurementLink(PROCUREMENT_PATHS.extension, { url: address, graceEndsAt: graceEnd })}
                target="_blank"
                rel="noopener noreferrer"
                size="2"
              >
                {t('workspace.license.requestExtension')}
              </Link>
            )}
          </Flex>
        </Flex>
      </SettingsPageContainer>
    </Box>
  );
}
