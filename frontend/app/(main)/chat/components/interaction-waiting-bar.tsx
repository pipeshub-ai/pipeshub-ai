'use client';

import React, { useEffect, useState } from 'react';
import { Button, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useChatStore } from '../store';
import { groupApprovals, isOpenGroup, questionWaits, slotWaitingCount } from '../interactions';
import { floatsGroup, useFloatedAsks } from './job-ask/job-asks';
import { interactionTitle } from '../interaction-wording';
import { cardWords } from './message-area/grounding-card';

const ACTIVE = '[data-interaction-active="true"]';

/** Each template ends the title's sentence itself: a title's own end mark would read twice ("in?."). */
const withoutEndMark = (title: string): string => title.trim().replace(/[.?!\u3002\uff1f\uff01\u0964]+$/, '');

/** The active card's words for the live region: "Agent needs your approval: Generate 7 images. 2 waiting for you." */
function useAnnouncement(): { key: string | null; text: string; count: number } {
  const { t } = useTranslation();
  const slot = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId] : undefined));
  const count = slotWaitingCount(slot);
  // BGUI-4: the card over the message box says its own asks; this says the transcript's.
  const floated = useFloatedAsks();
  if (!slot || count === 0) return { key: null, text: '', count };
  const [group] = slot.isStreaming
    ? groupApprovals(slot.pendingApprovals ?? []).filter((g) => isOpenGroup(g) && !floatsGroup(g, floated))
    : [];
  if (group) {
    const [first] = group.items;
    const title = withoutEndMark(interactionTitle(t, first, group.items.length));
    return {
      key: `${first.runId ?? ''}:${first.childRunId ?? ''}:${first.interactionId}`,
      text: t('interaction.announce', { title, count }),
      count,
    };
  }
  if (!questionWaits(slot)) return { key: null, text: '', count };
  const payload = slot.pendingAskUserQuestion?.payload;
  const first = payload?.questions[0]?.question;
  const question = withoutEndMark(payload && first !== undefined ? cardWords(payload, t).question(first) : '');
  return {
    key: `question:${slot.pendingAskUserQuestion?.assistantMessageId ?? ''}`,
    text: t('interaction.announceQuestion', { title: question, count }),
    count,
  };
}

/** Whether the active card is on screen; re-checked as cards come and go. */
function useActiveCardVisible(key: string | null): { visible: boolean; above: boolean } {
  const [state, setState] = useState({ visible: true, above: false });
  useEffect(() => {
    if (key === null || typeof IntersectionObserver === 'undefined') {
      setState({ visible: true, above: false });
      return undefined;
    }
    let observer: IntersectionObserver | null = null;
    // The card renders in the same commit as the state that names it: look for it after paint.
    const timer = setTimeout(() => {
      const card = document.querySelector(ACTIVE);
      if (!card) return;
      observer = new IntersectionObserver(
        ([entry]) =>
          setState({ visible: entry?.isIntersecting ?? true, above: (entry?.boundingClientRect.top ?? 0) < 0 }),
        { threshold: 0.15 },
      );
      observer.observe(card);
    }, 50);
    return () => {
      clearTimeout(timer);
      observer?.disconnect();
    };
  }, [key]);
  return state;
}

/**
 * Above the message box (UXINT-2, direction C): a polite live region that says when the agent needs the person,
 * and, only while the active card is scrolled out of view, a small pill that brings it back. No tray: the card
 * itself stays at its step.
 */
export function InteractionWaitingBar() {
  const { t } = useTranslation();
  const { key, text, count } = useAnnouncement();
  const { visible, above } = useActiveCardVisible(key);
  const [said, setSaid] = useState('');

  // Announce each new call to action once; a re-render with the same card says nothing new.
  useEffect(() => {
    setSaid(key === null ? '' : text);
  }, [key]);

  const goToFirst = () => {
    const card = document.querySelector<HTMLElement>(ACTIVE);
    if (!card) return;
    card.scrollIntoView({ block: 'center', behavior: 'smooth' });
    card.querySelector<HTMLElement>('[data-testid="interaction-heading"]')?.focus({ preventScroll: true });
  };

  return (
    <>
      <div aria-live="polite" role="status" data-testid="interaction-announcer" style={HIDDEN}>
        {said}
      </div>
      {key !== null && !visible ? (
        <Flex justify="center" style={{ marginBottom: 'var(--space-2)' }}>
          <Button
            size="2"
            color="amber"
            variant="solid"
            radius="full"
            onClick={goToFirst}
            data-testid="interaction-pill"
            style={{ minHeight: 44 }}
          >
            {t('interaction.pill', { count })}
            <MaterialIcon name={above ? 'arrow_upward' : 'arrow_downward'} size={16} />
          </Button>
        </Flex>
      ) : null}
    </>
  );
}

const HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};
