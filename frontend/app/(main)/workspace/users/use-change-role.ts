'use client';

import { useCallback } from 'react';
import type { TFunction } from 'i18next';
import { isProcessedError } from '@/lib/api';
import { ProfileApi } from '../profile/api';
import { USER_ROLES, isMaxOrgAdminsErrorMessage } from '../constants';
import type { User } from './types';

type AddToast = (toast: {
  variant: 'success' | 'error' | 'info';
  title: string;
  description?: string;
  duration?: number;
  action?: { label: string; onClick?: () => void };
}) => unknown;

const apiRole = (role: string) => (role === USER_ROLES.ADMIN ? 'admin' : 'member');

/**
 * Changes a person's workspace role. The success toast carries Undo, which puts
 * the previous role back through the same call, so a slip is one click to take
 * back. Rejects after showing its own error toast, so a caller that closes a
 * dialog on success can tell.
 */
export function useChangeRole({
  addToast,
  t,
  onChanged,
}: {
  addToast: AddToast;
  t: TFunction;
  /** Called after the role changed, or changed back, to refresh the list. */
  onChanged: () => void;
}) {
  return useCallback(
    async (user: User, newRole: string) => {
      const currentRole = user.role || USER_ROLES.MEMBER;
      if (newRole === currentRole) return;

      try {
        await ProfileApi.updateUser(
          user.userId,
          { role: apiRole(newRole) },
          { suppressErrorToast: true },
        );

        const displayName = user.name || user.email;
        addToast({
          variant: 'success',
          title: t('workspace.users.actions.changeRoleSuccess', 'Role updated'),
          description: t('workspace.users.actions.changeRoleSuccessDescription', {
            name: displayName,
            role: newRole,
            defaultValue: `${displayName} is now ${newRole}`,
          }),
          action: {
            label: t('workspace.users.actions.changeRoleUndo', 'Undo'),
            onClick: () => {
              void (async () => {
                try {
                  await ProfileApi.updateUser(
                    user.userId,
                    { role: apiRole(currentRole) },
                    { suppressErrorToast: true },
                  );
                  addToast({
                    variant: 'success',
                    title: t('workspace.users.actions.changeRoleRestored', 'Role restored'),
                    description: t('workspace.users.actions.changeRoleRestoredDescription', {
                      name: displayName,
                      role: currentRole,
                      defaultValue: `${displayName} is ${currentRole} again`,
                    }),
                    duration: 3000,
                  });
                } catch {
                  addToast({
                    variant: 'error',
                    title: t('workspace.users.actions.changeRoleUndoError', 'Could not restore the role'),
                    description: t('workspace.users.actions.changeRoleUndoErrorDescription', {
                      name: displayName,
                      role: newRole,
                      defaultValue: `${displayName} is still ${newRole}. Change the role again to put it back.`,
                    }),
                    duration: 5000,
                  });
                }
                onChanged();
              })();
            },
          },
          duration: 6000,
        });

        onChanged();
      } catch (err: unknown) {
        const apiMessage = isProcessedError(err) ? err.message : undefined;
        if (isMaxOrgAdminsErrorMessage(apiMessage)) {
          addToast({
            variant: 'error',
            title: t('workspace.users.actions.maxAdminsReachedTitle', 'Cannot add another admin'),
            description: t(
              'workspace.users.actions.maxAdminsReached',
              'An organization can have at most 5 admins.',
            ),
            duration: 5000,
          });
        } else {
          addToast({
            variant: 'error',
            title: t('workspace.users.actions.changeRoleError', 'Failed to change role'),
            ...(apiMessage ? { description: apiMessage } : {}),
            duration: 5000,
          });
        }
        throw err;
      }
    },
    [addToast, t, onChanged],
  );
}
