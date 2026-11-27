import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  PlaceholderResultWithMetadata,
  processUploadsInBackground,
} from '../../../../src/modules/knowledge_base/utils/utils';
import { createHmac } from 'node:crypto';
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command';

describe('Knowledge Base Utils - kbId parameter', () => {
  let executeStub: sinon.SinonStub;
  let mockLogger: any;
  let publish: sinon.SinonStub;

  const PY_URL = 'http://localhost:8000';
  const SECRET = 'test-scoped-secret';
  const TEST_GRANT_SCOPE = { secret: SECRET, userId: 'user-1' };

  // The grant the connector verifies: claims plus an HMAC under the domain-separated key.
  const openGrant = (token: string, secret = SECRET) => {
    const [body = '', sig] = token.split('.');
    const key = createHmac('sha256', secret)
      .update('pipeshub/kb-upload-grant/v1')
      .digest();
    const want = createHmac('sha256', key).update(body).digest('base64url');
    return {
      valid: want === sig,
      claims: JSON.parse(Buffer.from(body, 'base64url').toString()),
    };
  };

  const makePlaceholder = (overrides: any = {}): PlaceholderResultWithMetadata => ({
    placeholderResult: {
      documentId: overrides.documentId || `doc-${overrides.key || 'key-1'}`,
      documentName: overrides.documentName || 'test.pdf',
    },
    metadata: {
      file: {} as any,
      filePath: overrides.filePath || '/uploads/test.pdf',
      fileName: overrides.fileName || 'test.pdf',
      extension: overrides.extension || '.pdf',
      correctMimeType: overrides.correctMimeType || 'application/pdf',
      key: overrides.key || 'key-1',
      webUrl: overrides.webUrl || 'http://storage/key-1',
      validLastModified: overrides.validLastModified || Date.now(),
      size: overrides.size || 1024,
    },
  });

  const getRequestBody = () => {
    const body = executeStub.firstCall.thisValue.body as string;
    return JSON.parse(body);
  };

  beforeEach(() => {
    executeStub = sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: { success: true },
    });

    const { Logger } = require('../../../../src/libs/services/logger.service');
    mockLogger = Logger.getInstance({ service: 'test' });
    sinon.stub(mockLogger, 'info');
    sinon.stub(mockLogger, 'error');
    sinon.stub(mockLogger, 'warn');

    publish = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('processUploadsInBackground upload grant', () => {
    it('sends a signed grant naming exactly the documents this request created', async () => {
      await processUploadsInBackground(
        [
          makePlaceholder({
            documentId: 'doc-a',
            key: 'k-a',
            filePath: 'a.pdf',
          }),
          makePlaceholder({
            documentId: 'doc-b',
            key: 'k-b',
            filePath: 'b.pdf',
          }),
        ],
        'org-9',
        'kb-9',
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );
      const body = getRequestBody();
      const { valid, claims } = openGrant(body.grant);
      expect(valid).to.equal(true);
      expect(claims).to.include({
        orgId: 'org-9',
        userId: 'user-1',
        kbId: 'kb-9',
        folderId: '',
      });
      expect(claims.documents).to.deep.equal([
        ['k-a', 'doc-a', 'application/pdf', '.pdf'],
        ['k-b', 'doc-b', 'application/pdf', '.pdf'],
      ]);
      expect(
        body.files
          .map((f: any) => [
            f.record._key,
            f.record.externalRecordId,
            f.record.mimeType,
            f.fileRecord.extension,
          ])
          .sort(),
      ).to.deep.equal(claims.documents);
      expect(claims.exp - claims.iat).to.be.at.most(600);
    });

    it('binds the destination folder for a folder upload', async () => {
      await processUploadsInBackground(
        [makePlaceholder({ documentId: 'doc-a' })],
        'org-9',
        'kb-9',
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        { ...TEST_GRANT_SCOPE, folderId: 'folder-3' },
      );
      expect(openGrant(getRequestBody().grant).claims.folderId).to.equal(
        'folder-3',
      );
    });

    it('names only the files whose storage upload succeeded', async () => {
      const failing = makePlaceholder({
        documentId: 'doc-bad',
        key: 'k-bad',
        filePath: 'bad.pdf',
      });
      failing.placeholderResult.upload = () =>
        Promise.reject(new Error('storage down'));
      await processUploadsInBackground(
        [
          failing,
          makePlaceholder({
            documentId: 'doc-ok',
            key: 'k-ok',
            filePath: 'ok.pdf',
          }),
        ],
        'org-9',
        'kb-9',
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );
      expect(openGrant(getRequestBody().grant).claims.documents).to.deep.equal([
        ['k-ok', 'doc-ok', 'application/pdf', '.pdf'],
      ]);
    });

    it('is not signed with another secret', async () => {
      await processUploadsInBackground(
        [makePlaceholder()],
        'org-9',
        'kb-9',
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );
      expect(
        openGrant(getRequestBody().grant, 'some-other-secret').valid,
      ).to.equal(false);
    });

    it('sends nothing to the connector when no grant can be signed, and fails every file', async () => {
      const counts = await processUploadsInBackground(
        [makePlaceholder()],
        'org-9',
        'kb-9',
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        { secret: '', userId: 'user-1' },
      );
      expect(executeStub.called).to.equal(false);
      expect(counts).to.deep.include({ succeeded: 0, failed: 1 });
    });
  });

  describe('processUploadsInBackground with kbId', () => {
    it('should pass kbId as connectorId in the event payload', async () => {
      const kbId = 'kb-app-uuid-123';
      const orgId = 'org-456';

      await processUploadsInBackground(
        [makePlaceholder()],
        orgId,
        kbId,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(executeStub.calledOnce).to.equal(true);
      const body = getRequestBody();
      expect(body.files[0].record.connectorId).to.equal(kbId);
      expect(body.files[0].record.orgId).to.equal(orgId);
    });

    it('should handle UUID format kbId correctly', async () => {
      const kbId = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';

      await processUploadsInBackground(
        [makePlaceholder({ key: 'file-1', documentName: 'document.docx' })],
        'org-789',
        kbId,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(executeStub.calledOnce).to.equal(true);
      const body = getRequestBody();
      expect(body.files[0].record.connectorId).to.equal(kbId);
    });

    it('should use kbId for multiple file uploads', async () => {
      const kbId = 'kb-multi-123';

      await processUploadsInBackground(
        [
          makePlaceholder({ key: 'file-1', documentName: 'file1.pdf' }),
          makePlaceholder({ key: 'file-2', documentName: 'file2.docx' }),
          makePlaceholder({ key: 'file-3', documentName: 'file3.xlsx' }),
        ],
        'org-1',
        kbId,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(executeStub.calledOnce).to.equal(true);
      const body = getRequestBody();
      expect(body.files).to.have.lengthOf(3);
      body.files.forEach((file: any) => {
        expect(file.record.connectorId).to.equal(kbId);
      });
    });

    it('should maintain kbId even when upload fails', async () => {
      const kbId = 'kb-fail-test-456';
      executeStub.resolves({ statusCode: 500, data: { error: 'Internal error' } });

      await processUploadsInBackground(
        [makePlaceholder()],
        'org-1',
        kbId,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(executeStub.calledOnce).to.equal(true);
      const body = getRequestBody();
      expect(body.files[0].record.connectorId).to.equal(kbId);
    });

    it('should correctly set connectorId in record objects', async () => {
      const kbId = 'kb-record-789';
      const orgId = 'org-123';
      const currentTime = Date.now();

      await processUploadsInBackground(
        [makePlaceholder({ key: 'rec-1', documentName: 'test.pdf', documentId: 'ext-1' })],
        orgId,
        kbId,
        currentTime,
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      const body = getRequestBody();
      const record = body.files[0].record;
      expect(record.connectorId).to.equal(kbId);
      expect(record.orgId).to.equal(orgId);
      expect(record._key).to.equal('rec-1');
    });

    it('should differentiate between different kbIds', async () => {
      const kbId1 = 'kb-app-1';
      const kbId2 = 'kb-app-2';

      await processUploadsInBackground(
        [makePlaceholder({ key: 'file-1' })],
        'org-1',
        kbId1,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(JSON.parse(executeStub.firstCall.thisValue.body).files[0].record.connectorId).to.equal(kbId1);

      executeStub.resetHistory();

      await processUploadsInBackground(
        [makePlaceholder({ key: 'file-2' })],
        'org-1',
        kbId2,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      expect(JSON.parse(executeStub.firstCall.thisValue.body).files[0].record.connectorId).to.equal(kbId2);
    });

    it('should handle legacy knowledgeBase_ prefixed kbIds (for backward compatibility)', async () => {
      const legacyKbId = 'knowledgeBase_org-123';

      await processUploadsInBackground(
        [makePlaceholder()],
        'org-123',
        legacyKbId,
        Date.now(),
        PY_URL,
        { authorization: 'Bearer token' },
        mockLogger,
        publish,
        TEST_GRANT_SCOPE,
      );

      const body = getRequestBody();
      expect(body.files[0].record.connectorId).to.equal(legacyKbId);
    });
  });
});
