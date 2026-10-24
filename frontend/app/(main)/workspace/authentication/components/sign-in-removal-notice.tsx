'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Callout, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AuthMethodType, SignInRemovalStatus } from '../types';

/** How many people the notice lists before summing up the rest. */
export const PEOPLE_SHOWN = 10;

type MethodName = AuthMethodType | SignInRemovalStatus['removedMethods'][number];

interface SignInRemovalNoticeProps {
  /** What the 1.0 upgrade turned off, and who has no password yet; null renders nothing. */
  status: SignInRemovalStatus | null;
  /** Sign-in methods this workspace has turned on now. */
  enabledMethods: AuthMethodType[];
}

/** Joins names the way the current language writes a list ("A, B and C"). */
function formatList(names: string[], language: string): string {
  try {
    return new Intl.ListFormat(language, { style: 'long', type: 'conjunction' }).format(names);
  } catch {
    return names.join(', ');
  }
}

/**
 * Tells an admin, after upgrading to 1.0, that SAML or OAuth sign-in was turned
 * off, and lists the people who have no password yet so nobody is left unable
 * to sign in. Renders nothing once everyone listed has set a password.
 */
export function SignInRemovalNotice({ status, enabledMethods }: SignInRemovalNoticeProps) {
  const { t, i18n } = useTranslation();
  if (!status || status.removedMethods.length === 0 || status.peopleWithoutPassword.length === 0) {
    return null;
  }

  const methodName = (method: MethodName) =>
    t(`workspace.authentication.signInRemoval.methodNames.${method}`);
  const removed = formatList(status.removedMethods.map(methodName), i18n.language);
  const enabled = formatList(enabledMethods.map(methodName), i18n.language);
  const shown = status.peopleWithoutPassword.slice(0, PEOPLE_SHOWN);
  const hiddenCount = status.peopleWithoutPassword.length - shown.length;

  return (
    <Callout.Root color="amber" variant="surface" style={{ marginBottom: 'var(--space-5)' }}>
      <Callout.Icon>
        <MaterialIcon name="warning_amber" size={16} />
      </Callout.Icon>
      <Flex direction="column" gap="2">
        <Text size="2" weight="medium">
          {t('workspace.authentication.signInRemoval.title', {
            methods: removed,
            count: status.removedMethods.length,
          })}
        </Text>
        <Text size="2">
          {enabledMethods.length > 0 &&
            `${t('workspace.authentication.signInRemoval.nowSignsInWith', { methods: enabled })} `}
          {t('workspace.authentication.signInRemoval.peopleIntro')}
        </Text>
        <ul style={{ margin: 0, paddingLeft: 'var(--space-5)' }}>
          {shown.map((person) => (
            <li key={person.userId}>
              <Text size="2">
                {person.fullName?.trim()
                  ? t('workspace.authentication.signInRemoval.person', {
                      name: person.fullName.trim(),
                      email: person.email,
                    })
                  : person.email}
              </Text>
            </li>
          ))}
          {hiddenCount > 0 && (
            <li>
              <Text size="2">
                {t('workspace.authentication.signInRemoval.andMore', { count: hiddenCount })}
              </Text>
            </li>
          )}
        </ul>
        <Text size="2">{t('workspace.authentication.signInRemoval.howToSetPassword')}</Text>
      </Flex>
    </Callout.Root>
  );
}
