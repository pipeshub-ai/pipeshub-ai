import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { Project } from '../schema/project.schema';
import { ProjectKnowledgeBaseService } from './project-kb.service';

const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_JITTER_MS = 60 * 60 * 1000;
const PAGE_SIZE = 200;

export interface ProjectKbDriftRepairHandle {
  stop(): void;
}

/** Queues a `projectKbSync` for every live project with a linked KB, one org and one page at a time. Returns how many were queued. */
export async function enqueueProjectKbSyncForAllOrgs(
  logger: Logger,
): Promise<number> {
  const orgIds = await Project.distinct('orgId', {
    isDeleted: false,
    linkedKnowledgeBaseId: { $ne: null },
  });
  let queued = 0;
  for (const orgId of orgIds) {
    let afterId: Types.ObjectId | null = null;
    for (;;) {
      const page = await Project.find({
        orgId,
        isDeleted: false,
        linkedKnowledgeBaseId: { $ne: null },
        ...(afterId ? { _id: { $gt: afterId } } : {}),
      })
        .sort({ _id: 1 })
        .limit(PAGE_SIZE);
      for (const project of page) {
        try {
          await ProjectKnowledgeBaseService.enqueueSync(project);
          queued += 1;
        } catch (error) {
          logger.warn('Failed to queue project KB drift repair', {
            projectId: project._id.toString(),
            error,
          });
        }
      }
      const last = page.at(-1);
      if (!last || page.length < PAGE_SIZE) break;
      afterId = last._id;
    }
  }
  return queued;
}

/**
 * Daily safety net for the hidden project KBs: the sync events carry the full
 * desired state, so re-queuing one per project repairs any graph drift (a lost
 * event, an edge changed by hand). Every replica runs this, which only queues
 * duplicate syncs that converge to the same graph.
 */
export function startProjectKbDriftRepair(
  logger: Logger,
  intervalMs: number = DAY_MS,
  jitterMs: number = DEFAULT_JITTER_MS,
): ProjectKbDriftRepairHandle {
  let timer: NodeJS.Timeout | null = null;
  let stopped = false;

  const schedule = (): void => {
    if (stopped) return;
    timer = setTimeout(
      () => {
        void run();
      },
      intervalMs + Math.floor(Math.random() * jitterMs),
    );
    timer.unref();
  };

  const run = async (): Promise<void> => {
    try {
      const queued = await enqueueProjectKbSyncForAllOrgs(logger);
      logger.info('Queued project KB drift repair', { queued });
    } catch (error) {
      logger.warn('Project KB drift repair failed', error);
    } finally {
      schedule();
    }
  };

  schedule();
  return {
    stop: () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  };
}
