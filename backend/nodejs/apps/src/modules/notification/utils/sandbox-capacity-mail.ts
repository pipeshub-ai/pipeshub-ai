import { getBrandConfig } from '../../../libs/branding';
import type {
  SandboxCapacityAlertData,
  SandboxCapacityAlertStat,
} from '../../mail/utils/emailTemplates';

const DEFAULT_DOCS_URL =
  'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts';

type Level = 'warning' | 'critical';

const SUBJECTS: Record<Level, string> = {
  warning: 'code runs in chat are waiting for capacity',
  critical: 'code runs in chat are being refused',
};

/** What a code sandbox load alert carries in its `payload`, as far as the email uses it. */
export interface SandboxCapacityAlertPayload {
  level?: unknown;
  host?: unknown;
  sandboxMode?: unknown;
  memorySource?: unknown;
  windowMinutes?: unknown;
  runs?: unknown;
  waited?: unknown;
  refused?: unknown;
  busyPercent?: unknown;
  refusedPercent?: unknown;
  longestWaitSeconds?: unknown;
  slotWaitSeconds?: unknown;
  /** How often each level may be emailed; older events lack it (one day). */
  emailCooldownSeconds?: unknown;
  reason?: unknown;
  reasonText?: unknown;
  indexingText?: unknown;
  limit?: { current?: unknown; ceiling?: unknown } | null;
  hostMemoryTotalGb?: unknown;
  hostMemoryFreeGb?: unknown;
}

export interface SandboxCapacityMail {
  subject: string;
  templateData: SandboxCapacityAlertData;
}

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

/** An http(s) link, or `null`: the email puts it in an `href`, where escaping does not stop `javascript:`. */
function webUrl(value: unknown): string | null {
  const raw = text(value);
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' || url.protocol === 'http:' ? raw : null;
  } catch {
    return null;
  }
}

function actionsFor(reason: unknown, sidecar: boolean): string[] {
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

function introFor(level: Level, p: SandboxCapacityAlertPayload): string {
  const minutes = num(p.windowMinutes) ?? 30;
  const runs = num(p.runs) ?? 0;
  const slotWait = num(p.slotWaitSeconds);
  if (level === 'critical') {
    const waitText = slotWait === null ? '' : ` after waiting ${slotWait} s`;
    return (
      `In the last ${minutes} minutes, ${num(p.refused) ?? 0} of ${runs} code runs in chat ` +
      `(${num(p.refusedPercent) ?? 0}%) were refused${waitText} for a free sandbox. ` +
      'Users saw an error instead of a result.'
    );
  }
  const longest = num(p.longestWaitSeconds);
  const longestText =
    longest === null ? '' : `, and the longest wait was ${longest} s`;
  const refuseText =
    slotWait === null
      ? ''
      : ` Runs that wait longer than ${slotWait} s are refused.`;
  return (
    `For ${num(p.busyPercent) ?? 0}% of the last ${minutes} minutes, code runs in chat waited ` +
    `for a free sandbox. ${num(p.waited) ?? 0} of ${runs} runs waited${longestText}. ` +
    `Users saw "waiting in queue" before their code ran.${refuseText}`
  );
}

function statsFor(
  level: Level,
  p: SandboxCapacityAlertPayload,
  sidecar: boolean,
): SandboxCapacityAlertStat[] {
  const stats: SandboxCapacityAlertStat[] = [];
  const host = text(p.host);
  if (host) stats.push({ label: 'Host', value: host });
  stats.push({
    label: 'Window',
    value: `last ${num(p.windowMinutes) ?? 30} minutes`,
  });
  stats.push({ label: 'Runs', value: String(num(p.runs) ?? 0) });
  stats.push({
    label: 'Waited',
    value: `${num(p.waited) ?? 0} (${num(p.busyPercent) ?? 0}% of the time)`,
  });
  const refused = num(p.refused) ?? 0;
  stats.push({
    label: 'Refused',
    value:
      level === 'critical'
        ? `${refused} (${num(p.refusedPercent) ?? 0}%)`
        : String(refused),
  });
  const current = num(p.limit?.current);
  const ceiling = num(p.limit?.ceiling);
  if (current !== null && ceiling !== null) {
    stats.push({
      label: 'Runs allowed at once now / at most',
      value: `${current} / ${ceiling}`,
    });
  }
  const free = num(p.hostMemoryFreeGb);
  const total = num(p.hostMemoryTotalGb);
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
  const raw = num(cooldownSeconds);
  const seconds = raw !== null && raw >= 1 ? Math.round(raw) : DAY_S;
  return (
    'Admins are notified in the app each time code runs are held back again; ' +
    `this email is sent at most ${oncePer(seconds)} for each level. It goes to each admin of this workspace.`
  );
}

/**
 * The email for a code sandbox load alert, or `null` when the event is not a
 * warning or critical one. The numbers and the reason come from the event, so
 * the email and the in-app notification never disagree.
 */
export function buildSandboxCapacityMail(
  event: { title?: unknown; message?: unknown; redirectLink?: unknown },
  payload: SandboxCapacityAlertPayload,
): SandboxCapacityMail | null {
  const level = payload.level;
  if (level !== 'warning' && level !== 'critical') return null;
  const sidecar = payload.memorySource === 'sidecar';
  const heading = text(event.title) ?? '';
  const reasonText = text(payload.reasonText) ?? text(event.message) ?? '';
  const indexingText = text(payload.indexingText);
  const templateData: SandboxCapacityAlertData = {
    heading,
    intro: introFor(level, payload),
    reasonText,
    actions: actionsFor(payload.reason, sidecar),
    stats: statsFor(level, payload, sidecar),
    docsUrl: webUrl(event.redirectLink) ?? DEFAULT_DOCS_URL,
    footerNote: footerNoteFor(payload.emailCooldownSeconds),
  };
  if (indexingText) templateData.indexingText = indexingText;
  return {
    subject: `${getBrandConfig().productName}: ${SUBJECTS[level]}`,
    templateData,
  };
}
