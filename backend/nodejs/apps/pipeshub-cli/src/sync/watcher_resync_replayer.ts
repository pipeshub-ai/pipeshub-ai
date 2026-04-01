import * as fs from "fs";
import type { FileEntry, FileEvent } from "./watcher_state";
import { watcherStateFilePath } from "./watcher_state";
import {
  readReplayableWatchBatches,
  updateWatchBatchStatus,
} from "./watcher_sync_journal";

function listFilesUnderPrefix(
  files: Record<string, FileEntry>,
  prefix: string
): string[] {
  const normalizedPrefix = prefix.replace(/\/+$/, "");
  const childPrefix = normalizedPrefix ? `${normalizedPrefix}/` : "";
  return Object.keys(files)
    .filter((relPath) => {
      const entry = files[relPath];
      return (
        !entry?.isDirectory &&
        (relPath === normalizedPrefix || relPath.startsWith(childPrefix))
      );
    })
    .sort((a, b) => a.localeCompare(b));
}

function loadWatcherStateFiles(
  authDir: string,
  connectorInstanceId: string
): Record<string, FileEntry> {
  const statePath = watcherStateFilePath(connectorInstanceId, authDir);
  if (!fs.existsSync(statePath)) {
    return {};
  }
  try {
    const raw = JSON.parse(fs.readFileSync(statePath, "utf8")) as {
      files?: Record<string, FileEntry>;
    };
    return raw.files ?? {};
  } catch {
    return {};
  }
}

export function expandWatchEventsForReplay(
  events: FileEvent[],
  files: Record<string, FileEntry>
): FileEvent[] {
  const expanded: FileEvent[] = [];

  for (const event of events) {
    if (!event.isDirectory) {
      expanded.push(event);
      continue;
    }

    if (event.type === "DIR_DELETED") {
      for (const relPath of listFilesUnderPrefix(files, event.path)) {
        expanded.push({
          type: "DELETED",
          path: relPath,
          timestamp: event.timestamp,
          isDirectory: false,
        });
      }
      continue;
    }

    if (event.type === "DIR_MOVED" || event.type === "DIR_RENAMED") {
      const oldPrefix = String(event.oldPath ?? "").replace(/\/+$/, "");
      if (!oldPrefix) {
        continue;
      }
      for (const oldRelPath of listFilesUnderPrefix(files, oldPrefix)) {
        const suffix = oldRelPath.slice(oldPrefix.length).replace(/^\/+/, "");
        const newRelPath = [event.path, suffix].filter(Boolean).join("/");
        expanded.push({
          type: event.type === "DIR_MOVED" ? "MOVED" : "RENAMED",
          path: newRelPath,
          oldPath: oldRelPath,
          timestamp: event.timestamp,
          isDirectory: false,
        });
      }
    }
  }

  return expanded;
}

export async function replayPendingWatchBatches(
  authDir: string,
  connectorInstanceId: string,
  dispatchBatch: (events: FileEvent[], batchId: string) => Promise<void>,
  opts?: {
    includeSynced?: boolean;
  }
): Promise<{
  replayedBatches: number;
  replayedEvents: number;
  skippedBatches: number;
}> {
  const batches = readReplayableWatchBatches(
    authDir,
    connectorInstanceId,
    opts
  );
  if (batches.length === 0) {
    return {
      replayedBatches: 0,
      replayedEvents: 0,
      skippedBatches: 0,
    };
  }

  let replayedBatches = 0;
  let replayedEvents = 0;
  let skippedBatches = 0;

  for (const batch of batches) {
    const stateFiles = loadWatcherStateFiles(authDir, connectorInstanceId);
    const replayEvents =
      Array.isArray(batch.replayEvents) && batch.replayEvents.length > 0
        ? batch.replayEvents
        : expandWatchEventsForReplay(batch.events, stateFiles);

    if (replayEvents.length === 0) {
      updateWatchBatchStatus(authDir, connectorInstanceId, batch.batchId, "synced");
      skippedBatches += 1;
      continue;
    }

    try {
      await dispatchBatch(replayEvents, batch.batchId);
      updateWatchBatchStatus(authDir, connectorInstanceId, batch.batchId, "synced");
      replayedBatches += 1;
      replayedEvents += replayEvents.length;
    } catch (error) {
      updateWatchBatchStatus(authDir, connectorInstanceId, batch.batchId, "failed");
      throw error;
    }
  }

  return {
    replayedBatches,
    replayedEvents,
    skippedBatches,
  };
}
