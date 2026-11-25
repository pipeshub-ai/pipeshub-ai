import { engineSteps } from '@/chat/engine-steps';
import type { SSEEngineProgressEvent } from '@/chat/types';
import type { JobView } from './jobs';
import type { StepKind } from './view-models';
import type { Words } from './words';

/**
 * A job's Activity and Machine tabs (design.md §6B.42, "An open agent"): the trail's rail again, one row a step in
 * plain words with the command under it and its output one click down; asks as stamps ("Approved by you: ..."),
 * never buttons, because the ask itself lives only in the card over the chat; how it ended, quoted. Machine is its
 * terminal: the commands it ran and what they printed, in order (D5: no screen yet).
 */

export type ActivityRow =
  | {
      kind: 'step';
      id: string;
      icon: string;
      title: string;
      /** The command it ran (`$ …`), the file it wrote or the page it opened. */
      under?: string;
      output?: string;
      /** How a test run came out, under its command. */
      result?: string;
      error?: string;
      active: boolean;
    }
  /** An ask, in the trail's words: "Waiting for you: …" while it waits, then its stamp ("Approved by you: …"). */
  | { kind: 'ask'; id: string; text: string; meta?: string; live: boolean; stamp: boolean }
  | { kind: 'ended'; id: string; icon: string; label: string; text: string; danger: boolean };

const STEP_ICON: Record<StepKind, string> = {
  boot: 'deployed_code',
  command: 'terminal',
  file: 'draft',
  check: 'fact_check',
  browser: 'web',
  review: 'reply',
  signin: 'login',
  approval: 'how_to_reg',
  handoff: 'move_to_inbox',
  message: 'forward_to_inbox',
  fail: 'error',
  retry: 'schedule',
  restart: 'restart_alt',
};

export function activityRows(job: JobView, t: Words): ActivityRow[] {
  const { worker, state, status } = job;
  const live = status === 'working' || status === 'needsYou';
  const rows: ActivityRow[] = worker.steps.map((step, i) => {
    const active = live && i === state.done;
    if (step.kind === 'approval') {
      const answered = Boolean(state.answers[step.id]);
      return {
        kind: 'ask',
        id: step.id,
        text: answered ? step.stamp ?? t('ui.trail.stamp.closed', { action: step.title }) : t('ui.activity.waitingFor', { question: step.title }),
        ...(answered ? {} : { meta: t('ui.activity.answerInCard') }),
        live: active && !answered,
        stamp: answered,
      };
    }
    return {
      kind: 'step',
      id: step.id,
      icon: STEP_ICON[step.kind],
      title: active ? step.activeTitle ?? step.title : step.title,
      ...(step.command ? { under: `$ ${step.command}` } : step.url ? { under: step.url } : step.file && step.kind !== 'file' ? { under: step.file } : {}),
      ...(step.output && !active ? { output: step.output } : {}),
      ...(step.result && !active ? { result: step.result } : {}),
      ...(step.error ? { error: step.error } : {}),
      active,
    };
  });
  if (status === 'stopped') {
    rows.push({ kind: 'ended', id: 'stopped', icon: 'stop_circle', label: job.stoppedBy ?? t('ui.activity.youStopped'), text: t('ui.activity.stoppedText'), danger: false });
  } else if (status === 'couldntFinish') {
    rows.push({ kind: 'ended', id: 'failed', icon: 'error', label: t('ui.activity.couldntGoOn'), text: t('ui.activity.failedText'), danger: true });
  }
  return rows;
}

/** The files it changed, newest state of each, for the list under its steps. */
export function changedFiles(job: JobView): string[] {
  return job.worker.steps.filter((s) => s.kind === 'file' && s.file).map((s) => s.file as string);
}

export interface MachineLine {
  kind: 'command' | 'output' | 'failed';
  text: string;
}

/** Its terminal, from the frames the stream carried: each command, then what it printed. */
export function machineLines(frames: ReadonlyArray<SSEEngineProgressEvent>, ended: boolean, t: Words): MachineLine[] {
  const lines: MachineLine[] = [];
  for (const step of engineSteps([...frames], ended)) {
    if (step.kind !== 'command') continue;
    lines.push({ kind: 'command', text: `$ ${step.command}` });
    if (step.output) lines.push({ kind: 'output', text: step.output.replace(/\n+$/, '') });
    if (step.status === 'failed') lines.push({ kind: 'failed', text: t('ui.job.commandFailed', { code: step.exitCode ?? '' }) });
  }
  return lines;
}
