import { getBrandConfig } from '../../../libs/branding';
import {
  SANDBOX_ALERT_DOCS_URL,
  SANDBOX_ALERT_TITLES,
  SandboxAlertLevel,
  SandboxCapacityAlert,
  indexingSentence,
  reasonSentence,
} from './sandbox-capacity-alert';
import type {
  SandboxCapacityAlertData,
  SandboxCapacityAlertStat,
} from '../../mail/utils/emailTemplates';

type Level = SandboxAlertLevel;

const SUBJECTS: Record<Level, string> = {
  warning: 'code runs in chat are waiting for capacity',
  critical: 'code runs in chat are being refused',
};

export interface SandboxCapacityMail {
  subject: string;
  templateData: SandboxCapacityAlertData;
}

function actionsFor(
  reason: SandboxCapacityAlert['reason'],
  sidecar: boolean,
): string[] {
  switch (reason) {
    case 'memory':
      return sidecar
        ? [
            "Raise the code runner's memory (sandbox.dind.resources), or lower sandbox.capacity.maxTotal.",
          ]
        : ['Add memory to the host, or move a database to another machine.'];
    case 'cpu':
      return sidecar
        ? ["Raise the code runner's CPU (sandbox.dind.resources)."]
        : ['Add CPU cores to the host.'];
    case 'slots':
      return sidecar
        ? [
            'Raise sandbox.capacity.maxTotal if the code runner has room; otherwise give it more memory.',
          ]
        : [
            'Raise the sandbox limit (SANDBOX_MAX_TOTAL) if the host has room; otherwise add memory.',
          ];
    default:
      return [];
  }
}

function introFor(level: Level, p: SandboxCapacityAlert): string {
  const minutes = p.windowMinutes ?? 30;
  const runs = p.runs ?? 0;
  const slotWait = p.slotWaitSeconds;
  if (level === 'critical') {
    const waitText = slotWait === null ? '' : ` after waiting ${slotWait} s`;
    return (
      `In the last ${minutes} minutes, ${p.refused ?? 0} of ${runs} code runs in chat ` +
      `(${p.refusedPercent ?? 0}%) were refused${waitText} for a free sandbox. ` +
      'Users saw an error instead of a result.'
    );
  }
  const longest = p.longestWaitSeconds;
  const longestText =
    longest === null ? '' : `, and the longest wait was ${longest} s`;
  const refuseText =
    slotWait === null
      ? ''
      : ` Runs that wait longer than ${slotWait} s are refused.`;
  return (
    `For ${p.busyPercent ?? 0}% of the last ${minutes} minutes, code runs in chat waited ` +
    `for a free sandbox. ${p.waited ?? 0} of ${runs} runs waited${longestText}. ` +
    `Users saw "waiting in queue" before their code ran.${refuseText}`
  );
}

function statsFor(
  level: Level,
  p: SandboxCapacityAlert,
  sidecar: boolean,
): SandboxCapacityAlertStat[] {
  const stats: SandboxCapacityAlertStat[] = [];
  if (p.host) stats.push({ label: 'Host', value: p.host });
  stats.push({
    label: 'Window',
    value: `last ${p.windowMinutes ?? 30} minutes`,
  });
  stats.push({ label: 'Runs', value: String(p.runs ?? 0) });
  stats.push({
    label: 'Waited',
    value: `${p.waited ?? 0} (${p.busyPercent ?? 0}% of the time)`,
  });
  const refused = p.refused ?? 0;
  stats.push({
    label: 'Refused',
    value:
      level === 'critical'
        ? `${refused} (${p.refusedPercent ?? 0}%)`
        : String(refused),
  });
  const current = p.limit.current;
  const ceiling = p.limit.ceiling;
  if (current !== null && ceiling !== null) {
    stats.push({
      label: 'Runs allowed at once now / at most',
      value: `${current} / ${ceiling}`,
    });
  }
  const free = p.hostMemoryFreeGb;
  const total = p.hostMemoryTotalGb;
  if (free !== null && total !== null) {
    stats.push({
      label: sidecar
        ? 'Code runner memory free / total'
        : 'Host memory free / total',
      value: `${free} GB / ${total} GB`,
    });
  }
  return stats;
}

const DAY_S = 86400;

/** "once a day", "once an hour", "once every 10 minutes": in the largest whole unit. */
function oncePer(seconds: number): string {
  const units: [number, string][] = [
    [DAY_S, 'day'],
    [3600, 'hour'],
    [60, 'minute'],
    [1, 'second'],
  ];
  for (const [size, name] of units) {
    if (seconds >= size && seconds % size === 0) {
      const n = seconds / size;
      if (n === 1) return `once ${name === 'hour' ? 'an' : 'a'} ${name}`;
      return `once every ${n} ${name}s`;
    }
  }
  return `once every ${seconds} seconds`;
}

/** Says how often this email may come, from the cooldown the event carries (one day when it has none). */
export function footerNoteFor(cooldownSeconds: unknown): string {
  const raw =
    typeof cooldownSeconds === 'number' && Number.isFinite(cooldownSeconds)
      ? cooldownSeconds
      : null;
  const seconds = raw !== null && raw >= 1 ? Math.round(raw) : DAY_S;
  return (
    'Admins are notified in the app each time code runs are held back again; ' +
    `this email is sent at most ${oncePer(seconds)} for each level. It goes to each admin of this workspace.`
  );
}

/**
 * The email for a validated warning or critical alert, or `null` for a
 * resolved notice. Every sentence is built here from the alert's enums and
 * numbers, and the link is a constant, so a forged event cannot put its own
 * text or link in a product email.
 */
export function buildSandboxCapacityMail(
  alert: SandboxCapacityAlert,
): SandboxCapacityMail | null {
  if (alert.kind === 'resolved') return null;
  const level = alert.kind;
  const sidecar = alert.memorySource === 'sidecar';
  const templateData: SandboxCapacityAlertData = {
    heading: SANDBOX_ALERT_TITLES[level],
    intro: introFor(level, alert),
    reasonText: reasonSentence(alert),
    actions: actionsFor(alert.reason, sidecar),
    stats: statsFor(level, alert, sidecar),
    docsUrl: SANDBOX_ALERT_DOCS_URL,
    footerNote: footerNoteFor(alert.emailCooldownSeconds),
  };
  const indexingText = indexingSentence(alert);
  if (indexingText) templateData.indexingText = indexingText;
  return {
    subject: `${getBrandConfig().productName}: ${SUBJECTS[level]}`,
    templateData,
  };
}
