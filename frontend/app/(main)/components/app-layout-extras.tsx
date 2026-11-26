'use client';

import type { ReactNode } from 'react';
import { SendFeedbackDialog } from '@/app/(main)/feedback/components/send-feedback-dialog';
import { GetSupportDialog } from '@/app/(main)/support/components/get-support-dialog';

/** Extra elements mounted once in the app shell: the Send feedback and Contact support dialogs. Editions plug in via config.ts. */
export function AppLayoutExtras(): ReactNode {
  return (
    <>
      <SendFeedbackDialog />
      <GetSupportDialog />
    </>
  );
}
