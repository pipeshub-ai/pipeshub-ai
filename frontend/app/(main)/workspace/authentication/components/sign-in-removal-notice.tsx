'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Callout, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AUTH_METHOD_META, type AuthMethodState, type ConfigStatus } from '../types';

/** The release that removes SAML and custom OAuth/OIDC sign-in. */
export const SIGN_IN_REMOVAL_VERSION = '0.11.0';

/** Sign-in methods that SIGN_IN_REMOVAL_VERSION removes. */
export const METHODS_BEING_REMOVED = ['samlSso', 'oauth'] as const;
export type MethodBeingRemoved = (typeof METHODS_BEING_REMOVED)[number];

export function isMethodBeingRemoved(type: string): type is MethodBeingRemoved {
  return (METHODS_BEING_REMOVED as readonly string[]).includes(type);
}

/** The methods being removed that this workspace has turned on or set up. */
export function methodsBeingRemovedInUse(
  methods: AuthMethodState[],
  configStatus: ConfigStatus,
): MethodBeingRemoved[] {
  return METHODS_BEING_REMOVED.filter(
    (type) => configStatus[type] || methods.some((m) => m.type === type && m.enabled),
  );
}

interface SignInRemovalNoticeProps {
  /** Methods being removed that the workspace uses; nothing renders when empty. */
  inUse: MethodBeingRemoved[];
}

/** Tells an admin, before they upgrade, that sign-in methods their workspace uses are going away. */
export function SignInRemovalNotice({ inUse }: SignInRemovalNoticeProps) {
  const { t } = useTranslation();
  if (inUse.length === 0) return null;

  const methods = inUse
    .map((type) => AUTH_METHOD_META.find((meta) => meta.type === type)?.label ?? type)
    .join(t('workspace.authentication.removal.listSeparator'));

  return (
    <Callout.Root color="amber" variant="surface" style={{ marginBottom: 'var(--space-5)' }}>
      <Callout.Icon>
        <MaterialIcon name="warning_amber" size={16} />
      </Callout.Icon>
      <Callout.Text>
        <Text weight="medium" style={{ display: 'block', marginBottom: 'var(--space-1)' }}>
          {t('workspace.authentication.removal.title', { version: SIGN_IN_REMOVAL_VERSION })}
        </Text>
        {t('workspace.authentication.removal.body', { methods, version: SIGN_IN_REMOVAL_VERSION })}
      </Callout.Text>
    </Callout.Root>
  );
}
