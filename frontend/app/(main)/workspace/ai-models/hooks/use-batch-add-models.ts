'use client';

import { useCallback, useRef, useState } from 'react';
import { AIModelsApi } from '../api';
import type { PickedModel } from '../types';

export type BatchRowStatus = 'pending' | 'checking' | 'healthy' | 'failed';

export interface BatchRow {
  model: string;
  status: BatchRowStatus;
  message?: string;
  modelKey?: string;
}

export interface BatchProgressEvent {
  model?: string;
  status?: BatchRowStatus;
  message?: string;
  modelKey?: string;
}

/** A retry replaces only the rows it is checking and leaves the others in place. */
export function mergeBatchRows(existing: BatchRow[], incoming: BatchRow[]): BatchRow[] {
  const incomingByModel = new Map(incoming.map((row) => [row.model, row]));
  const merged = existing.map((row) => incomingByModel.get(row.model) ?? row);
  for (const row of incoming) {
    if (!merged.some((current) => current.model === row.model)) merged.push(row);
  }
  return merged;
}

/** A stream can end, or an abort can be swallowed, while rows are still in flight. */
export function failUnresolvedRows(rows: BatchRow[]): BatchRow[] {
  if (!rows.some((row) => row.status === 'pending' || row.status === 'checking')) return rows;
  return rows.map((row) =>
    row.status === 'pending' || row.status === 'checking'
      ? { ...row, status: 'failed', message: row.message ?? 'interrupted' }
      : row,
  );
}

export function applyBatchEvent(rows: BatchRow[], eventName: string, data: BatchProgressEvent): BatchRow[] {
  if (eventName !== 'progress' || !data.model || !data.status) return rows;
  return rows.map((row) =>
    row.model === data.model
      ? { ...row, status: data.status as BatchRowStatus, message: data.message, modelKey: data.modelKey ?? row.modelKey }
      : row,
  );
}

export interface BatchAddRequest {
  modelType: string;
  provider: string;
  configuration: Record<string, unknown>;
  models: PickedModel[];
  defaultModel?: string;
}

function connectionFingerprint(request: BatchAddRequest): string {
  const sorted = Object.keys(request.configuration)
    .sort()
    .map((key) => [key, request.configuration[key]]);
  return JSON.stringify([request.modelType, request.provider, sorted]);
}

export function useBatchAddModels() {
  const abortRef = useRef<AbortController | null>(null);
  const rowsRef = useRef<BatchRow[]>([]);
  // Retries join the connection a previous run created, as long as the shared settings are unchanged.
  const connectionRef = useRef<{ id: string; fingerprint: string } | null>(null);
  const [rows, setRowsState] = useState<BatchRow[]>([]);
  const [running, setRunning] = useState(false);

  const rememberRows = useCallback((next: BatchRow[]) => {
    rowsRef.current = next;
    setRowsState(next);
  }, []);

  const setRows = useCallback((update: (current: BatchRow[]) => BatchRow[]) => {
    rememberRows(update(rowsRef.current));
  }, [rememberRows]);

  const run = useCallback(async (request: BatchAddRequest, options?: { merge?: boolean }) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seeded = request.models.map((model) => ({ model: model.id, status: 'pending' as const }));
    let current: BatchRow[] = options?.merge ? mergeBatchRows(rowsRef.current, seeded) : seeded;
    rememberRows(current);
    setRunning(true);
    let saved = 0;
    let aborted = false;
    const fingerprint = connectionFingerprint(request);
    const joinConnection =
      connectionRef.current?.fingerprint === fingerprint ? connectionRef.current.id : undefined;
    const remember = (next: BatchRow[]) => {
      current = next;
      rememberRows(next);
    };
    try {
      await AIModelsApi.batchAddModels(
        {
          modelType: request.modelType,
          provider: request.provider,
          configuration: request.configuration,
          defaultModel: request.defaultModel,
          ...(joinConnection ? { connectionId: joinConnection } : {}),
          models: request.models.map((model) => ({
            model: model.id,
            modelFriendlyName: model.modelFriendlyName,
            isMultimodal: model.isMultimodal,
            isReasoning: model.isReasoning,
            contextLength: model.contextLength,
          })),
        },
        {
          signal: controller.signal,
          onError: (error) => {
            remember(
              current.map((row) =>
                row.status === 'pending' || row.status === 'checking'
                  ? { ...row, status: 'failed', message: error.message }
                  : row,
              ),
            );
          },
          onEvent: (event) => {
            if (event.event === 'done') {
              const data = event.data as { saved?: number; aborted?: boolean; connectionId?: string };
              saved = data.saved ?? saved;
              aborted = Boolean(data.aborted);
              if (saved > 0 && data.connectionId) {
                connectionRef.current = { id: data.connectionId, fingerprint };
              }
            }
            remember(applyBatchEvent(current, event.event, (event.data ?? {}) as BatchProgressEvent));
          },
        },
      );
    } finally {
      if (abortRef.current === controller) setRunning(false);
    }
    const settled = failUnresolvedRows(current);
    if (settled !== current) remember(settled);
    return { saved, aborted, rows: settled };
  }, [rememberRows]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    connectionRef.current = null;
    rememberRows([]);
    setRunning(false);
  }, [rememberRows]);

  return { rows, setRows, running, run, abort, reset };
}
