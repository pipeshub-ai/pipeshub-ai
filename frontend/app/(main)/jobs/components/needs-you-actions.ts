'use client';

import type { TFunction } from 'i18next';
import { SkillsApi } from '@/app/(main)/workspace/skills/personal/api';
import { useSkillsStore } from '@/app/(main)/workspace/skills/personal/store';
import type { InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import { toast } from '@/lib/store/toast-store';
import { dropItem } from '@/lib/harness-jobs/inbox-store';

/**
 * A skill proposal decided in place on Needs you (D9), through the skills service's own routes, the ones its review
 * panel calls, so a decision means the same wherever it is taken. Each says how it went in a toast.
 */

const SKILLS_PAGE = '/workspace/skills/personal/';

const candidateOf = (item: InboxItem): string | null => (item.pointer.type === 'skill_candidate' ? item.pointer.candidateId : null);

export async function approveSkill(item: InboxItem, name: string, t: TFunction): Promise<void> {
  const id = candidateOf(item);
  if (!id) return;
  try {
    await SkillsApi.approveCandidate(id);
    dropItem(item.id);
    toast.success(t('needsYou.skillApproved', { name }));
  } catch {
    toast.error(t('needsYou.skillFailed'));
  }
}

export async function rejectSkill(item: InboxItem, name: string, t: TFunction): Promise<void> {
  const id = candidateOf(item);
  if (!id) return;
  try {
    await SkillsApi.rejectCandidate(id);
    dropItem(item.id);
    toast.success(t('needsYou.skillRejected', { name }));
  } catch {
    toast.error(t('needsYou.skillFailed'));
  }
}

/** Edit: the skills page with the editor open on the proposal, as the review panel's "Edit and approve" opens it. */
export async function editSkill(item: InboxItem, go: (href: string) => void, t: TFunction): Promise<void> {
  const id = candidateOf(item);
  if (!id) return;
  let candidate: Awaited<ReturnType<typeof SkillsApi.getPendingCandidates>>[number] | undefined;
  try {
    candidate = (await SkillsApi.getPendingCandidates()).find((c) => c.candidate_id === id);
  } catch {
    candidate = undefined;
  }
  if (!candidate) {
    toast.info(t('needsYou.skillGone'));
    dropItem(item.id);
    return;
  }
  go(SKILLS_PAGE);
  useSkillsStore.getState().openEditorPanel(null, {
    prefillName: candidate.name,
    prefillDescription: candidate.description ?? '',
    prefillBody: candidate.body ?? '',
    prefillCategory: candidate.category ?? undefined,
    prefillSubcategory: candidate.subcategory ?? undefined,
    prefillTags: candidate.tags ?? [],
    candidateId: candidate.candidate_id,
  });
}
