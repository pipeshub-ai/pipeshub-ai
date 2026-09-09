'use client';

import { create } from 'zustand';
import { toast } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { FeedbackApi } from './api';

function showSmtpNotConfiguredToast() {
  const isAdmin = selectIsAdmin(useUserStore.getState());
  if (isAdmin) {
    toast.warning('SMTP is not configured', {
      description: 'Configure SMTP first, then send feedback.',
      action: { label: 'Configure SMTP', href: '/workspace/mail/' },
    });
    return;
  }
  toast.warning('SMTP is not configured', {
    description: 'Ask your admin to configure SMTP first, then try again.',
  });
}

interface FeedbackDialogState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
}

let smtpCheckInFlight = false;

export const useFeedbackDialogStore = create<FeedbackDialogState>((set) => ({
  isOpen: false,
  open: () => {
    if (smtpCheckInFlight) return;
    smtpCheckInFlight = true;
    void FeedbackApi.getSmtpStatus()
      .then(({ configured }) => {
        if (!configured) {
          showSmtpNotConfiguredToast();
          return;
        }
        set({ isOpen: true });
      })
      .catch(() => {
        showSmtpNotConfiguredToast();
      })
      .finally(() => {
        smtpCheckInFlight = false;
      });
  },
  close: () => set({ isOpen: false }),
}));
