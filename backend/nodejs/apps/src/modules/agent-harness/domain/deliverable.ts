/**
 * ADR-RUN-01 D1: what a run owes the person, resolved once from its request at admission and pinned as
 * `x.pipeshub.deliverable`: a file the request names or asks for, or an answer in chat. The worker reads the pin and
 * never parses the request again. Python resolves a run admitted before the pin with the same expressions
 * (`harness_core/engines/outcome/deliverable.py`); `contracts/harness/vectors/deliverable.json` pins both.
 */

export type PinnedDeliverable =
  { kind: 'file'; name?: string } | { kind: 'answer' };

/** A file name the request spells out: a word with a known document extension, not part of a path or URL. */
const FILE_NAME =
  /(?<![\w/.-])([A-Za-z0-9][\w.-]{0,100}\.(?:md|markdown|txt|csv|json|docx|xlsx|pdf|pptx|html))\b/;
/** "make a file", "save it as a markdown document", "write a report file": the request wants a file. */
const ASKS_FOR_FILE =
  /\b(?:make|create|write|save|produce|generate|give|prepare|build|export|draft|put)\b[^.\n]{0,80}?\b(?:file|document|doc|markdown|\.md|spreadsheet|csv|download(?:able)?)\b/i;

export function resolveDeliverable(request: string): PinnedDeliverable {
  const named = FILE_NAME.exec(request);
  if (named !== null) return { kind: 'file', name: named[1] };
  return ASKS_FOR_FILE.test(request) ? { kind: 'file' } : { kind: 'answer' };
}
