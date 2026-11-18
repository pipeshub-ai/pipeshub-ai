'use client';

import type { ReactNode } from 'react';
import type { RowAction } from '../../components/entity-row-action-menu';
import type { User } from '../types';

export interface UserRowExtras {
  /** More items for an active member's row menu, shown below View Profile. */
  memberActions: (user: User) => RowAction[];
  /** What those items open, rendered once by the page. */
  element: ReactNode;
}

const NONE: UserRowExtras = { memberActions: () => [], element: null };

/** This edition adds nothing to a person's row menu. */
export function useUserRowExtras(): UserRowExtras {
  return NONE;
}
