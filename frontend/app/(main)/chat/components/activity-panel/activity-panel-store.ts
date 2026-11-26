'use client';

import { create } from 'zustand';
import type { DurableAgentRunHandle } from '../../types';
import { useChatStore } from '../../store';

export type ActivitySection = 'plan' | 'commands' | 'tools' | 'files' | 'brief';

/**
 * A job panel's tabs as the URL names them (`&section=`, D5; BGUI-0: the design's tabs, one view at a time), in the
 * order the tab row draws them. BGUI-11: Outputs, what it made, sits after Activity, how it made it, and before
 * Machine, the deepest look, so the tabs read from the person's question to the job's internals.
 */
export type JobPanelSection = 'brief' | 'activity' | 'outputs' | 'machine';
export const JOB_PANEL_SECTIONS: ReadonlyArray<JobPanelSection> = ['brief', 'activity', 'outputs', 'machine'];

/** Where each tab takes focus: its own tab in the tab row, named by the section it shows. */
export const SECTION_OF_JOB: Record<JobPanelSection, ActivitySection> = {
  brief: 'brief',
  activity: 'tools',
  outputs: 'files',
  machine: 'commands',
};

/** Links written before today's tabs: Terminal is on Machine; Files (the job's files) is Outputs (BGUI-11). */
const LEGACY_SECTIONS: Readonly<Record<string, JobPanelSection>> = { terminal: 'machine', files: 'outputs' };

export const isJobPanelSection = (value: unknown): value is JobPanelSection =>
  typeof value === 'string' && (JOB_PANEL_SECTIONS as ReadonlyArray<string>).includes(value);

/** The tab a URL's `section` names, an older link's section included; null for one it does not know. */
export function jobPanelSectionOf(value: unknown): JobPanelSection | null {
  if (isJobPanelSection(value)) return value;
  return typeof value === 'string' ? (LEGACY_SECTIONS[value] ?? null) : null;
}

/** A run the panel shows; `job`: a background job (a child run), shown with its header, Brief and Outputs. */
export type PanelRun = DurableAgentRunHandle & { job?: true };

export const ACTIVITY_PANEL_ID = 'chat-activity-panel';

/** The id of the "View activity" control of one answer's run, where focus returns when the drawer closes. */
export const viewActivityId = (runId: string): string => `chat-view-activity-${runId}`;

/** The id a job card gives its Details button, where focus returns when the job's panel closes. */
export const jobDetailsId = (runId: string): string => `chat-job-details-${runId}`;

interface ActivityPanelState {
  /** Desktop: open only while the person keeps it open; never remembered (CTL-3: detail opens on click only). */
  open: boolean;
  /** Phone: a bottom sheet is modal, so it opens only when asked. */
  sheetOpen: boolean;
  /** The run an answer's "View activity" opened; null: the conversation's latest run. */
  run: PanelRun | null;
  /** Where focus goes once the panel shows: the panel itself or a section, e.g. Files from a chip. */
  focusSection: ActivitySection | 'panel' | null;
  /** The job section the URL names; null: none asked, the panel picks by the job's state. */
  jobSection: JobPanelSection | null;
  /** Desktop: the job panel widened (design: 640 px, at most 42% of the window); kept while the page lives. */
  wide: boolean;
  setOpen: (open: boolean, mobile: boolean) => void;
  /** `section`: where focus goes, e.g. Commands for `/logs`; the panel itself by default. */
  openRun: (run: DurableAgentRunHandle, mobile: boolean, section?: ActivitySection) => void;
  openSection: (section: ActivitySection | 'panel', mobile: boolean) => void;
  sectionFocused: () => void;
  /**
   * A job's panel, for both layouts at once: a job opens from a card, a link or a reload, where the caller does not
   * know which layout shows; closing clears both.
   */
  showJob: (session: { agentId?: string | null; sessionId: string }, runId: string, section?: JobPanelSection | null) => void;
  /** A tab was chosen: the URL follows, focus moves to its tab. */
  jumpTo: (section: JobPanelSection) => void;
  setWide: (wide: boolean) => void;
  closeJob: () => void;
}

export const useActivityPanelStore = create<ActivityPanelState>((set) => ({
  open: false,
  sheetOpen: false,
  run: null,
  focusSection: null,
  jobSection: null,
  wide: false,
  setOpen: (open, mobile) => set(mobile ? { sheetOpen: open } : { open }),
  openRun: (run, mobile, section) =>
    set(
      mobile
        ? { sheetOpen: true, run, focusSection: section ?? 'panel' }
        : { open: true, run, focusSection: section ?? 'panel' },
    ),
  openSection: (section, mobile) =>
    set(mobile ? { sheetOpen: true, run: null, focusSection: section } : { open: true, run: null, focusSection: section }),
  sectionFocused: () => set({ focusSection: null }),
  showJob: (session, runId, section) =>
    set({
      open: true,
      sheetOpen: true,
      run: { ...(session.agentId ? { agentId: session.agentId } : {}), sessionId: session.sessionId, runId, job: true },
      jobSection: section ?? null,
      focusSection: section ? SECTION_OF_JOB[section] : 'panel',
    }),
  jumpTo: (section) => set({ jobSection: section, focusSection: SECTION_OF_JOB[section] }),
  setWide: (wide) => set({ wide }),
  closeJob: () => set({ open: false, sheetOpen: false, run: null, jobSection: null, focusSection: null }),
}));

/** The job whose panel is open, with the section the URL should name; null when no job panel is open. */
export function openJobOf(state: Pick<ActivityPanelState, 'open' | 'sheetOpen' | 'run' | 'jobSection'>): {
  runId: string;
  sessionId: string;
  section: JobPanelSection | null;
} | null {
  if (!state.run?.job || !(state.open || state.sheetOpen)) return null;
  return { runId: state.run.runId, sessionId: state.run.sessionId, section: state.jobSection };
}

/**
 * Open one job of the chat on screen in the details panel (D4): CJ-2's Details button and CJ-4's rows call it. The
 * URL follows (`&job=…[&section=…]`, D5). False when no saved conversation is on screen to hold the job.
 */
export function openJob(runId: string, section?: JobPanelSection): boolean {
  const chat = useChatStore.getState();
  const slot = chat.activeSlotId ? chat.slots[chat.activeSlotId] : undefined;
  if (!slot?.convId || !runId) return false;
  useActivityPanelStore.getState().showJob({ agentId: slot.threadAgentId, sessionId: slot.convId }, runId, section ?? null);
  return true;
}

/** Focus goes back to the control that opened the drawer when it closes (FE-6). */
export function focusViewActivity(runId: string): void {
  if (typeof document === 'undefined') return;
  document.getElementById(viewActivityId(runId))?.focus();
}

/** Focus goes back to the job card's Details button when the job's panel closes. */
export function focusJobDetails(runId: string): void {
  if (typeof document === 'undefined') return;
  document.getElementById(jobDetailsId(runId))?.focus();
}
