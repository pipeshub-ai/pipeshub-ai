import type { AdmitRunCommand } from '../domain/admission';

/** A queued next run decided again: the admission to make now, or the reason it is refused. */
export type Readmission = { command: AdmitRunCommand } | { refused: string };

/**
 * Decides a queued next run (SS2) again when it drains, as a fresh admission
 * of its sender would be decided now (R2 F6): grants, ceilings and the sender
 * themselves may have changed while it waited.
 */
export interface NextRunReadmission {
  readmit(command: AdmitRunCommand): Promise<Readmission>;
}
