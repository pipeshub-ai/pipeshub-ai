'use client';

import { create } from 'zustand';
import { toast } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { i18n } from '@/lib/i18n';
import { FeedbackApi } from './api';

function showSmtpNotConfiguredToast() {
  const isAdmin = selectIsAdmin(useUserStore.getState());
  if (isAdmin) {
    toast.warning(i18n.t('feedback.smtpNotConfigured'), {
      description: i18n.t('feedback.smtpNotConfiguredAdmin'),
      action: { label: i18n.t('feedback.smtpConfigureAction'), href: '/workspace/mail/' },
    });
    return;
  }
  toast.warning(i18n.t('feedback.smtpNotConfigured'), {
    description: i18n.t('feedback.smtpNotConfiguredMember'),
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
