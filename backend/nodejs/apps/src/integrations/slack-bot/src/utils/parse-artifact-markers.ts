export interface ParsedSlackArtifact {
  fileName: string;
  downloadUrl: string;
  mimeType: string;
  recordId?: string;
}

const RECORD_PLACEHOLDER_PREFIX = "record:";

/**
 * Strip `::artifact[...]` markers from assistant content (frontend does this
 * via parseArtifactMarkers and renders an Artifacts panel instead).
 * Also returns any markers that carry a real download URL.
 */
export function parseArtifactMarkers(content: string): {
  text: string;
  artifacts: ParsedSlackArtifact[];
} {
  const artifacts: ParsedSlackArtifact[] = [];
  const seen = new Set<string>();

  let text = content.replace(
    /::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}/g,
    (_match, fileName: string, url: string, meta: string) => {
      const [mime = "", , recordId = ""] = String(meta).split("|");
      const cleanName = String(fileName).trim() || "artifact";
      const rawUrl = String(url).trim();
      const fromPlaceholder = rawUrl.startsWith(RECORD_PLACEHOLDER_PREFIX)
        ? rawUrl.slice(RECORD_PLACEHOLDER_PREFIX.length).trim()
        : "";
      const cleanUrl = fromPlaceholder ? "" : rawUrl;
      const cleanRecordId = recordId.trim() || fromPlaceholder;
      const dedupeKey = `${cleanRecordId || cleanUrl}:${cleanName}`;
      if (!seen.has(dedupeKey)) {
        seen.add(dedupeKey);
        artifacts.push({
          fileName: cleanName,
          downloadUrl: cleanUrl,
          mimeType: mime.trim() || "application/octet-stream",
          ...(cleanRecordId ? { recordId: cleanRecordId } : {}),
        });
      }
      return "";
    },
  );

  text = stripArtifactRemnants(text);

  return { text: text.replace(/\n{3,}/g, "\n\n").trimEnd(), artifacts };
}

function stripArtifactRemnants(text: string): string {
  // Remnant / hallucinated short forms — strip entirely.
  const withoutShortForms = text.replace(
    /::artifact\[[^\]]+\](?:\([^)]*\))?(?:\{[^}]*\})?/g,
    "",
  );
  // Legacy download-task markers (frontend also strips these).
  return withoutShortForms.replace(
    /::download_conversation_task\[[^\]]+\]\([^)]*\)/g,
    "",
  );
}

/**
 * Markers removed from one streamed piece of the answer. Unlike
 * {@link parseArtifactMarkers} it keeps the surrounding whitespace: the piece
 * is joined to the next one as it is.
 */
export function stripArtifactMarkers(piece: string): string {
  return stripArtifactRemnants(
    piece.replace(/::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}/g, ''),
  );
}
