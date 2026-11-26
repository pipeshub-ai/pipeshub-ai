/**
 * The words of an approval card (UXINT-3): a title from the tool's name and a verb, never `server · tool_id`; a
 * reason line per rule, read from i18n by the rule code so a renamed or new rule is a new key, not new prose here.
 */
import type { TFunction } from 'i18next';
import { approvalAction, isFirstPartyTool, passesForFirstParty, safeText } from './approval-preview';
import { humanizeToolName, isWebTool } from './utils/tool-display';
import { displaySafe } from '@/lib/utils/display-safe';
import { engineName, isAcpEngine } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { approvalAskRules } from '@/config';
import type { ApprovalReason, PendingApproval } from './types';

/** A destination is the call's own text: masked like its arguments (QA-11) and display-safe (SEC-B-04). */
const masked = (text: string | undefined): string | undefined => (text === undefined ? undefined : safeText(text));

/** Tools with a plain verb of their own ("Search the web 3 times"); any other is "Create ticket (3 calls)". */
export const VERBS: ReadonlySet<string> = new Set(['web_search', 'fetch_url', 'generate_image', 'run_code', 'install_packages']);

/** The tool's own name, without its `{app}__` or `dynamic__` prefix or an MCP server's `mcp_` mark. */
export function bareTool(toolName: string): string {
  if (toolName.includes('__')) return toolName.slice(toolName.lastIndexOf('__') + 2);
  // shortcut: `mcp_{server}_{tool}` is split at the first `_` after `mcp_`; a server id with `_` in it reads wrong.
  const mcp = /^mcp_[^_]+_(.+)$/.exec(toolName);
  return mcp ? mcp[1] : toolName;
}

/** An MCP tool's server, as its id names it (`mcp_e2esaas_create_ticket` → `e2esaas`). */
const mcpServer = (toolName: string): string | null => /^mcp_([^_]+)_./.exec(toolName)?.[1] ?? null;

/**
 * An app a third party named (an MCP server, an API toolset) that reads as one of PipesHub's own apps keeps a mark
 * of what it is, "Slack (MCP)": a server an agent's owner named "Slack" never passes for the Slack connector
 * (SEC-B-04). "MCP" and "API" are names, the same in every language.
 */
function thirdPartyApp(name: string, toolName: string): string {
  if (isFirstPartyTool(toolName) || !passesForFirstParty(name)) return name;
  return `${name} (${toolName.startsWith('api_') ? 'API' : 'MCP'})`;
}

/** "E2Esaas · create_ticket" names the app before the dot; a tool's own `{app}__` prefix names it otherwise. */
export function appOf(approval: Pick<PendingApproval, 'toolName' | 'displayName'>): string | null {
  const dotted = shownName(approval.displayName)?.split(' · ');
  if (dotted && dotted.length === 2 && dotted[0].trim()) return thirdPartyApp(dotted[0].trim(), approval.toolName);
  const named = approvalAction(approval.toolName).app ?? mcpServer(approval.toolName);
  return named === null ? null : thirdPartyApp(named, approval.toolName);
}

/** A display name as a card shows it: a third party's text, so display-safe (SEC-B-04). */
export const shownName = (displayName: string | undefined): string | undefined =>
  displayName === undefined ? undefined : displaySafe(displayName);

/** "Generate 7 images", "Create ticket": what the call does, for `count` calls of it. */
export function interactionAction(
  t: TFunction,
  approval: Pick<PendingApproval, 'toolName' | 'displayName'>,
  count = 1,
): string {
  const bare = bareTool(approval.toolName);
  if (VERBS.has(bare)) return t(`interaction.verb.${bare}`, { count });
  const display = shownName(approval.displayName);
  const dotted = display?.split(' · ');
  // "E2Esaas · create_ticket" and a bare tool id are ids, worded "Create ticket"; a written name stays as written.
  const id = dotted && dotted.length === 2 ? dotted[1] : display?.trim() ? null : bare;
  const action = id !== null ? humanizeToolName(id) : (display ?? '').trim();
  return t('interaction.verb.generic', { action, count });
}

const sameWords = (a: string, b: string): boolean => {
  const words = (s: string) => new Set(s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const app = words(b);
  return app.size > 0 && [...app].every((w) => words(a).has(w));
};

/** The repository tools' titles (WP-63): the repository, or the branch and repository, is what the person decides. */
const REPOSITORY_TITLES: Readonly<Record<string, string>> = {
  repository_fetch: 'interaction.titleClone',
  repository_push: 'interaction.titlePush',
};

/** WP-63: the upload hold of a read-write site after a private read, asked once per run and site. */
export const SITE_UPLOAD_RULE = 'site_upload';

/** `pipeshub@egress/site_upload`: the upload card's tool. */
export const isSiteUpload = (approval: Pick<PendingApproval, 'toolName' | 'reason'>): boolean =>
  approval.reason?.rule === SITE_UPLOAD_RULE || /(?:^|[/_])site_upload$/.test(approval.toolName);

/** A `repository_fetch` card (WP-63): the clone of one public repository. */
export const isClone = (approval: Pick<PendingApproval, 'toolName'>): boolean =>
  bareTool(approval.toolName) === 'repository_fetch' || /(?:^|[/_])repository_fetch$/.test(approval.toolName);

export interface CloneDetails {
  /** `github.com/onyx-dot-app/onyx`. */
  repository: string;
  host: string;
  /** The last fetch's size ("about"), or the most a first clone may bring ("up to"); null when not named. */
  size: { kind: 'about' | 'upTo'; mb: number } | null;
}

/**
 * The clone card's destination, `github.com/onyx-dot-app/onyx (public, read-only, about 42 MB)`, as its parts: the
 * card names the repository in its title and the size in its body (the approved mock).
 */
export function cloneDetails(destination: string | undefined): CloneDetails | null {
  const where = masked(destination)?.trim();
  if (!where) return null;
  const match = /^(.*?)\s*\(([^()]*)\)\s*$/.exec(where);
  const repository = (match ? match[1] : where).trim();
  const detail = match ? match[2] : '';
  const about = /about\s+(\d+)\s*MB/i.exec(detail);
  const upTo = /up to\s+(\d+)\s*MB/i.exec(detail);
  const size = about
    ? { kind: 'about' as const, mb: Number(about[1]) }
    : upTo
      ? { kind: 'upTo' as const, mb: Number(upTo[1]) }
      : null;
  return { repository, host: repository.split('/')[0] ?? repository, size };
}

/** "Generate 7 images?", "Create ticket in E2Esaas?", "Send message to #sales in Slack?". */
export function interactionTitle(
  t: TFunction,
  approval: Pick<PendingApproval, 'toolName' | 'displayName' | 'reason'>,
  count = 1,
): string {
  // WP-63: the upload hold names the site ("Allow uploads to mirror.acme.net for this run?").
  if (isSiteUpload(approval)) {
    return t('interaction.titleSiteUpload', { destination: masked(approval.reason?.destination) ?? t('interaction.outside') });
  }
  // WP-63: a clone names its repository ("Clone github.com/o/r?", its size in the card's body); a push its branch.
  const repository = REPOSITORY_TITLES[bareTool(approval.toolName)];
  const where = masked(approval.reason?.destination);
  if (repository !== undefined && where) {
    const clone = isClone(approval) ? cloneDetails(where) : null;
    return t(repository, { destination: clone?.repository ?? where });
  }
  const what = interactionAction(t, approval, count);
  // A built-in tool's verb says it all ("Generate 7 images"); its toolset name would only repeat it.
  const rawApp = VERBS.has(bareTool(approval.toolName)) ? null : appOf(approval);
  const app = rawApp && !sameWords(what, rawApp) ? rawApp : null;
  // A web read names where it goes in its reason line; a send names it in its title.
  const destination = isWebTool(bareTool(approval.toolName)) ? undefined : masked(approval.reason?.destination);
  if (destination && app) return t('interaction.titleToIn', { what, destination, app });
  if (destination) return t('interaction.titleTo', { what, destination });
  if (app) return t('interaction.titleIn', { what, app });
  return t('interaction.title', { what });
}

/** "Codex wants to:", or "The code task wants to:" when the engine is not named (an older server). */
export function childAttribution(t: TFunction, approval: Pick<PendingApproval, 'childEngine' | 'childRole'>): string {
  const engine = approval.childEngine;
  if (!engine) {
    // A delegate keeps its role ("Sub-agent “research” wants to:"); the generic name is the code task's.
    const role = approval.childRole?.replace(/^pipeshub-subagent-?/, '');
    return role ? t('approvalCard.child.from', { role }) : t('interaction.childWants');
  }
  const fallback = isAcpEngine(engine) ? engineName(engine) : humanizeToolName(engine);
  return t('interaction.engineWants', { engine: t(`interaction.engine.${engine}`, { defaultValue: fallback }) });
}

/** A call that only reads (a web search, a page fetch): the reason line says that it changes nothing. */
export const readsOnly = (approval: Pick<PendingApproval, 'toolName'>): boolean => isWebTool(bareTool(approval.toolName));

const POLICY_WHY: Readonly<Record<string, string>> = {
  deployment: 'approvalCard.policy.whyDeployment',
  org: 'approvalCard.policy.whyOrg',
  agent: 'approvalCard.policy.whyAgent',
  engine: 'approvalCard.policy.whyEngine',
};
const POLICY_RULES: ReadonlySet<string> = new Set(['writes', 'effect_class', 'tool']);
/** The org's policy webhook asked (G-40): decided on its own card, once, never for the conversation. */
export const EXTERNAL_POLICY_RULE = 'external_policy';
const SUBMIT_PLAN = 'submit_plan';
const PLAN_RULE = 'plan_approval';

/** The approval asks about the run's plan (G-04), not a call. */
export const isPlanApproval = (card: Pick<PendingApproval, 'toolName' | 'reason'>): boolean =>
  card.reason?.rule === PLAN_RULE || card.toolName === SUBMIT_PLAN || card.toolName.endsWith(`/${SUBMIT_PLAN}`);

/** The values a reason line's words take: an edition rule's, read from its fields; none for this build's rules. */
export function interactionWhyValues(
  reason: ApprovalReason | undefined,
): Readonly<Record<string, string | number>> | undefined {
  return reason ? approvalAskRules.whyValues(reason) : undefined;
}

/**
 * The reason line, keyed by the rule code that holds the call (and, for the flow rule, what the run read and
 * whether the call only reads). Null: a rule this build does not word.
 */
export function interactionWhy(
  t: TFunction,
  approval: Pick<PendingApproval, 'toolName' | 'reason'>,
  count = 1,
): string | null {
  const reason: ApprovalReason | undefined = approval.reason;
  if (!reason) return null;
  if (isSiteUpload(approval)) {
    return t('interaction.why.siteUpload', { destination: masked(reason.destination) ?? t('interaction.outside') });
  }
  if (reason.rule === 'flow_external_write') {
    const seen = reason.sensitivity === 'private' ? 'Private' : 'Unknown';
    if (!readsOnly(approval)) return t(`approvalCard.flow.why${seen}`);
    return t(`interaction.why.flowRead${seen}`, { count, destination: masked(reason.destination) ?? t('interaction.outside') });
  }
  if (reason.rule === 'engine_writes') {
    return t(reason.sensitivity === 'public' ? 'approvalCard.engine.why' : 'approvalCard.engine.whyRead');
  }
  if (POLICY_RULES.has(reason.rule)) return t((reason.layer && POLICY_WHY[reason.layer]) || 'approvalCard.policy.why');
  if (reason.rule === 'plan_approval') return t('approvalCard.plan.why');
  if (reason.rule === EXTERNAL_POLICY_RULE) return t('approvalCard.externalPolicy.why');
  const key = approvalAskRules.whyKey(reason);
  return key !== undefined ? t(key, interactionWhyValues(reason)) : null;
}

/**
 * When a waiting card was asked, as the steps' header says it (WP-97): the time today, else the date and time, in
 * the reader's locale. Undefined for a time that cannot be read.
 */
export function waitingSinceTime(iso: string, locale: string, now: Date = new Date()): string | undefined {
  const at = new Date(iso);
  if (!Number.isFinite(at.getTime())) return undefined;
  const sameDay = at.toDateString() === now.toDateString();
  return sameDay
    ? at.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    : at.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** The longest place a button names; a longer one is clipped there, and the scope line under it says it whole. */
const GRANT_BUTTON_CHARS = 40;

/**
 * The one place every call of a card goes, as the conversation grant names it (FLOWGRANT): masked, and only when
 * they all go there (a grant covers one destination).
 */
export function grantDestination(items: readonly Pick<PendingApproval, 'reason'>[]): string | undefined {
  const places = new Set(items.map((item) => safeText(item.reason?.destination ?? '')));
  const [only] = places;
  return places.size === 1 && only ? only : undefined;
}

/** "Allow onyx.app for this conversation": the scope said on the button itself, never a silent default. */
export function allowConversationText(t: TFunction, destination: string | undefined): string {
  if (!destination) return t('approvalCard.flow.allowConversation');
  const shown = destination.length > GRANT_BUTTON_CHARS ? `${destination.slice(0, GRANT_BUTTON_CHARS - 1)}…` : destination;
  return t('approvalCard.flow.allowConversationFor', { destination: shown });
}
