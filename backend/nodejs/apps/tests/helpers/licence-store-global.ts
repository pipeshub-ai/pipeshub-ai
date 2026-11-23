/**
 * Loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * Adding a user now reads the licence to apply its seat limit. The configured
 * licence store opens the real key-value store, so every test starts with an
 * empty one instead: no licence installed, so no seat limit. A test that needs
 * a licence installs its own store with `setLicenseStore` and puts this one back.
 */
import { setLicenseStore } from '../../src/ee/modules/license/license-boot';
import type { LicenseStore } from '../../src/ee/modules/license/license.service';

export const NO_LICENSE_STORE: LicenseStore = {
  read: async () => null,
  write: async () => undefined,
};

setLicenseStore(NO_LICENSE_STORE);
