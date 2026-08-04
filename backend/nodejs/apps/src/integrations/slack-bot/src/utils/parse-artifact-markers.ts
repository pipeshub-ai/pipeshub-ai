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

  // Remnant / hallucinated short forms — strip entirely.
  text = text.replace(/::artifact\[[^\]]+\](?:\([^)]*\))?(?:\{[^}]*\})?/g, "");
  // Legacy download-task markers (frontend also strips these).
  text = text.replace(/::download_conversation_task\[[^\]]+\]\([^)]*\)/g, "");

  return { text: text.replace(/\n{3,}/g, "\n\n").trimEnd(), artifacts };
}
