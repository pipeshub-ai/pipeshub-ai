'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useLicenseNotice } from './use-license-notice';

/**
 * Under search results and answers, for a member: some company connectors are limited to the
 * people who created them, so results may be shorter. It never names them; a member cannot open them.
 */
export function LimitedSourcesNote() {
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const notice = useLicenseNotice(isAdmin === false);
  if (isAdmin !== false || !notice) return null;
  const { limited, restoring } = notice.connectors;
  if (limited + restoring === 0) return null;
  return (
    <Flex align="start" gap="1" role="status" style={{ marginBottom: 'var(--space-3)', color: 'var(--slate-11)' }}>
      <MaterialIcon name="info" size={14} color="var(--slate-11)" />
      <Text size="1">{limited > 0 ? t('licenseNotices.memberLimited') : t('licenseNotices.memberRestoring')}</Text>
    </Flex>
  );
}
