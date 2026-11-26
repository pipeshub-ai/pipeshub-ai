'use client';

import { useCallback, useEffect, useRef } from 'react';
import { Box, Button, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { toast } from '@/lib/store/toast-store';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';
import { sectionOf } from '@/lib/harness-jobs/jobs';
import { openJob, useActivityPanelStore } from '../activity-panel/activity-panel-store';
import { BACKGROUND_AGENTS_PANEL_ID, focusBackgroundAgentsButton, useBackgroundAgentsStore } from './background-agents-store';
import { BackgroundAgentsList } from './background-agents-list';
import { RequestJobsView } from './request-view';
import { useChatJobs } from './use-chat-jobs';

/** The same width as the activity panel beside the chat, so the right side does not jump between the two. */
const PANE_WIDTH = 'clamp(320px, 34vw, 480px)';

/** True while the list is open for the conversation on screen. */
export function useBackgroundAgentsOpen(conversationId: string | null): boolean {
  return useBackgroundAgentsStore((s) => conversationId !== null && s.openFor === conversationId);
}

/**
 * The chat's Background agents list, beside the chat (desktop) or in a bottom sheet (phone). The right side holds
 * one panel at a time: opening a job hands it to the job's panel through the chat's one way in (`openJob`, the URL
 * follows), and the activity panel opening from anywhere else closes the list.
 */
export function BackgroundAgentsHost({ isMobile }: { isMobile: boolean }) {
  const { t } = useTranslation();
  const { jobs, asks, session, owner } = useChatJobs(true);
  const request = useBackgroundAgentsStore((s) => s.request);
  const close = useBackgroundAgentsStore((s) => s.close);
  const back = useBackgroundAgentsStore((s) => s.back);
  const openRequest = useBackgroundAgentsStore((s) => s.openRequest);
  const clear = useJobsStore((s) => s.clear);
  const unclear = useJobsStore((s) => s.unclear);
  const cleared = useJobsStore((s) => s.cleared);
  const paneRef = useRef<HTMLDivElement>(null);

  useEffect(
    () =>
      useActivityPanelStore.subscribe((state, prev) => {
        const opened = (state.open && !prev.open) || (state.sheetOpen && !prev.sheetOpen) || (state.run !== prev.run && state.run !== null);
        if (opened && (state.open || state.sheetOpen)) useBackgroundAgentsStore.getState().close();
      }),
    [],
  );

  // Without preventScroll the focus scrolls the chat's row sideways to reach the pane while it slides in, and the chat
  // jumps under the sidebar for the length of the slide.
  useEffect(() => {
    if (!isMobile) paneRef.current?.focus({ preventScroll: true });
  }, [isMobile, request]);

  const dismiss = useCallback(() => {
    close();
    focusBackgroundAgentsButton();
  }, [close]);

  // A row's Stop goes once its job ends, and the row moves to Finished: the focus would fall to the page, where Escape
  // no longer closes the list. It stays in the list instead.
  useEffect(() => {
    const pane = paneRef.current;
    if (isMobile || !pane) return undefined;
    // A removed element takes the focus without a focusout, so the list's own changes are watched instead.
    let inside = pane.contains(document.activeElement);
    const onFocusIn = () => {
      inside = true;
    };
    const onFocusOut = (e: FocusEvent) => {
      if (e.relatedTarget instanceof Node && !pane.contains(e.relatedTarget)) inside = false;
    };
    // A click elsewhere leaves the list even when what was clicked takes no focus.
    const onPointerDown = (e: PointerEvent) => {
      if (!(e.target instanceof Node) || !pane.contains(e.target)) inside = false;
    };
    const watch = new MutationObserver(() => {
      if (inside && (document.activeElement === document.body || document.activeElement === null)) pane.focus({ preventScroll: true });
    });
    pane.addEventListener('focusin', onFocusIn);
    pane.addEventListener('focusout', onFocusOut);
    document.addEventListener('pointerdown', onPointerDown, true);
    watch.observe(pane, { childList: true, subtree: true });
    return () => {
      watch.disconnect();
      pane.removeEventListener('focusin', onFocusIn);
      pane.removeEventListener('focusout', onFocusOut);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [isMobile, session]);

  useEffect(() => {
    if (isMobile) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && paneRef.current?.contains(document.activeElement)) dismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isMobile, dismiss]);

  if (!session) return null;

  const onOpenJob = (runId: string) => {
    close();
    openJob(runId);
  };

  const finished = jobs.filter((j) => sectionOf(j.status) === 'finished' && !cleared.includes(j.runId)).map((j) => j.runId);
  const clearFinished = () => {
    if (!finished.length) return;
    clear(finished);
    toast.success(t('backgroundAgents.cleared', { count: finished.length }), {
      description: t('backgroundAgents.clearedBody'),
      action: { label: t('backgroundAgents.undo'), onClick: () => unclear(finished) },
    });
  };

  const body = request ? (
    <RequestJobsView session={session} requestRunId={request} jobs={jobs} owner={owner} isMobile={isMobile} onOpenJob={onOpenJob} />
  ) : (
    <BackgroundAgentsList jobs={jobs} asks={asks} session={session} owner={owner} isMobile={isMobile} onOpenJob={onOpenJob} onOpenRequest={openRequest} />
  );

  if (isMobile) {
    return (
      <MobileBottomSheet open onOpenChange={(open) => !open && close()} title={t('backgroundAgents.title')} {...(request ? { onBack: back } : {})}>
        <Box id={BACKGROUND_AGENTS_PANEL_ID} data-testid="background-agents-panel" style={{ maxHeight: '70dvh', overflowY: 'auto', padding: 'var(--space-3) var(--space-3) var(--space-5)' }}>
          {body}
          {!request && finished.length > 0 ? (
            <Flex justify="center" style={{ marginTop: 'var(--space-4)' }}>
              <Button size="2" variant="ghost" color="gray" onClick={clearFinished} style={{ margin: 0, minHeight: 44, gap: 'var(--space-2)' }}>
                <MaterialIcon name="delete" size={18} color="var(--gray-11)" />
                {t('backgroundAgents.clearFinished')}
              </Button>
            </Flex>
          ) : null}
        </Box>
      </MobileBottomSheet>
    );
  }

  return (
    <Flex
      ref={paneRef}
      id={BACKGROUND_AGENTS_PANEL_ID}
      role="region"
      aria-label={t('backgroundAgents.title')}
      tabIndex={-1}
      data-testid="background-agents-panel"
      direction="column"
      style={{
        width: PANE_WIDTH,
        flexShrink: 0,
        height: '100%',
        borderLeft: '1px solid var(--olive-3)',
        backgroundColor: 'var(--color-panel-solid)',
        animation: 'slideInFromRight 0.28s cubic-bezier(0.4, 0, 0.2, 1)',
        outline: 'none',
      }}
    >
      <Flex align="center" gap="1" style={{ height: 48, flexShrink: 0, padding: '0 var(--space-2) 0 var(--space-3)', borderBottom: '1px solid var(--olive-3)' }}>
        {request ? (
          <Tooltip content={t('backgroundAgents.back')}>
            <IconButton size="1" variant="ghost" color="gray" aria-label={t('backgroundAgents.back')} onClick={back} style={{ margin: 0, width: 28, height: 28 }}>
              <MaterialIcon name="arrow_back" size={16} color="var(--gray-11)" />
            </IconButton>
          </Tooltip>
        ) : null}
        <Text as="div" size="2" weight="medium" style={{ flex: 1, color: 'var(--gray-12)', paddingLeft: request ? 'var(--space-1)' : 0 }}>
          {t('backgroundAgents.title')}
        </Text>
        {!request && finished.length > 0 ? (
          <Tooltip content={t('backgroundAgents.clearFinished')}>
            <IconButton size="1" variant="ghost" color="gray" aria-label={t('backgroundAgents.clearFinished')} onClick={clearFinished} style={{ margin: 0, width: 28, height: 28 }}>
              <MaterialIcon name="delete" size={16} color="var(--gray-11)" />
            </IconButton>
          </Tooltip>
        ) : null}
        <Tooltip content={t('backgroundAgents.close')}>
          <IconButton size="1" variant="ghost" color="gray" aria-label={t('backgroundAgents.close')} onClick={dismiss} style={{ margin: 0, width: 28, height: 28 }}>
            <MaterialIcon name="close" size={16} color="var(--gray-11)" />
          </IconButton>
        </Tooltip>
      </Flex>
      <Box className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 'var(--space-4) var(--space-3)' }}>
        {body}
      </Box>
    </Flex>
  );
}
