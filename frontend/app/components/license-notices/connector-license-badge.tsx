'use client';

import { Badge, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { formatDay, useLicenseNotice } from './use-license-notice';

/**
 * On a connector an admin sees: it will be limited when the upgrade grace ends, it is limited,
 * or its access is being restored. Storage and database connectors are never listed by the server.
 */
export function ConnectorLicenseBadge({ connectorId }: { connectorId: string | undefined }) {
  const { t, i18n } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const notice = useLicenseNotice(isAdmin === true);
  const item = connectorId ? notice?.connectors.items?.find((c) => c.id === connectorId) : undefined;
  if (isAdmin !== true || !item) return null;

  const day = (iso: string | null | undefined) => formatDay(iso, i18n.language);
  const grace = notice.upgradeGrace;
  const date = day(grace?.endsAt);
  // A key dated ahead. The server marks the connector only if the key starts after the grace ends, or if it
  // starts in time but does not turn permission sync on.
  const keyLater = grace?.key === 'startsLater' && grace.keyDate !== null && Date.parse(grace.keyDate) > Date.parse(grace.endsAt);
  const affectedTooltip =
    grace?.key !== 'startsLater'
      ? t('licenseNotices.connector.affectedTooltip', { date })
      : t(keyLater ? 'licenseNotices.connector.affectedTooltipKeyLater' : 'licenseNotices.connector.affectedTooltipKeyWithoutSync', {
          start: day(grace.keyDate),
          date,
        });
  const [label, tooltip, color, icon] =
    item.state === 'affected'
      ? [t('licenseNotices.connector.affected'), affectedTooltip, 'amber', 'schedule']
      : item.state === 'restoring'
        ? [t('licenseNotices.connector.restoring'), t('licenseNotices.connector.restoringTooltip'), 'blue', 'sync']
        : [t('licenseNotices.connector.limited'), t('licenseNotices.connector.limitedTooltip'), 'gray', 'lock'];

  return (
    <Tooltip content={tooltip}>
      <Badge color={color as 'amber' | 'blue' | 'gray'} variant="soft" size="1" style={{ flexShrink: 0, minHeight: 24 }}>
        <MaterialIcon name={icon} size={14} />
        {label}
      </Badge>
    </Tooltip>
  );
}
