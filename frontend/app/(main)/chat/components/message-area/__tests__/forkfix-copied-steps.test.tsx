/**
 * FORKFIX: a copied answer's saved steps in a fork. A coding child's row (`sub_agent`) the source saved named the
 * child's run; in the fork, opening it asked `…/runs/{child}/timeline` under the fork and got 404. A fork now copies
 * the row without its run ids: it still reads "Code task: …" with its status, and opening it asks nothing.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { SubAgentGroup } from '../agent-activity';
import { ChatApi } from '../../../api';
import { useChatStore } from '../../../store';
import type { MessagePart } from '../../../types';

const FORK = '6ad9f7501c469c87f85ad15b';
const CHILD = 'd2d96865-7dd0-e6e3-dec9-03a95dfab23e';
const PARENT = 'cd04a9d1-7652-4ad0-af2c-b3d7792a195a';
const initialChat = useChatStore.getState();
const getTimeline = vi.spyOn(ChatApi, 'getRunTimeline');

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  const store = useChatStore.getState();
  const slotId = store.createSlot(FORK);
  store.updateSlot(slotId, { isInitialized: true, threadAgentId: 'agent-1', isOwner: true, durable: true });
  store.setActiveSlot(slotId);
  getTimeline.mockReset();
  getTimeline.mockRejectedValue(new Error('404 Agent run not found'));
});

afterEach(() => cleanup());

const row = (part: MessagePart) => render(<Theme><SubAgentGroup part={part} /></Theme>);
const saved: MessagePart = { type: 'sub_agent', codeTaskGoal: 'run it', status: 'completed', parts: [] };

describe('FORKFIX: a copied coding child row', () => {
  it('reads as it did in the original and opens without asking for a run', () => {
    row(saved);
    const toggle = screen.getByText('Code task: run it');
    expect(screen.queryByTestId('sub-agent-toggle')).toBeNull();
    fireEvent.click(toggle);
    expect(getTimeline).not.toHaveBeenCalled();
  });

  it("before FORKFIX the same row kept the source's run ids and opening it asked the fork for the child's timeline", () => {
    row({ ...saved, runId: CHILD, parentRunId: PARENT });
    fireEvent.click(screen.getByTestId('sub-agent-toggle'));
    expect(getTimeline).toHaveBeenCalledWith(expect.objectContaining({ sessionId: FORK, runId: CHILD }));
  });
});
