/**
 * Why "Work on a repository" is greyed (WP-26 GIT-FLOW), decided the way `/engine` resolves a chat's engine.
 */
import { repositoryName, type ChatRepositoryView } from './repository-api';
import { ENGINE_NONE, myDefaultEngine } from './sandbox-commands';
import type { PendingRepository } from './repository-store';
import type { CodingEngineView } from '@/app/(main)/workspace/ai-accounts/types';

/** Why the picker cannot be used here. */
export type RepositoryReason = 'orgOff' | 'noEngine' | 'hasRepository';

export interface RepositoryEngineInput {
  engines: CodingEngineView | null;
  /** `quickChat`: the assistant outside Agent mode, where no engine runs. */
  engineChoice?: 'choose' | 'fixed' | 'quickChat';
  agentChat: boolean;
  /** The engines the agent's profile lists. */
  agentEngines: readonly string[];
  /** The saved chat's own engine (`GET …/sandbox`): a kind, `none`, null for "not set", undefined when unknown. */
  chatEngine?: string | null;
  /** The engine `/engine` held for this new chat. */
  heldEngine?: string | null;
}

/** Whether this chat's next turn has a coding engine, the way `/engine` resolves it; undefined when not known yet. */
export function chatHasEngine(input: RepositoryEngineInput): boolean | undefined {
  if (input.engineChoice === 'quickChat') return false;
  if (input.agentChat) return input.agentEngines.length > 0;
  const own = input.chatEngine ?? input.heldEngine ?? null;
  if (own !== null) return own !== ENGINE_NONE;
  if (!input.engines) return undefined;
  const fallback = myDefaultEngine(input.engines);
  return fallback !== ENGINE_NONE && input.engines.engines.some((e) => e.kind === fallback && e.available);
}

/** Why "Work on a repository" is greyed here, or null when it can be used (or is not known to be unusable). */
export function repositoryReason(input: RepositoryEngineInput & { repository: string | null }): RepositoryReason | null {
  if (input.repository) return 'hasRepository';
  if (input.engines && !input.engines.engines.some((e) => e.available)) return 'orgOff';
  return chatHasEngine(input) === false ? 'noEngine' : null;
}

/** The repository a chat already works on, as `owner/name`: its saved one, else the one held for its first message. */
export function chatRepositoryName(view: ChatRepositoryView | null | undefined, held: PendingRepository | null): string | null {
  if (view) return repositoryName(view.url);
  return held ? held.fullName : null;
}
