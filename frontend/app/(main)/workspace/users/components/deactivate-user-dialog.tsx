'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import type { User } from '../types';

const K = 'workspace.users.actions';

export type AccountSwitch = 'deactivate' | 'reactivate';

interface DeactivateUserDialogProps {
  target: User | null;
  action: AccountSwitch;
  isLoading: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * Asks before a member is deactivated or reactivated, saying in plain words what
 * each does (SEC-AZ-08): deactivating stops their work, pauses their schedules
 * and revokes their access tokens; reactivating restarts none of it.
 */
export function DeactivateUserDialog({ target, action, isLoading, onClose, onConfirm }: DeactivateUserDialogProps) {
  const { t } = useTranslation();
  const name = target?.name || target?.email || '';
  const lines =
    action === 'deactivate'
      ? [t(`${K}.deactivateStopsWork`), t(`${K}.deactivatePausesSchedules`), t(`${K}.deactivateRevokesTokens`)]
      : [t(`${K}.reactivateNothingRestarts`), t(`${K}.reactivateNewTokens`)];
  return (
    <ConfirmationDialog
      open={!!target}
      onOpenChange={(open) => {
        if (!open && !isLoading) onClose();
      }}
      title={t(action === 'deactivate' ? `${K}.deactivateTitle` : `${K}.reactivateTitle`, { name })}
      message={
        <Flex direction="column" gap="2">
          {lines.map((line) => (
            <Text key={line} size="2">
              {line}
            </Text>
          ))}
        </Flex>
      }
      confirmLabel={t(action === 'deactivate' ? `${K}.deactivateButton` : `${K}.reactivateButton`)}
      cancelLabel={t(`${K}.cancelButton`)}
      confirmVariant={action === 'deactivate' ? 'danger' : 'primary'}
      isLoading={isLoading}
      onConfirm={onConfirm}
    />
  );
}

/** The status the Users table shows: a deactivated account first, then blocked, active or pending. */
export function userStatus(user: Pick<User, 'isDisabled' | 'isBlocked' | 'hasLoggedIn'>): 'Deactivated' | 'Blocked' | 'Active' | 'Pending' {
  if (user.isDisabled) return 'Deactivated';
  if (user.isBlocked) return 'Blocked';
  return user.hasLoggedIn ? 'Active' : 'Pending';
}
