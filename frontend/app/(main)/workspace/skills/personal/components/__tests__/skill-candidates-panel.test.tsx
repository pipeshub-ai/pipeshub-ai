import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const mocks = vi.hoisted(() => ({
  api: { getPendingCandidates: vi.fn(), rejectCandidate: vi.fn(), approveCandidate: vi.fn(), listSkills: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  store: {
    candidatesPanelOpen: true,
    closeCandidatesPanel: vi.fn(),
    candidates: [{ candidate_id: 'c1', name: 'deploy-service', description: 'd', body: 'b' }],
    setCandidates: vi.fn(),
    setSkills: vi.fn(),
    openEditorPanel: vi.fn(),
  },
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/loading-button', () => ({
  LoadingButton: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button onClick={onClick}>{children}</button>
  ),
}));
vi.mock('../../../../components', () => ({
  WorkspaceRightPanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: mocks.toast }));
vi.mock('../../api', async () => ({
  SkillsApi: mocks.api,
  isCandidateGoneError: (await vi.importActual<typeof import('../../api')>('../../api')).isCandidateGoneError,
}));
vi.mock('../../store', () => ({ useSkillsStore: () => mocks.store }));

import { SkillCandidatesPanel } from '../skill-candidates-panel';

beforeEach(() => {
  installBrowserShims();
  mocks.api.getPendingCandidates.mockReset().mockResolvedValue([]);
  mocks.api.rejectCandidate.mockReset();
  mocks.toast.success.mockReset();
  mocks.toast.error.mockReset();
  mocks.toast.info.mockReset();
  mocks.store.setCandidates.mockReset();
});
afterEach(cleanup);

describe('SkillCandidatesPanel reject', () => {
  it('refreshes the list and says the candidate was already reviewed when the server answers 404', async () => {
    mocks.api.rejectCandidate.mockRejectedValue({ response: { status: 404 } });
    renderInTheme(<SkillCandidatesPanel />);
    await waitFor(() => expect(mocks.api.getPendingCandidates).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(mocks.toast.info).toHaveBeenCalledWith('This candidate has already been reviewed'));
    expect(mocks.api.getPendingCandidates).toHaveBeenCalledTimes(2);
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('keeps the error toast for any other failure', async () => {
    mocks.api.rejectCandidate.mockRejectedValue({ response: { status: 500 } });
    renderInTheme(<SkillCandidatesPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('Failed to update candidate'));
    expect(mocks.toast.info).not.toHaveBeenCalled();
    expect(mocks.api.getPendingCandidates).toHaveBeenCalledTimes(1);
  });
});
