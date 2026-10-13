'use client';

import { toast } from '@/lib/store/toast-store';

/**
 * Explain why a send was held back: the previous answer is still being written
 * on the server (a refresh doesn't stop it), and starting a second run on the
 * same thread would collide with it. The typed message stays in the composer.
 */
export function showRemoteRunInProgressToast(): void {
  toast.info('Still answering your last message', {
    description: 'Wait for the answer to finish, then send this again.',
  });
}
