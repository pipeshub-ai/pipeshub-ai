import type { AgentDirectory } from '../../control-plane';
import { HarnessRunConfigUnavailableError } from '../domain/errors';
import { resolvedTurnClaims } from '../domain/resolved-turn';
import type { ResolvedRoute } from '../ports/routing-decider';
import type {
  LegacyTurn,
  LegacyTurnResolution,
  LegacyTurnResolver,
} from './legacy-turn-resolver';
import type { ResolvedTurnAuthority } from './resolved-turn-authority';

/** A legacy turn's header but for the conversation, which its handler creates or reads after it is admitted here. */
export interface PreparedTurn {
  turn: LegacyTurn;
  resolution: LegacyTurnResolution;
  /** The agent directory holds a current "may run" for the actor. */
  mayRun: boolean;
}

export interface LegacyTurnHeaderOptions {
  resolver: Pick<LegacyTurnResolver, 'resolve'>;
  /** Null while the deployment holds no gateway keyring: under `on` no header can be made, so the turn is refused. */
  authority: () => ResolvedTurnAuthority | null;
  /** The cached agent directory (`CONTROL_PLANE_AGENT_DIRECTORY`), when one is bound. */
  directory?: () => Pick<AgentDirectory, 'authorizeRun'> | null;
  /**
   * Whether the harness is on for the org (ADR-OPS-08); absent: on. Where it is off the turn gets no header, as a
   * release without the harness sent none, and so no `RUN_CONFIG_UNAVAILABLE` either.
   */
  harnessOn?: (orgId: string) => Promise<boolean>;
}

/**
 * `X-PipesHub-Resolved` for one legacy chat turn (CPD-10 §3), bound only
 * under `CONTROL_PLANE_PIN=on`. Two steps
 * because a handler knows its conversation only after it has answered the
 * client: `prepare` pins the turn before anything is written, so a turn that
 * cannot be pinned is refused with a status; `sign` binds the pin to the
 * conversation and signs it.
 */
export class LegacyTurnHeader {
  constructor(private readonly options: LegacyTurnHeaderOptions) {}

  /**
   * Null when no header is sent: the harness is off for the org, or the agent directory denies the actor the
   * agent (the query service then refuses the request as it does today, on
   * its own check). Rejects with
   * `RUN_CONFIG_UNAVAILABLE` when the turn cannot be pinned or signed.
   */
  async prepare(
    turn: LegacyTurn,
    routed?: ResolvedRoute,
  ): Promise<PreparedTurn | null> {
    if (
      this.options.harnessOn !== undefined &&
      !(await this.options.harnessOn(turn.orgId))
    ) {
      return null;
    }
    this.authority();
    const mayRun = await this.mayRun(turn);
    if (mayRun === 'denied') return null;
    const resolution = await this.options.resolver.resolve(turn, routed);
    return { turn, resolution, mayRun: mayRun === 'granted' };
  }

  async sign(prepared: PreparedTurn, conversationId: string): Promise<string> {
    const authority = this.authority();
    try {
      return await authority.header(
        resolvedTurnClaims(prepared.resolution.pin, {
          orgId: prepared.turn.orgId,
          actorId: prepared.turn.actorId,
          agentKey: prepared.turn.agentKey,
          conversationId,
          issuedAt: authority.issuedAt(),
          routing: prepared.resolution.routing,
          mayRun: prepared.mayRun,
          unreadableApprovals: prepared.resolution.unreadableApprovals,
        }),
      );
    } catch {
      throw new HarnessRunConfigUnavailableError(
        'The resolved turn could not be signed',
      );
    }
  }

  private authority(): ResolvedTurnAuthority {
    const authority = this.options.authority();
    if (authority === null) {
      throw new HarnessRunConfigUnavailableError(
        'No signing keys for the resolved turn',
      );
    }
    return authority;
  }

  /** `unknown`: no directory, the assistant, or a directory that cannot be reached; the query service checks. */
  private async mayRun(
    turn: LegacyTurn,
  ): Promise<'granted' | 'denied' | 'unknown'> {
    const directory = this.options.directory?.() ?? null;
    if (directory === null || turn.agentKey === null) return 'unknown';
    try {
      const access = await directory.authorizeRun(turn.orgId, turn.agentKey, {
        orgId: turn.orgId,
        userId: turn.actorId,
      });
      return access === null ? 'denied' : 'granted';
    } catch {
      return 'unknown';
    }
  }
}
