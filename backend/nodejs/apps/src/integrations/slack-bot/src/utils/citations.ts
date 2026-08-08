/**
 * Slack citation helpers.
 *
 * The LLM emits citations as `[source](refN)` / `[source](https://refN.xyz)`.
 * The web UI rewrites those into numbered chips; Slack's markdown→mrkdwn
 * converter turns them into broken `<refN|source>` links. Rewrite resolvable
 * numbered citations and strip remaining tiny-ref links before conversion.
 */

export type SlackCitationLike = {
  citationData: {
    chunkIndex?: string | number;
    metadata: {
      recordId?: string;
      recordType?: string;
      webUrl?: string;
      connector?: string;
    };
  };
};

/** `[label](refN)` or `[label](https://refN.xyz[/...])` */
const TINY_REF_CITATION_LINK_PATTERN =
  /\[[^\]]*\]\s*\(\s*(?:https?:\/\/)?ref\d+(?:\.xyz)?(?:\/[^)]*)?\s*\)/gi;

/** Any `[N](target)` markdown link */
const NUMBERED_MARKDOWN_LINK_PATTERN = /\[(\d+)\]\(([^)]+)\)/g;

function parseCitationNumber(rawValue: unknown): number | null {
  if (typeof rawValue === "number" && Number.isInteger(rawValue) && rawValue > 0) {
    return rawValue;
  }
  if (typeof rawValue !== "string") {
    return null;
  }
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function normalizeFrontendBaseUrl(frontendBaseUrl: string): string {
  return (frontendBaseUrl || "http://localhost:3000").replace(/\/$/, "");
}

function absolutizeWebUrl(webUrl: string, frontendBaseUrl: string): string {
  const trimmed = webUrl.trim();
  if (!trimmed) {
    return "";
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const base = normalizeFrontendBaseUrl(frontendBaseUrl);
  return trimmed.startsWith("/") ? `${base}${trimmed}` : `${base}/${trimmed}`;
}

function resolveCitationWebUrl(
  citation: SlackCitationLike,
  frontendBaseUrl: string,
): string {
  const meta = citation.citationData.metadata;
  const base = normalizeFrontendBaseUrl(frontendBaseUrl);

  if (meta.recordType === "FILE" && meta.connector !== "WEB" && meta.recordId) {
    return `${base}/record/${meta.recordId}`;
  }

  const fromWebUrl = absolutizeWebUrl(meta.webUrl || "", frontendBaseUrl);
  if (fromWebUrl) {
    return fromWebUrl;
  }

  if (meta.recordId) {
    return `${base}/record/${encodeURIComponent(meta.recordId)}`;
  }

  return "";
}

function isTinyRefTarget(target: string): boolean {
  return /^(?:https?:\/\/)?ref\d+(?:\.xyz)?(?:\/.*)?$/i.test(target.trim());
}

/** Remove `[source](refN)` / `[N](refN)` / tiny-web-ref links. */
export function stripTinyRefCitationLinks(text: string): string {
  if (!text) {
    return "";
  }
  return text.replace(TINY_REF_CITATION_LINK_PATTERN, "");
}

/**
 * Rewrite numbered citation links using citation metadata, then strip any
 * remaining tiny-ref links so markdown→mrkdwn cannot emit `<refN|source>`.
 */
export function rewriteCitationsForSlack(
  answerBody: string,
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): string {
  if (!answerBody) {
    return "";
  }

  const citationNumberToUrl = new Map<number, string>();
  for (const citation of citations || []) {
    const citationNumber = parseCitationNumber(citation.citationData.chunkIndex);
    if (!citationNumber || citationNumberToUrl.has(citationNumber)) {
      continue;
    }
    const citationWebUrl = resolveCitationWebUrl(citation, frontendBaseUrl);
    if (!citationWebUrl) {
      continue;
    }
    citationNumberToUrl.set(citationNumber, citationWebUrl);
  }

  let citationCount = 1;
  const webUrlToDisplayNumber = new Map<string, number>();

  let text = answerBody.replace(
    NUMBERED_MARKDOWN_LINK_PATTERN,
    (match, citationNumberText: string, target: string) => {
      const citationNumber = Number.parseInt(citationNumberText, 10);
      if (!Number.isInteger(citationNumber) || citationNumber <= 0) {
        return match;
      }

      const mappedUrl = citationNumberToUrl.get(citationNumber);
      if (mappedUrl) {
        let displayNumber = webUrlToDisplayNumber.get(mappedUrl);
        if (displayNumber === undefined) {
          displayNumber = citationCount;
          webUrlToDisplayNumber.set(mappedUrl, citationCount);
          citationCount += 1;
        }
        return `[${displayNumber}](${mappedUrl})`;
      }

      // Unmapped tiny refs become broken Slack links — drop them.
      if (isTinyRefTarget(target)) {
        return "";
      }

      return match;
    },
  );

  return stripTinyRefCitationLinks(text);
}
