'use client';

import React, { useCallback } from 'react';
import { Box, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import type { DurableAgentRunHandle } from '../../types';
import { ActivityPanel } from './activity-panel';
import {
  ACTIVITY_PANEL_ID,
  focusJobDetails,
  focusViewActivity,
  useActivityPanelStore,
  viewActivityId,
  type PanelRun,
} from './activity-panel-store';
import { useActivityRun } from './use-activity-run';
import { useChatStore } from '../../store';

/** Desktop width: wide enough for an 80-column terminal line at 12px, narrow enough to keep the answer readable. */
const PANE_WIDTH = 'clamp(320px, 34vw, 480px)';
/** A job's panel widened (design: 640 px, never more than 42% of the window, so the chat keeps a readable column). */
const WIDE_PANE_WIDTH = 'max(clamp(320px, 34vw, 480px), min(640px, 42vw))';

export interface ActivityPanelLayout {
  run: PanelRun | null;
  /** There is a run to show. */
  available: boolean;
  /** The panel is on screen (on desktop, only while no file preview takes the right side). */
  visible: boolean;
}

/**
 * Where the panel stands. On desktop it shares the right side with the file
 * preview, one at a time: a preview opened while the panel is open hides it
 * (the preference is kept, and it comes back when the preview closes), and
 * the toggle closes a preview to show the panel.
 */
/** The run an answer's "View activity" chose, while its conversation is the one on screen. */
function useChosenRun(): PanelRun | null {
  const chosen = useActivityPanelStore((s) => s.run);
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  return chosen && chosen.sessionId === convId ? chosen : null;
}

/** A job asked for (a link, a reload) while its chat is still loading: the panel waits for it, never shows another run. */
function useJobWaiting(): boolean {
  return useActivityPanelStore((s) => s.run?.job === true);
}

export function useActivityPanelLayout(isMobile: boolean, previewShown: boolean): ActivityPanelLayout {
  const latest = useActivityRun();
  const chosen = useChosenRun();
  const jobAsked = useJobWaiting();
  const open = useActivityPanelStore((s) => (isMobile ? s.sheetOpen : s.open));
  const run = chosen ?? (jobAsked ? null : latest);
  const available = run !== null;
  return { run, available, visible: available && open && (isMobile || !previewShown) };
}

/**
 * "View activity" under an answer (CTL-3): opens the drawer on that answer's run, and nothing else ever opens it.
 * It says whether the drawer shows this run.
 */
export function ViewActivityButton({
  run,
  isMobile,
  onClosePreview,
}: {
  run: DurableAgentRunHandle;
  isMobile: boolean;
  onClosePreview?: () => void;
}) {
  const { t } = useTranslation();
  const latest = useActivityRun();
  const chosen = useChosenRun();
  const open = useActivityPanelStore((s) => (isMobile ? s.sheetOpen : s.open));
  const shown = open && (chosen ?? latest)?.runId === run.runId;
  const setOpen = useActivityPanelStore((s) => s.setOpen);
  const openRun = useActivityPanelStore((s) => s.openRun);
  return (
    <Button
      id={viewActivityId(run.runId)}
      variant={shown ? 'soft' : 'ghost'}
      color="gray"
      size="1"
      aria-expanded={shown}
      aria-controls={ACTIVITY_PANEL_ID}
      onClick={() => {
        if (shown) {
          setOpen(false, isMobile);
          return;
        }
        onClosePreview?.();
        openRun(run, isMobile);
      }}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
        <MaterialIcon name="timeline" size={16} color="var(--gray-11)" />
      </span>
      {t('activityPanel.viewActivity')}
    </Button>
  );
}

/** The panel beside the chat (desktop) or in a bottom sheet (phone). */
export function ActivityPanelHost({ run, isMobile }: { run: PanelRun; isMobile: boolean }) {
  const { t } = useTranslation();
  const setOpen = useActivityPanelStore((s) => s.setOpen);
  const closeJob = useActivityPanelStore((s) => s.closeJob);
  const job = run.job === true;
  const wide = useActivityPanelStore((s) => s.wide) && job;
  const close = useCallback(() => {
    if (job) {
      closeJob();
      focusJobDetails(run.runId);
      return;
    }
    setOpen(false, isMobile);
    focusViewActivity(run.runId);
  }, [job, closeJob, setOpen, isMobile, run.runId]);

  if (isMobile) {
    return (
      <MobileBottomSheet
        open
        onOpenChange={(open) => !open && close()}
        title={job ? t('jobPanel.title') : t('activityPanel.title')}
        // A job's tabs and their own scrolling need nearly the whole screen, as the design's drawer takes it.
        maxHeight={job ? '92dvh' : undefined}
      >
        <Box style={{ height: job ? 'calc(92dvh - 88px)' : '52dvh' }}>
          <ActivityPanel run={run} variant="sheet" onClose={close} />
        </Box>
      </MobileBottomSheet>
    );
  }
  return (
    <Box
      style={{
        width: wide ? WIDE_PANE_WIDTH : PANE_WIDTH,
        flexShrink: 0,
        transition: 'width 0.2s ease',
        height: '100%',
        borderLeft: '1px solid var(--olive-3)',
        backgroundColor: 'var(--color-panel-solid)',
        animation: 'slideInFromRight 0.28s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <ActivityPanel run={run} variant="pane" onClose={close} />
    </Box>
  );
}
