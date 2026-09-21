'use client';

import { toast } from '@/lib/store/toast-store';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';

/**
 * Warn that the workspace has no AI model. The settings page is admin-only,
 * so members are told who can fix it instead of getting a button they can't use.
 */
export function showNoModelToast(): void {
  // Unknown (profile still loading) counts as a member: never offer a page they may not open.
  if (selectIsAdmin(useUserStore.getState()) !== true) {
    toast.warning('No AI model configured', {
      description:
        'This workspace has no AI model set up yet, so chat can\'t answer. Ask a workspace admin to add one in Workspace → AI Models.',
      duration: null,
    });
    return;
  }
  toast.warning('No AI model configured', {
    description:
      'This workspace has no AI model set up yet, so chat can\'t answer. Add one in AI Models, then send your message again.',
    action: { label: 'Open AI Models', href: '/workspace/ai-models' },
    duration: null,
  });
}
