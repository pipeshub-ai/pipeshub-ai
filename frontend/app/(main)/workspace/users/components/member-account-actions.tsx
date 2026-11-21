'use client';

import type React from 'react';
import type { RowAction } from '../../components/entity-row-action-menu';
import type { User } from '../types';

/** Row actions an edition adds to a member on the Users page, and the dialog they open. Editions plug in via config.ts. */
export function useMemberAccountActions(_isAdmin: boolean): {
  action: (user: User) => RowAction | false;
  dialog: React.ReactNode;
} {
  return { action: NO_ACTION, dialog: null };
}

const NO_ACTION = (): false => false;
