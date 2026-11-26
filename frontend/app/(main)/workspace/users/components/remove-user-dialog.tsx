'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import type { TFunction } from 'i18next';
import { fetchOrgWithLogo } from '@/chat/api';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import type { User } from '../types';

const K = 'workspace.users.actions';

/** The org's display name, or null until it loads (or when it cannot). */
export function useOrgName(): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void fetchOrgWithLogo().then(({ org }) => {
      if (live) setName(org?.shortName || org?.registeredName || null);
    });
    return () => {
      live = false;
    };
  }, []);
  return name;
}

/** What the success toast says, naming the org exactly as the dialog did (`workspace` is what it showed). */
export function removedUserDescription(t: TFunction, target: Pick<User, 'name' | 'email'>, workspace: string): string {
  return t(`${K}.removeSuccessDescription`, { name: target.name || target.email, workspace });
}

interface RemoveUserDialogProps {
  target: User | null;
  /** OAuth apps the person created (they stop working on removal); null or 0 says nothing. */
  oauthAppCount?: number | null;
  isLoading: boolean;
  onClose: () => void;
  /** Gets the org as the dialog named it, so what follows (the toast) names it the same way. */
  onConfirm: (workspace: string) => void;
}

/**
 * Asks before a person is removed, naming the org and saying what removal does
 * to their data (CLEANUP-G2): private chats and files are erased, chats shared
 * with others move to an admin.
 */
export function RemoveUserDialog({ target, oauthAppCount, isLoading, onClose, onConfirm }: RemoveUserDialogProps) {
  const { t } = useTranslation();
  const orgName = useOrgName();
  const workspace = orgName ?? t(`${K}.removeUserThisOrg`);
  return (
    <ConfirmationDialog
      open={!!target}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t(`${K}.removeUserTitle`)}
      message={
        <Flex direction="column" gap="2">
          <Text size="2">
            {t(`${K}.removeUserMessage`, {
              email: target?.email || target?.name || '',
              workspace,
            })}
          </Text>
          <Text size="2">{t(`${K}.removeUserErases`)}</Text>
          <Text size="2">{t(`${K}.removeUserSharedChats`)}</Text>
          {oauthAppCount ? (
            <Text size="2">
              {oauthAppCount === 1
                ? t(`${K}.removeUserOAuthAppsOne`)
                : t(`${K}.removeUserOAuthAppsMany`, { count: oauthAppCount })}
            </Text>
          ) : null}
        </Flex>
      }
      confirmLabel={t(`${K}.removeButton`)}
      cancelLabel={t(`${K}.cancelButton`)}
      confirmVariant="danger"
      isLoading={isLoading}
      onConfirm={() => onConfirm(workspace)}
    />
  );
}
