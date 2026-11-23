import type { TFunction } from 'i18next';
import type { LicenseStatus, LicenseStatusView } from './types';

export const STATUS_COLOR: Record<LicenseStatus, 'green' | 'amber' | 'red' | 'gray'> = {
  active: 'green',
  expiring: 'amber',
  grace: 'amber',
  expired: 'red',
  pending: 'gray',
  none: 'gray',
};

export function statusLabel(t: TFunction, status: LicenseStatus): string {
  switch (status) {
    case 'active': return t('workspace.license.status.active');
    case 'expiring': return t('workspace.license.status.expiring');
    case 'grace': return t('workspace.license.status.grace');
    case 'expired': return t('workspace.license.status.expired');
    case 'pending': return t('workspace.license.status.pending');
    default: return t('workspace.license.status.none');
  }
}

export function typeLabel(t: TFunction, type: string): string {
  switch (type) {
    case 'trial': return t('workspace.license.type.trial');
    case 'pro': return t('workspace.license.type.pro');
    case 'development': return t('workspace.license.type.development');
    default: return type;
  }
}

/** Plain words for a refused key; the server's own sentence for the rarer reasons. */
export function installErrorMessage(t: TFunction, reason: string | null, serverMessage: string): string {
  switch (reason) {
    case 'LICENSE_BAD_SIGNATURE': return t('workspace.license.errors.badSignature');
    case 'LICENSE_WRONG_ADDRESS': return t('workspace.license.errors.wrongAddress');
    case 'LICENSE_EXPIRED': return t('workspace.license.errors.expired');
    case 'LICENSE_MALFORMED': return t('workspace.license.errors.malformed');
    default: return serverMessage || t('workspace.license.errors.generic');
  }
}

const isFuture = (iso: string | null | undefined): iso is string =>
  !!iso && Date.parse(iso) > Date.now();

/** The upgrade grace in force: no license key has started and the grace has not ended. */
export function upgradeGraceEnd(view: LicenseStatusView): string | null {
  const noLicense = view.status === 'none' || view.status === 'pending';
  return noLicense && isFuture(view.upgradeGrace?.endsAt) ? view.upgradeGrace.endsAt : null;
}
