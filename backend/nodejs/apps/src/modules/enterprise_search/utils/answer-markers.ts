// Artifact and download cards are rendered from these markers, and only the
// backend may author them (appended to a completed answer). Streamed model text
// is a prompt-injection surface, so any marker found in it is attacker-shaped.
// Same forms as `_LLM_*_MARKER_RE` in backend/python/app/utils/streaming.py.
const MODEL_AUTHORED_MARKERS: readonly RegExp[] = [
  /::artifact\[[^\]]+\]\([^)]+\)\{[^}]*\}/g,
  /::artifact\[[^\]]+\]\{[^}]*\}/g,
  /::artifact\[[^\]]+\](?:\([^)]*\))?(?!\{)/g,
  /::download_conversation_task\[[^\]]+\]\([^)]+\)/g,
];

export function stripModelAuthoredMarkers(text: string): string {
  return MODEL_AUTHORED_MARKERS.reduce(
    (current, pattern) => current.replace(pattern, ''),
    text,
  );
}
