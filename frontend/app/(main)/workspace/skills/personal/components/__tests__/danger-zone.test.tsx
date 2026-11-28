import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { SkillUsage } from '../../types';

const api = vi.hoisted(() => ({
  getUsage: vi.fn(),
  deleteSkill: vi.fn(),
  deprecateSkill: vi.fn(),
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('../../api', () => ({ SkillsApi: api, isSkillConflictError: () => false }));
vi.mock('../../store', () => ({ useSkillsStore: () => ({ skills: [] }) }));

import { DangerZone } from '../skill-editor-panel';

// The delete panel decides on what the usage route reports: agents and skills the user may
// not see arrive only as counts, and a counted one must detach or refuse exactly as a named one.

const NAME = 'my-skill';

function usage(overrides: Partial<SkillUsage>): SkillUsage {
  return { usedByAgents: [], requiredBySkills: [], otherAgentCount: 0, otherSkillCount: 0, ...overrides };
}

async function openDelete(u: SkillUsage) {
  api.getUsage.mockResolvedValue(u);
  const onDone = vi.fn();
  renderInTheme(<DangerZone skillName={NAME} onDone={onDone} nestedHost={null} />);
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(api.getUsage).toHaveBeenCalledWith(NAME));
  return onDone;
}

async function confirmDelete() {
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: NAME } });
  const buttons = screen.getAllByRole('button', { name: 'Delete' });
  fireEvent.click(buttons[buttons.length - 1]);
}

beforeEach(() => {
  installBrowserShims();
  api.getUsage.mockReset();
  api.deleteSkill.mockReset().mockResolvedValue(undefined);
});
afterEach(cleanup);

describe('DangerZone delete', () => {
  it('detaches when the skill is assigned only to agents the user cannot open', async () => {
    const onDone = await openDelete(usage({ otherAgentCount: 2 }));
    expect(await screen.findByText(/This skill is assigned to: 2 you can't see\./)).toBeTruthy();
    await confirmDelete();
    await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith(NAME, true));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('names the agents the user can open and counts the rest', async () => {
    await openDelete(usage({ usedByAgents: [{ id: 'ag-1', name: 'Payroll' }], otherAgentCount: 1 }));
    expect(await screen.findByText(/This skill is assigned to: Payroll, 1 you can't see\./)).toBeTruthy();
    await confirmDelete();
    await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith(NAME, true));
  });

  it('deletes without detach when nothing uses the skill', async () => {
    await openDelete(usage({}));
    await confirmDelete();
    await waitFor(() => expect(api.deleteSkill).toHaveBeenCalledWith(NAME, false));
  });

  it('refuses when only skills the user cannot see require it', async () => {
    await openDelete(usage({ otherSkillCount: 1 }));
    expect(await screen.findByText('Cannot delete this skill')).toBeTruthy();
    expect(screen.getByText(/required by other skill\(s\): 1 you can't see\./)).toBeTruthy();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(api.deleteSkill).not.toHaveBeenCalled();
  });
});
