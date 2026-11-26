import { HarnessLogger } from './harness-log';
import {
  AuthorityAnswer,
  ContainerKind,
  CreatePublicationBody,
  KnowledgePublicationsSetting,
  PublicationAuthority,
  PublicationView,
} from '../ports/knowledge-publications';

/** The caller as the verified credential names them; never from the body. */
export interface PublicationCaller {
  orgId: string;
  authorization: string;
}

/** A refusal with the query service's (or Node's) typed code and its HTTP status. */
export class PublicationRefusedError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'PublicationRefusedError';
  }
}

export const PUBLICATIONS_DISABLED = 'publications_disabled';

/**
 * Knowledge publications at the gateway (P2.13): the org's Labs flag, then the
 * query service, which owns the graph and decides ownership again with the
 * caller's own credential. Off: every route answers 404 before anything is
 * forwarded.
 */
export class KnowledgePublicationGateway {
  constructor(
    private readonly deps: {
      setting: KnowledgePublicationsSetting;
      authority: PublicationAuthority;
      logger?: HarnessLogger;
    },
  ) {}

  async create(
    caller: PublicationCaller,
    body: CreatePublicationBody,
  ): Promise<PublicationView> {
    await this.requireEnabled(caller);
    return this.settle(
      caller,
      'create',
      await this.deps.authority.create(caller.authorization, body),
    );
  }

  async list(
    caller: PublicationCaller,
    containerKind: ContainerKind,
    containerId: string,
  ): Promise<PublicationView[]> {
    await this.requireEnabled(caller);
    return this.settle(
      caller,
      'list',
      await this.deps.authority.list(
        caller.authorization,
        containerKind,
        containerId,
      ),
    );
  }

  async revoke(
    caller: PublicationCaller,
    publicationId: string,
  ): Promise<PublicationView> {
    await this.requireEnabled(caller);
    return this.settle(
      caller,
      'revoke',
      await this.deps.authority.revoke(caller.authorization, publicationId),
    );
  }

  private async requireEnabled(caller: PublicationCaller): Promise<void> {
    if (!(await this.deps.setting.enabled(caller.orgId))) {
      throw new PublicationRefusedError(
        PUBLICATIONS_DISABLED,
        'Knowledge publications are off for this organization',
        404,
      );
    }
  }

  private settle<T>(
    caller: PublicationCaller,
    action: string,
    answer: AuthorityAnswer<T>,
  ): T {
    if (answer.ok) return answer.value;
    this.deps.logger?.warn('publications.refused', {
      orgId: caller.orgId,
      action,
      reason: answer.code,
      status: answer.status,
    });
    throw new PublicationRefusedError(
      answer.code,
      answer.message,
      answer.status,
    );
  }
}
