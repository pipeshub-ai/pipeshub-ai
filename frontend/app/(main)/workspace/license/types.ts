/** What GET /api/v1/license answers (backend `LicenseStatusView`, ee/modules/license/license.service.ts). */

export type LicenseStatus = 'none' | 'pending' | 'active' | 'expiring' | 'grace' | 'expired';

export interface LicenseView {
  licenseId: string;
  licensee: string;
  type: 'trial' | 'pro' | 'development' | string;
  seats: number | 'unlimited';
  features: string[];
  url: string | null;
  notForProduction: boolean;
  startDate: string;
  endDate: string;
  graceEndsAt: string;
}

export interface LicenseStatusView {
  status: LicenseStatus;
  license: LicenseView | null;
  upcoming: LicenseView | null;
  /** Null while no license has started and enforcement is off: everything works as before license keys. */
  permissionSync: { allowed: boolean; until: string | null } | null;
  ignored: number;
  /** Null until enforcement is on; `endsAt` null for an install that needs no grace. */
  upgradeGrace: { endsAt: string | null; affected: boolean | null } | null;
}

export interface InstallResult {
  installed: boolean;
  alreadyInstalled: boolean;
  status: LicenseStatusView;
}
