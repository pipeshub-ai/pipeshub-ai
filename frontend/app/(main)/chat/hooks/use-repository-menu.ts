'use client';

import { useCallback, useEffect, useRef } from 'react';
import { useChatStore } from '@/chat/store';
import { useSandboxViews } from '@/chat/sandbox-commands';
import { chatRepositoryName, repositoryReason, type RepositoryReason } from '@/chat/repository';
import { heldRepository, useRepositoryStore } from '@/chat/repository-store';

export interface RepositoryMenuInput {
  /** The chat runs on the agent harness and is the viewer's own: only then is the picker offered. */
  enabled: boolean;
  /** The saved chat; null before its first message. */
  session: { agentId?: string; sessionId: string } | null;
  /** The agent of a new chat (null: the assistant). */
  agentId: string | null;
  agentChat: boolean;
  engineChoice?: 'choose' | 'fixed' | 'quickChat';
  /** A reply is running: when it ends, the chat's copy may have been made. */
  running: boolean;
}

export interface RepositoryMenu {
  reason: RepositoryReason | null;
  /** The repository the chat already works on (`owner/name`), saved or held. */
  repository: string | null;
  /** Reads what the reasons depend on again; called when the + menu opens or `/` is typed. */
  refresh: () => void;
}

/**
 * Whether "Work on a repository" and `/repo` can be used here (WP-26), and why not. The saved chat's repository is
 * read when the chat opens (its chip needs it) and after each reply; the engine choices only when asked.
 */
export function useRepositoryMenu({ enabled, session, agentId, agentChat, engineChoice, running }: RepositoryMenuInput): RepositoryMenu {
  const sessionId = session?.sessionId ?? null;
  const sessionAgent = session?.agentId;
  const engines = useRepositoryStore((s) => s.engines);
  const saved = useRepositoryStore((s) => (sessionId ? s.views[sessionId] : undefined));
  const held = useRepositoryStore((s) => (sessionId ? null : heldRepository(s.pending, agentId)));
  const agentEngines = useChatStore((s) => s.agentEngines);
  const heldEngine = useChatStore((s) =>
    !sessionId && s.pendingCodingEngine && s.pendingCodingEngine.agentId === agentId ? s.pendingCodingEngine.engine : null,
  );
  const chatEngine = useSandboxViews((s) => {
    if (!sessionId) return undefined;
    const known = s.views[sessionId];
    return known ? (known.view?.codingEngine ?? null) : undefined;
  });

  const load = useCallback(() => {
    if (!sessionId) return;
    const target = { ...(sessionAgent ? { agentId: sessionAgent } : {}), sessionId };
    void useRepositoryStore.getState().load(target);
  }, [sessionId, sessionAgent]);

  useEffect(() => {
    if (enabled) load();
  }, [enabled, load]);

  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running && enabled) load();
    wasRunning.current = running;
  }, [running, enabled, load]);

  const refresh = useCallback(() => {
    if (!enabled) return;
    void useRepositoryStore.getState().loadEngines();
    if (sessionId) {
      load();
      void useSandboxViews.getState().load({ ...(sessionAgent ? { agentId: sessionAgent } : {}), sessionId });
    }
  }, [enabled, load, sessionId, sessionAgent]);

  const repository = chatRepositoryName(saved, held);
  const reason = enabled
    ? repositoryReason({ engines, engineChoice, agentChat, agentEngines, chatEngine, heldEngine, repository })
    : null;
  return { reason, repository, refresh };
}
