'use client';

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { useDemoDataStore } from './store';

const UNDO_WINDOW_MS = 10_000;

/** Show or hide the demo for this person, with a toast when it cannot be saved. */
export function useDemoSwitch() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const setIncludeInStore = useDemoDataStore((s) => s.setInclude);
  const [busy, setBusy] = useState(false);

  const setInclude = useCallback(
    async (include: boolean | null): Promise<boolean> => {
      setBusy(true);
      try {
        await setIncludeInStore(include);
        return true;
      } catch {
        addToast({ variant: 'error', title: t('demoData.switch.errorTitle'), description: t('demoData.switch.errorDescription') });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [addToast, setIncludeInStore, t],
  );

  /**
   * Hide it from the chat landing. Nothing stays behind on the page, so the
   * toast says where to turn it back on, and Undo restores what was there.
   */
  const hideWithUndo = useCallback(async () => {
    const previous = useDemoDataStore.getState().status?.chosen ?? null;
    if (!(await setInclude(false))) return;
    addToast({
      variant: 'info',
      title: t('demoData.switch.hiddenTitle'),
      description: t('demoData.switch.hiddenDescription'),
      action: { label: t('demoData.switch.undo'), onClick: () => void setInclude(previous) },
      duration: UNDO_WINDOW_MS,
    });
  }, [addToast, setInclude, t]);

  return { setInclude, hideWithUndo, busy };
}
