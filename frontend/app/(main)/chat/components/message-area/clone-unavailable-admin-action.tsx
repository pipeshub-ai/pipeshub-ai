'use client';

import React, { type ReactNode } from 'react';
import type { UnavailableClone } from './outside-access';

export interface CloneUnavailableAdminActionProps {
  clone: UnavailableClone;
  /** What the card shows an org admin otherwise. */
  children?: ReactNode;
}

/** What the "Clone not available" card offers an org admin. Editions plug in via config.ts; this one adds nothing. */
export function CloneUnavailableAdminAction({ children }: CloneUnavailableAdminActionProps) {
  return <>{children}</>;
}
