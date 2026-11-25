import {
  PRIORITY_CLASSES,
  type PriorityClass,
} from '../contracts/generated/core/enums';

export type { PriorityClass };
export { PRIORITY_CLASSES };

/**
 * G-45: the run kinds a person waits on. Mirrors `priority-classes.json` (`runKinds`), as does the worker's
 * rate-budget mapping; a child is absent because it takes its parent's class.
 */
const RUN_KIND_CLASSES: Readonly<Record<string, PriorityClass>> = Object.freeze(
  {
    interactive: 'interactive',
    authoring: 'interactive',
    command: 'interactive',
    scheduled: 'background',
    workflow_code: 'background',
  },
);

/** 0 is served first. Stored beside the class so the claim query sorts on an index. */
export const priorityRank = (priorityClass: PriorityClass): number =>
  PRIORITY_CLASSES.indexOf(priorityClass);

/** A run recorded before classes existed reads as interactive: it was admitted when every run was served first. */
export const priorityClassOf = (run: {
  priorityClass?: PriorityClass | null;
}): PriorityClass => run.priorityClass ?? 'interactive';

export const priorityClassForRunKind = (runKind: string): PriorityClass =>
  RUN_KIND_CLASSES[runKind] ?? 'interactive';

/** A lower-class run a person just answered: they are waiting on it now, and it keeps that class from then on. */
export const afterApproval = (priorityClass: PriorityClass): PriorityClass =>
  priorityRank(priorityClass) > priorityRank('approval_resume')
    ? 'approval_resume'
    : priorityClass;

/** The two fields a run document stores for its class. */
export const priorityFields = (
  priorityClass: PriorityClass,
): { priorityClass: PriorityClass; priorityRank: number } => ({
  priorityClass,
  priorityRank: priorityRank(priorityClass),
});

/**
 * How claim-next orders the queue (G-45). `above`: only runs of a strictly higher class than it, for a full
 * worker that will make room by preempting a run of that class. `floor`: this claim serves the oldest due run of
 * any class, the share every lower class is owed so none starves.
 */
export interface ClaimOrder {
  above?: PriorityClass;
  floor?: boolean;
}

/** One claim in five serves the oldest due run whatever its class: background work keeps a fifth of the slots that free. */
export const DEFAULT_CLAIM_FLOOR_EVERY = 5;

/** Every `floorEvery`-th claim of a Node process is a floor claim; 0 turns the floor off. */
export class ClaimFloor {
  private claims = 0;

  constructor(private readonly floorEvery: number) {}

  next(): boolean {
    if (this.floorEvery <= 0) return false;
    this.claims = (this.claims + 1) % this.floorEvery;
    return this.claims === 0;
  }
}
