'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Callout, Link } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { RELEASE_DEFAULTS } from '@/lib/contracts/harness/generated/core/policy-data';
import type { CodingEngineView } from '../types';

/** Where an admin reads how to install gVisor (`policy/release.json` `engineIsolation`). */
export const GVISOR_INSTALL_GUIDE = RELEASE_DEFAULTS.engineIsolation.installGuide;
/** The guide's section on registering runsc with its own syscall filter (`--oci-seccomp`). */
export const GVISOR_SYSCALL_FILTER_GUIDE = `${GVISOR_INSTALL_GUIDE}#syscall-filter`;
/** The picker's reason for an engine this server cannot sandbox at its floor (the server's code, lower-cased). */
export const ISOLATION_REASON = 'engine_isolation_unavailable';

/**
 * What an admin must know about the engines' sandbox, above the
 * coding-engine settings: gVisor missing on a production install, with its
 * install guide; or, while the dev/test override is on, a warning that stays
 * until it is turned off; or gVisor without its own syscall filter
 * (R32-SECCOMP), with the guide's fix. Nothing otherwise.
 */
export function EngineIsolationNotice({
  view,
}: {
  view: Pick<CodingEngineView, 'engines' | 'withoutGvisor' | 'gvisorSyscallFilter'>;
}) {
  const { t } = useTranslation();
  if (view.withoutGvisor) {
    return (
      <Callout.Root color="amber" size="1" role="status">
        <Callout.Icon>
          <MaterialIcon name="warning" size={16} />
        </Callout.Icon>
        <Callout.Text size="1">{t('aiAccounts.codingEngine.isolation.weaker')}</Callout.Text>
      </Callout.Root>
    );
  }
  if (!view.engines.some((engine) => engine.reason === ISOLATION_REASON)) {
    if (view.gvisorSyscallFilter !== false) return null;
    return (
      <Callout.Root color="amber" size="1" role="status">
        <Callout.Icon>
          <MaterialIcon name="shield" size={16} />
        </Callout.Icon>
        <Callout.Text size="1">
          {t('aiAccounts.codingEngine.isolation.noSyscallFilter')}{' '}
          <Link href={GVISOR_SYSCALL_FILTER_GUIDE} target="_blank" rel="noopener noreferrer">
            {t('aiAccounts.codingEngine.isolation.syscallFilterGuide')}
          </Link>
        </Callout.Text>
      </Callout.Root>
    );
  }
  return (
    <Callout.Root color="red" size="1" role="alert">
      <Callout.Icon>
        <MaterialIcon name="shield" size={16} />
      </Callout.Icon>
      <Callout.Text size="1">
        {t('aiAccounts.codingEngine.isolation.required')}{' '}
        <Link href={GVISOR_INSTALL_GUIDE} target="_blank" rel="noopener noreferrer">
          {t('aiAccounts.codingEngine.isolation.guide')}
        </Link>
      </Callout.Text>
    </Callout.Root>
  );
}
