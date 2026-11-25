'use client';

import { Button, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget } from '@/app/components/ui/responsive';
import { useActivityPanelStore } from '../activity-panel/activity-panel-store';
import { BACKGROUND_AGENTS_BUTTON_ID, BACKGROUND_AGENTS_PANEL_ID, useBackgroundAgentsStore } from './background-agents-store';
import { openerState } from './chat-jobs';
import { useChatJobs } from './use-chat-jobs';

/** The right side holds one panel at a time: the list takes it from the activity or job panel. */
function closeActivityPanel(): void {
  const panel = useActivityPanelStore.getState();
  if (panel.run?.job) panel.closeJob();
  else if (panel.open || panel.sheetOpen) useActivityPanelStore.setState({ open: false, sheetOpen: false });
}

/**
 * The chat's Background agents button in its top bar (design §6B.43): shown only once the chat has handed work to a
 * background agent. While any still goes it carries their count, amber when one needs you, so its name says the same
 * number in words.
 */
export function BackgroundAgentsButton({ isMobile, onClosePreview }: { isMobile: boolean; onClosePreview?: () => void }) {
  const { t } = useTranslation();
  const { jobs, session } = useChatJobs(false);
  const state = openerState(jobs);
  const conversationId = session?.sessionId ?? null;
  const expanded = useBackgroundAgentsStore((s) => conversationId !== null && s.openFor === conversationId);
  if (!state.shown || !conversationId) return null;

  const count = state.needsYou > 0 ? state.needsYou : state.live;
  const name =
    state.needsYou > 0
      ? t('backgroundAgents.openerNeedsYou', { count: state.needsYou })
      : state.live > 0
        ? t('backgroundAgents.openerRunning', { count: state.live })
        : t('backgroundAgents.title');

  const toggle = () => {
    const store = useBackgroundAgentsStore.getState();
    if (expanded) {
      store.close();
      return;
    }
    closeActivityPanel();
    onClosePreview?.();
    store.open(conversationId);
  };

  return (
    <Tooltip content={t('backgroundAgents.title')}>
      <Button
        id={BACKGROUND_AGENTS_BUTTON_ID}
        data-testid="background-agents-button"
        size="2"
        variant={expanded ? 'soft' : 'ghost'}
        color="gray"
        aria-label={name}
        aria-expanded={expanded}
        aria-controls={BACKGROUND_AGENTS_PANEL_ID}
        onClick={toggle}
        style={{ margin: 0, gap: 'var(--space-1)', paddingInline: 'var(--space-2)', ...touchTarget(isMobile) }}
      >
        <MaterialIcon name="deployed_code" size={18} color="var(--gray-11)" />
        {count > 0 ? (
          <span
            data-testid="background-agents-count"
            data-tone={state.needsYou > 0 ? 'needsYou' : 'live'}
            style={{
              minWidth: 18,
              height: 18,
              padding: '0 5px',
              boxSizing: 'border-box',
              borderRadius: 9,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 'var(--font-size-1)',
              fontWeight: 500,
              fontVariantNumeric: 'tabular-nums',
              color: state.needsYou > 0 ? 'var(--amber-11)' : 'var(--accent-11)',
              background: state.needsYou > 0 ? 'var(--amber-a3)' : 'var(--accent-a3)',
            }}
          >
            {count}
          </span>
        ) : null}
      </Button>
    </Tooltip>
  );
}
