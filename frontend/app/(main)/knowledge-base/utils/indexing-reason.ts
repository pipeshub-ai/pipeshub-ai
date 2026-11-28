import type { TFunction } from 'i18next';

/**
 * Stored failure reasons that speak to the person who added the file, each with the key of the line any other reader
 * reads instead: someone else would take "you" for themselves. The server stores no code for these, so they are
 * matched by their exact words. None by default, so every reader reads a reason as it is stored.
 */
let spokenToUploader: ReadonlyMap<string, string> = new Map();

/** Names the reasons that speak to the uploader, with the key each reads as for anyone else; `undefined` names none. */
export function setReasonsSpokenToUploader(reasons: ReadonlyMap<string, string> | undefined): void {
  spokenToUploader = reasons ?? new Map();
}

/** A record's failure reason as this reader should read it. Only a viewer known to be the uploader gets their own words. */
export function indexingReasonFor<T extends string | null | undefined>(reason: T, t: TFunction, readerIsUploader = false): T | string {
  const stored = reason?.trim();
  const forOthers = !readerIsUploader && stored ? spokenToUploader.get(stored) : undefined;
  return forOthers ? t(forOthers) : reason;
}
