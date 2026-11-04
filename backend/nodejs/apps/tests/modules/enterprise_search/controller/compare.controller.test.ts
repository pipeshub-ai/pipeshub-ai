import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { asAccount } from '../../../../src/libs/context/orgContext';
import {
  setConversationRepository,
  type ChatSessionDto,
  type ConversationRepository,
} from '../../../../src/libs/repositories/conversation';
import { ConflictError } from '../../../../src/libs/errors/http.errors';
import * as drain from '../../../../src/libs/repositories/drain';
import * as fork from '../../../../src/modules/enterprise_search/utils/fork-conversation';
import * as forkArtifacts from '../../../../src/modules/enterprise_search/utils/fork-artifacts';
import { CONVERSATION_STATUS } from '../../../../src/modules/enterprise_search/constants/constants';
import {
  chooseCompare,
  createCompareBranchHandler,
  listCompareBranches,
  purgeBranchArtifacts,
} from '../../../../src/modules/enterprise_search/controller/compare.controller';

const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const USER = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const PARENT = 'cccccccccccccccccccccccc';
const BRANCH = 'dddddddddddddddddddddddd';
const MSG = 'eeeeeeeeeeeeeeeeeeeeeeee';
const me = { userId: USER, orgId: ORG };

interface Result {
  status?: number;
  body?: unknown;
  error?: { statusCode?: number; name?: string };
}

const parent = (extra: Partial<ChatSessionDto> = {}): ChatSessionDto =>
  ({
    _id: PARENT,
    orgId: ORG,
    userId: USER,
    status: CONVERSATION_STATUS.COMPLETE,
    ...extra,
  }) as ChatSessionDto;

const branch = (extra: Partial<ChatSessionDto> = {}): ChatSessionDto =>
  ({
    _id: BRANCH,
    orgId: ORG,
    userId: USER,
    compareOf: PARENT,
    compareFromMessageId: MSG,
    metadata: { compareFromSeq: 3 },
    status: CONVERSATION_STATUS.COMPLETE,
    ...extra,
  }) as ChatSessionDto;

const call = async (
  handler: (req: any, res: any, next: any) => Promise<void>,
  params: Record<string, string>,
  body: Record<string, unknown> = {},
): Promise<Result> => {
  const out: Result = {};
  const res = {
    status(code: number) {
      out.status = code;
      return { json: (b: unknown) => (out.body = b) };
    },
  };
  await asAccount(ORG, USER, 'test', () =>
    handler(
      { user: me, params, body, context: { requestId: 'r' }, headers: {} },
      res,
      (e: any) => (out.error = e),
    ),
  );
  return out;
};

describe('compare.controller', () => {
  let repo: { [K in keyof ConversationRepository]?: sinon.SinonStub };

  beforeEach(() => {
    repo = {
      findOwned: sinon.stub().resolves(parent()),
      findMessage: sinon.stub().resolves({
        _id: MSG,
        messageType: 'user_query',
        seq: 3,
      }),
      listBranches: sinon.stub().resolves([]),
      listStaleBranches: sinon.stub().resolves([]),
      discardBranches: sinon.stub().resolves(),
      listMessages: sinon.stub().resolves([]),
      countMessages: sinon.stub().resolves(4),
      reserveRun: sinon.stub().resolves({
        sessionId: PARENT,
        runId: 'run-1',
        previousStatus: CONVERSATION_STATUS.COMPLETE,
      }),
      releaseRun: sinon.stub().resolves(),
      recordRunState: sinon.stub().resolves(parent()),
    };
    setConversationRepository(repo as unknown as ConversationRepository);
    sinon.stub(drain, 'refuseIfDraining').resolves(false);
    sinon.stub(fork, 'createCompareBranch').resolves({
      conversation: branch(),
    } as never);
    sinon.stub(fork, 'replaceTurnWithBranch').resolves();
    sinon.stub(fork, 'messagesUpTo').returns(
      (async function* () {
        yield [{}, {}];
      })() as never,
    );
    sinon.stub(fork, 'copyBranchTurnArtifacts').resolves({
      copies: {},
      failed: false,
    } as never);
    sinon.stub(forkArtifacts, 'collectArtifactRefs').returns([]);
  });

  afterEach(() => {
    setConversationRepository();
    sinon.restore();
  });

  it('lists branches of the caller chat and 404s an unknown one', async () => {
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    const listed = await call(listCompareBranches(), {
      conversationId: PARENT,
    });
    expect(listed.status).to.equal(200);
    expect(listed.body).to.deep.equal({
      branches: [{ branchConversationId: BRANCH, userMessageId: MSG }],
    });

    (repo.findOwned as sinon.SinonStub).resolves(null);
    const missing = await call(listCompareBranches(), {
      conversationId: PARENT,
    });
    expect(missing.error?.statusCode).to.equal(404);
  });

  it('refuses Compare on an agent chat', async () => {
    (repo.findOwned as sinon.SinonStub).callsFake(async (scope: { kind: string }) =>
      scope.kind === 'agent'
        ? parent({ sessionType: 'agent' } as never)
        : null,
    );
    const out = await call(listCompareBranches(), { conversationId: PARENT });
    expect(out.error?.statusCode).to.equal(400);
  });

  it('creates a branch, sweeps stale ones, and keeps a live sibling', async () => {
    const live = branch({
      _id: 'ffffffffffffffffffffffff',
      status: CONVERSATION_STATUS.INPROGRESS,
      lastActivityAt: Date.now(),
    });
    const stale = branch({
      _id: '111111111111111111111111',
      compareOf: PARENT,
    });
    (repo.listBranches as sinon.SinonStub)
      .onFirstCall()
      .resolves([live, branch({ _id: '222222222222222222222222' })])
      .onSecondCall()
      .resolves([branch()]);
    (repo.listStaleBranches as sinon.SinonStub).resolves([
      stale,
      { ...stale, compareOf: undefined } as ChatSessionDto,
    ]);
    const remove = sinon.stub().resolves();
    const out = await call(
      createCompareBranchHandler({
        copier: () => ({} as never),
        remove,
        removeOne: sinon.stub().resolves(),
      }),
      { conversationId: PARENT },
      { userMessageId: MSG },
    );
    expect(out.status).to.equal(200);
    expect(out.body).to.deep.equal({ branchConversationId: BRANCH });
    expect((repo.discardBranches as sinon.SinonStub).called).to.equal(true);
    expect(remove.called).to.equal(true);
  });

  it('drops a raced extra branch and reports the collision', async () => {
    (repo.listBranches as sinon.SinonStub)
      .onFirstCall()
      .resolves([])
      .onSecondCall()
      .resolves([
        branch({ _id: '333333333333333333333333' }),
        branch(),
      ]);
    const out = await call(
      createCompareBranchHandler(),
      { conversationId: PARENT },
      { userMessageId: MSG },
    );
    expect(out.error?.name).to.equal('ConflictError');
    expect(
      (repo.discardBranches as sinon.SinonStub).calledWith(
        ORG,
        PARENT,
        [BRANCH],
      ),
    ).to.equal(true);
  });

  it('404s a missing question on branch create', async () => {
    (repo.findMessage as sinon.SinonStub).resolves(null);
    const out = await call(
      createCompareBranchHandler(),
      { conversationId: PARENT },
      { userMessageId: MSG },
    );
    expect(out.error?.statusCode).to.equal(404);
  });

  it('chooses primary and treats an empty retry as already chosen', async () => {
    const remove = sinon.stub().resolves();
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    const primary = await call(
      chooseCompare({
        copier: () => ({} as never),
        remove,
        removeOne: sinon.stub().resolves(),
      }),
      { conversationId: PARENT },
      { choice: 'PRIMARY' },
    );
    expect(primary.status).to.equal(200);
    expect(primary.body).to.deep.equal({ ok: true });

    (repo.listBranches as sinon.SinonStub).resolves([]);
    const empty = await call(
      chooseCompare(),
      { conversationId: PARENT },
      { choice: BRANCH },
    );
    expect(empty.status).to.equal(200);
    expect(empty.body).to.deep.equal({ ok: true, already: true });
  });

  it('404s an unknown branch and 409s a branch with no answer yet', async () => {
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    const missing = await call(
      chooseCompare(),
      { conversationId: PARENT },
      { choice: '999999999999999999999999' },
    );
    expect(missing.error?.statusCode).to.equal(404);

    (repo.countMessages as sinon.SinonStub).resolves(2);
    const early = await call(
      chooseCompare(),
      { conversationId: PARENT },
      { choice: BRANCH },
    );
    expect(early.error?.name).to.equal('ConflictError');
  });

  it('keeps the chosen branch turn and releases the run locks', async () => {
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    (repo.listMessages as sinon.SinonStub)
      .onFirstCall()
      .resolves([{ dto: { content: 'kept' } }])
      .onSecondCall()
      .resolves([{ dto: { content: 'dropped' } }]);
    (forkArtifacts.collectArtifactRefs as sinon.SinonStub)
      .onFirstCall()
      .returns([{ artifactId: 'keep-a' }])
      .onSecondCall()
      .returns([{ artifactId: 'drop-a' }, { artifactId: 'keep-a' }]);
    const remove = sinon.stub().resolves();
    const removeOne = sinon.stub().rejects(new Error('blob gone'));
    const out = await call(
      chooseCompare({
        copier: () => ({} as never),
        remove,
        removeOne,
      }),
      { conversationId: PARENT },
      { choice: BRANCH },
    );
    expect(out.status).to.equal(200);
    expect(out.body).to.deep.equal({ ok: true });
    expect((fork.replaceTurnWithBranch as sinon.SinonStub).called).to.equal(
      true,
    );
    expect((repo.releaseRun as sinon.SinonStub).called).to.equal(true);
    expect(removeOne.calledWith('drop-a')).to.equal(true);
  });

  it('says the other answer is still running when its reserve conflicts', async () => {
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    (repo.reserveRun as sinon.SinonStub).rejects(
      new ConflictError('already running'),
    );
    const out = await call(
      chooseCompare(),
      { conversationId: PARENT },
      { choice: BRANCH },
    );
    expect(out.error?.name).to.equal('ConflictError');
    expect(String(out.error)).to.include('still being answered');
  });

  it('purges branch artifacts before delete, and still continues when a blob is gone', async () => {
    (repo.listBranches as sinon.SinonStub).resolves([branch()]);
    const remove = sinon.stub().rejects(new Error('already gone'));
    const next = sinon.stub();
    await asAccount(ORG, USER, 'test', () =>
      purgeBranchArtifacts({
        copier: () => ({} as never),
        remove,
        removeOne: sinon.stub().resolves(),
      })(
        {
          user: me,
          params: { conversationId: PARENT },
        } as never,
        {} as never,
        next,
      ),
    );
    expect(next.calledOnce).to.equal(true);
    expect(next.firstCall.args[0]).to.equal(undefined);

    const skipped = sinon.stub();
    await asAccount(ORG, USER, 'test', () =>
      purgeBranchArtifacts({
        copier: () => ({} as never),
        remove: sinon.stub().resolves(),
        removeOne: sinon.stub().resolves(),
      })(
        { user: me, params: { conversationId: 'not-an-id' } } as never,
        {} as never,
        skipped,
      ),
    );
    expect((repo.listBranches as sinon.SinonStub).calledOnce).to.equal(true);
    expect(skipped.calledOnce).to.equal(true);
  });

  it('forwards a listBranches failure from the purge guard', async () => {
    const boom = new Error('mongo down');
    (repo.listBranches as sinon.SinonStub).rejects(boom);
    const next = sinon.stub();
    await asAccount(ORG, USER, 'test', () =>
      purgeBranchArtifacts({
        copier: () => ({} as never),
        remove: sinon.stub().resolves(),
        removeOne: sinon.stub().resolves(),
      })(
        { user: me, params: { conversationId: PARENT } } as never,
        {} as never,
        next,
      ),
    );
    expect(next.calledOnceWithExactly(boom)).to.equal(true);
  });
});
