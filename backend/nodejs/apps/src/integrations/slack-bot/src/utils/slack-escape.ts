/**
 * SEC-B-03: the one escaper for text the bot did not write (model answers, tool and file names, indexed record names).
 *
 * In Slack mrkdwn `<…>` is an instruction to Slack: `<!channel>` notifies a whole channel, `<@U…>` pings a person,
 * `<url|label>` shows any label on any target. Slack decodes only `&amp;`, `&lt;` and `&gt;`, so escaping those three
 * characters with no exceptions leaves no way to spell a live token, whatever is nested, encoded or hidden inside it.
 * Links and mentions are built only by the bot, through `slackLink`, never passed through from untrusted text.
 */

/** Zero-width and bidi controls: invisible, so they can make a label read as a host it is not. */
const INVISIBLE_CONTROLS =
  /[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]/g;

const SAFE_LINK_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

/** `&`, `<` and `>` escaped, so Slack shows the text and acts on none of it. */
export function escapeSlackText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** The text with its invisible controls removed: what a reader actually sees. */
export function stripInvisibleControls(text: string): string {
  return text.replace(INVISIBLE_CONTROLS, '');
}

function parseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

/** The host a label claims to be, or null when it does not read as an address. */
function claimedHost(label: string): string | null {
  const seen = stripInvisibleControls(label).trim();
  const bare = seen.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '');
  // A label reads as an address when it starts with a dotted host ("pipeshub.example/login", "https://x.io").
  if (bare === seen && !/^[^\s/@]+\.[^\s/.@]{2,}(?:[/?#:]|$)/.test(bare))
    return null;
  return parseUrl(`https://${bare}`)?.hostname.toLowerCase() ?? null;
}

/** What a reader is shown for a link's target: its host and path, without the scheme. */
function targetDisplay(target: URL): string {
  if (target.protocol === 'mailto:') return target.pathname;
  const path = target.pathname === '/' ? '' : target.pathname;
  const shown = `${target.hostname}${path}`;
  return shown.length > 80 ? `${shown.slice(0, 79)}…` : shown;
}

/**
 * The label a link may show: the given one unless it reads as an address on another host than the target, in which
 * case the target itself is shown. A label naming the target's own host (a citation, our own record links) is kept.
 */
export function safeLinkLabel(url: string, label: string | undefined): string {
  const target = parseUrl(url);
  const seen = stripInvisibleControls(label ?? '').trim();
  if (!target) return seen;
  if (seen === '') return targetDisplay(target);
  const host = claimedHost(seen);
  if (host !== null && host !== target.hostname.toLowerCase()) {
    return targetDisplay(target);
  }
  return seen;
}

/** A URL the bot may put inside `<…|…>`: an http(s) or mailto target, with Slack's delimiters percent-encoded. */
export function safeLinkTarget(url: string): string | null {
  const parsed = parseUrl(stripInvisibleControls(url).trim());
  if (!parsed || !SAFE_LINK_SCHEMES.has(parsed.protocol)) return null;
  return parsed.href
    .replace(/\|/g, '%7C')
    .replace(/</g, '%3C')
    .replace(/>/g, '%3E');
}

/**
 * A mrkdwn link the bot builds from a parsed target and label. A target that is not a web or mail address is shown as
 * escaped text instead, so nothing an attacker wrote becomes clickable under another name.
 */
export function slackLink(url: string, label?: string): string {
  const target = safeLinkTarget(url);
  if (target === null) {
    const shown = label && label !== url ? `${label} (${url})` : url;
    return escapeSlackText(stripInvisibleControls(shown));
  }
  // `|` cannot end the label early (Slack splits at the first one), but `>` would, and escaping removes it.
  return `<${escapeSlackText(target)}|${escapeSlackText(safeLinkLabel(target, label))}>`;
}
