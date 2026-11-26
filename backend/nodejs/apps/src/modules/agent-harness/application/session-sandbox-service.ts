import { HarnessNotFoundError } from '../domain/errors';
import type { ApprovalLevel } from '../contracts/generated/core/enums';
import { normalizeRules, stricter } from '../domain/policy/approval-rules';
import type { SignInKeep } from '../domain/engine-signin';
import type { DeploymentCapabilities } from '../domain/capabilities';
import {
  ChatSessionRepository,
  SessionExecutionScope,
} from '../ports/chat-session-repository';
import type { WorkspaceService } from './workspace-service';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** The agent's code tool, as approval rules name it (ASKCODE). */
export const RUN_CODE_TOOL_ID = 'pipeshub@sandbox/run_code';
/** Python's `app.harness.sandbox.images.DEFAULT_SANDBOX_IMAGE`: read from the same variable on both sides. */
export const DEFAULT_SANDBOX_IMAGE = 'pipeshub/sandbox:latest';

/**
 * Every durable run pins `grants.egress: none` (`run-spec.ts`, P5.11), so its code has no network whatever
 * `SANDBOX_ALLOW_NETWORK` says; a pin that granted egress would make each code run ask (`writes: ask`).
 */
const CODE_NETWORK = false;

/** What `/sandbox` shows (SLASH3 §3.6); no sandbox is started to answer it. */
export interface SessionSandboxView {
  /** `run_code`'s image and the broker's isolation; null when no broker was probed. */
  image: {
    name: string;
    isolation: DeploymentCapabilities['sandbox.isolation'] | null;
  };
  /** Whether `run_code`'s sandbox may reach public hosts (through the egress proxy). */
  network: boolean;
  /**
   * How a code run is decided before it starts: allow (own sandbox, CODEAUTO), ask (an ASKCODE rule, or
   * network: `writes: ask`), deny (a rule blocks it). The worker decides again on every call.
   */
  codeRuns: ApprovalLevel;
  workspace: { revision: number; files: number; bytes: number };
  /** A Claude sign-in kept between messages (SIGNIN-5): only where the owner is signed in. */
  keptSignIn: {
    idleMinutes: number;
    maxHours: number;
    signedInAt?: string;
  } | null;
  /** This chat's own coding engine from its next message (SLASH3 `/engine`): a kind, `none`, or null (not set). */
  codingEngine: string | null;
}

export interface SessionSandboxOptions {
  sessions: Pick<ChatSessionRepository, 'findStreamAccess'>;
  workspaces: Pick<WorkspaceService, 'sessionFiles'>;
  /** The approval rules in force (ASKCODE stores `tools[run_code]` there; `ApprovalRulesReader`); absent: none. */
  approvals?: {
    read(orgId: string): Promise<{
      policy: { org: unknown; agents: Record<string, unknown> };
      deployment: { default: unknown };
    }>;
  };
  isolation?: () => DeploymentCapabilities['sandbox.isolation'] | null;
  codingEngines?: {
    sessionChoice(orgId: string, sessionId: string): Promise<string | null>;
  };
  signIns?: {
    view(
      orgId: string,
      userId: string,
    ): Promise<{
      claude: { state: string; keep: SignInKeep; signedInAt?: string };
    }>;
    endSession(orgId: string, sessionId: string): Promise<void>;
  };
  env?: Readonly<Record<string, string | undefined>>;
  logger?: HarnessLogger;
}

/**
 * `/sandbox` and its "End now" (SLASH3 SB1): what runs the chat's code,
 * composed from what Node already holds, and the end of a kept sign-in
 * sandbox through the same per-session revocation as sharing. Owner only.
 */
export class SessionSandboxService {
  private readonly logger: HarnessLogger;
  private readonly env: Readonly<Record<string, string | undefined>>;

  constructor(private readonly options: SessionSandboxOptions) {
    this.logger = options.logger ?? silentHarnessLogger;
    this.env = options.env ?? process.env;
  }

  async view(scope: SessionExecutionScope): Promise<SessionSandboxView> {
    await this.owner(scope);
    const [head, codeRuns, kept, codingEngine] = await Promise.all([
      this.options.workspaces.sessionFiles(scope),
      this.codeRuns(scope),
      this.kept(scope),
      this.options.codingEngines?.sessionChoice(scope.orgId, scope.sessionId) ??
        Promise.resolve(null),
    ]);
    const image = (this.env['SANDBOX_DOCKER_IMAGE'] ?? '').trim();
    return {
      image: {
        name: image !== '' ? image : DEFAULT_SANDBOX_IMAGE,
        isolation: this.options.isolation?.() ?? null,
      },
      network: CODE_NETWORK,
      codeRuns,
      workspace: {
        revision: head.revision,
        files: head.files.length,
        bytes: head.files.reduce((sum, file) => sum + file.sizeBytes, 0),
      },
      keptSignIn: kept,
      codingEngine,
    };
  }

  /** "End now": logs the sign-in out, wipes and destroys the kept sandbox; the next message signs in again. */
  async endKept(scope: SessionExecutionScope): Promise<void> {
    await this.owner(scope);
    await this.options.signIns?.endSession(scope.orgId, scope.sessionId);
    this.logger.info(
      'signin.kept_ended',
      harnessLogMeta('signin.kept_ended', {
        orgId: scope.orgId,
        sessionId: scope.sessionId,
        actorId: scope.actorId,
      }),
    );
  }

  private async codeRuns(scope: SessionExecutionScope): Promise<ApprovalLevel> {
    let level: ApprovalLevel = 'allow';
    if (this.options.approvals === undefined) return level;
    const view = await this.options.approvals.read(scope.orgId);
    const layers = [
      view.deployment.default,
      view.policy.org,
      scope.agentKey !== undefined
        ? view.policy.agents[scope.agentKey]
        : undefined,
    ];
    for (const layer of layers) {
      const rule = normalizeRules(layer)?.tools[RUN_CODE_TOOL_ID];
      if (rule !== undefined) level = stricter(level, rule);
    }
    return level;
  }

  private async kept(
    scope: SessionExecutionScope,
  ): Promise<SessionSandboxView['keptSignIn']> {
    if (this.options.signIns === undefined) return null;
    const { claude } = await this.options.signIns.view(
      scope.orgId,
      scope.actorId,
    );
    if (claude.state !== 'connected') return null;
    return {
      idleMinutes: claude.keep.idleMinutes,
      maxHours: claude.keep.maxHours,
      ...(claude.signedInAt !== undefined
        ? { signedInAt: claude.signedInAt }
        : {}),
    };
  }

  private async owner(scope: SessionExecutionScope): Promise<void> {
    const access = await this.options.sessions.findStreamAccess(scope);
    if (access?.audience !== 'owner') throw new HarnessNotFoundError();
  }
}
