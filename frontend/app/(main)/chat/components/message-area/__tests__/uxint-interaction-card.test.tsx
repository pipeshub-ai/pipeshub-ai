/**
 * UXINT-2/4/5: one card at its step. A batch is one card with Allow all / Review each / Deny; a decided card is one
 * receipt line; a later card waits as one line. The active card takes focus only when the person is not typing,
 * Enter is its primary action and Escape returns to the message box; buttons are 44 px; a paused run says so.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalInteraction } from '../interaction-card';
import type { PendingApproval } from '../../../types';
import type { ApprovalGroup } from '../../../interactions';

const h = React.createElement;
beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

const WONDERS = ['Great Wall of China', 'Petra', 'Christ the Redeemer', 'Machu Picchu', 'Chichén Itzá', 'Colosseum', 'Taj Mahal'];
const image = (i: number, over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: `ix.approve.t7.${i}`,
  toolCallId: `t7.${i}`,
  toolName: 'image_generator__generate_image',
  displayName: 'Image Generator · generate_image',
  args: JSON.stringify({ prompt: `A photorealistic picture of ${WONDERS[i]}`, size: '1024x1024' }),
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: 'run-w',
  afterTurns: 7,
  seq: 200 + i,
  reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false },
  ...over,
});
const batch = (over: (i: number) => Partial<PendingApproval> = () => ({})): ApprovalGroup => ({
  key: 'g',
  items: WONDERS.map((_, i) => image(i, over(i))),
});

function renderGroup(group: ApprovalGroup, state: 'active' | 'queued' | 'done' = 'active', paused = false) {
  const onDecide = vi.fn();
  const onDecideAll = vi.fn();
  const view = render(
    h(Theme, null, h(ApprovalInteraction, { group, state, onDecide, onDecideAll, conversationChoice: true, paused })),
  );
  return { ...view, onDecide, onDecideAll };
}

describe('a batch is one card (UXINT-2, D2)', () => {
  it('UXC-1: "Generate 7 images?" lists the seven prompts, no raw JSON', () => {
    renderGroup(batch());
    const card = screen.getByRole('region', { name: 'Generate 7 images?' });
    const items = within(card).getAllByTestId('approval-group-item');
    expect(items.map((li) => li.textContent)).toEqual(WONDERS.map((w) => `A photorealistic picture of ${w}`));
    expect(card.textContent).not.toContain('{');
    expect(card.textContent).toContain('Your organization requires approval');
  });

  it('UXC-2, DECIDE409: one click on "Allow all 7" decides the seven calls in one batch', () => {
    const { onDecide, onDecideAll } = renderGroup(batch());
    fireEvent.click(screen.getByRole('button', { name: 'Allow all 7' }));
    expect(onDecide).not.toHaveBeenCalled();
    expect(onDecideAll).toHaveBeenCalledTimes(1);
    const [items, decision] = onDecideAll.mock.calls[0];
    expect([items.map((item: PendingApproval) => item.interactionId), decision]).toEqual([
      WONDERS.map((_, i) => `ix.approve.t7.${i}`),
      'approved',
    ]);
  });

  it('DECIDE409: "Deny all" leaves out a call already decided', () => {
    const { onDecideAll } = renderGroup(batch((i) => (i === 0 ? { status: 'approved' } : {})));
    fireEvent.click(screen.getByRole('button', { name: 'Deny all' }));
    fireEvent.click(screen.getByTestId('deny-confirm'));
    const [items, decision] = onDecideAll.mock.calls[0];
    expect(items).toHaveLength(6);
    expect(decision).toBe('denied');
  });

  it('UXC-3: "Review each" opens a row per call; denying one decides that one only', () => {
    const { onDecide, rerender } = renderGroup(batch());
    fireEvent.click(screen.getByRole('button', { name: 'Review each' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny: A photorealistic picture of Petra' }));
    expect(onDecide.mock.calls.map(([item, decision]) => [item.interactionId, decision])).toEqual([['ix.approve.t7.1', 'denied']]);
    rerender(h(Theme, null, h(ApprovalInteraction, { group: batch((i) => (i === 1 ? { status: 'denied' } : {})), state: 'active', onDecide, onDecideAll: vi.fn(), conversationChoice: true, paused: false })));
    const petra = screen.getAllByTestId('approval-group-item')[1];
    expect(within(petra).getByTestId('interaction-receipt').textContent).toBe('closeDenied by you · nothing was sent');
  });

  it('UXC-1b: three calls sharing a body are told apart by the field that differs (QAFIX2)', () => {
    const titles = ['qa4 one', 'qa4 two', 'qa4 three'];
    renderGroup({
      key: 'tickets',
      items: titles.map((title, i) => image(i, { toolName: 'e2esaas__create_ticket', args: JSON.stringify({ body: 'qa-fp4', title }) })),
    });
    expect(screen.getAllByTestId('approval-group-item').map((li) => li.textContent)).toEqual(titles);
    fireEvent.click(screen.getByRole('button', { name: 'Review each' }));
    expect(screen.getByRole('button', { name: 'Deny: qa4 two' })).toBeTruthy();
  });

  it('UXC-4: decided, the card is one receipt line where it was', () => {
    renderGroup(batch(() => ({ status: 'approved' })));
    expect(screen.queryByRole('region')).toBeNull();
    expect(screen.getByTestId('interaction-receipt').textContent).toBe('checkGenerate 7 images · Allowed all 7 by you');
    cleanup();
    renderGroup(batch((i) => ({ status: i === 0 ? 'denied' : 'approved' })));
    expect(screen.getByTestId('interaction-receipt').textContent).toBe('checkGenerate 7 images · 6 allowed, 1 not run');
    cleanup();
    renderGroup({ key: 'one', items: [image(0, { status: 'denied' })] });
    expect(screen.getByTestId('interaction-receipt').textContent).toBe('closeGenerate an image · Denied by you · nothing was sent');
    cleanup();
    renderGroup({ key: 'one', items: [image(0, { status: 'approved', scope: 'conversation' })] });
    expect(screen.getByTestId('interaction-receipt').textContent).toBe('checkGenerate an image · Allowed for this conversation by you');
  });

  it('UXC-5: a later card waits as one line, without buttons', () => {
    renderGroup({ key: 'one', items: [image(0, { toolName: 'coding_sandbox__run_code', displayName: undefined })] }, 'queued');
    expect(screen.getByTestId('interaction-queued').textContent).toBe('Next: Run code');
    expect(screen.queryAllByRole('button')).toEqual([]);
  });
});

describe('keyboard, focus and size (UXINT-4, D4)', () => {
  const composer = () => {
    const box = document.createElement('textarea');
    box.className = 'chat-input-textarea';
    document.body.appendChild(box);
    return box;
  };

  it('UXK-1: a new active card takes focus when the message box is empty and not focused', () => {
    composer();
    renderGroup({ key: 'one', items: [image(0)] });
    expect(document.activeElement?.getAttribute('data-testid')).toBe('interaction-heading');
    expect(document.activeElement?.textContent).toBe('Generate an image?');
  });

  it('UXK-2: it never takes focus from someone typing', () => {
    const box = composer();
    box.focus();
    renderGroup({ key: 'one', items: [image(0)] });
    expect(document.activeElement).toBe(box);
    cleanup();
    const filled = composer();
    filled.value = 'half a sentence';
    renderGroup({ key: 'one', items: [image(0)] });
    expect(document.activeElement?.getAttribute('data-testid')).not.toBe('interaction-heading');
  });

  it('UXK-3: Enter allows (all of a batch); Escape goes back to the message box', () => {
    const box = composer();
    const { onDecideAll } = renderGroup(batch());
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter' });
    expect(onDecideAll.mock.calls[0][0]).toHaveLength(7);
    fireEvent.keyDown(screen.getByTestId('interaction-heading'), { key: 'Escape' });
    expect(document.activeElement).toBe(box);
  });

  it('UXK-4: Enter on a button is that button, not the card’s primary action', () => {
    composer();
    const { onDecide } = renderGroup({ key: 'one', items: [image(0)] });
    fireEvent.keyDown(screen.getByRole('button', { name: /^Deny/ }), { key: 'Enter' });
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('UXK-5: the card is a labelled region that says its keys; every button is 44 px', () => {
    renderGroup(batch());
    const card = screen.getByRole('region', { name: 'Generate 7 images?' });
    expect(card.getAttribute('data-interaction-active')).toBe('true');
    const described = (card.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain('Press Enter to allow all 7, or Escape to go back to the message box.');
    for (const button of within(card).getAllByRole('button')) expect(button.style.minHeight).toBe('44px');
  });
});

describe('a paused run (UXINT-5, D5)', () => {
  it('UXP-1: the card says it is paused and stays actionable', () => {
    const { onDecide } = renderGroup({ key: 'one', items: [image(0)] }, 'active', true);
    expect(screen.getByTestId('interaction-paused').textContent).toContain('Paused, waiting for you');
    fireEvent.click(screen.getByRole('button', { name: /^Allow once: / }));
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ interactionId: 'ix.approve.t7.0' }), 'approved', undefined);
  });
});
