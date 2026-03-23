import sinon from 'sinon';

export interface MockStorageProvider {
  uploadDocument: sinon.SinonStub;
  getBuffer: sinon.SinonStub;
  getSignedUrl: sinon.SinonStub;
  deleteDocument: sinon.SinonStub;
  listDocuments: sinon.SinonStub;
  documentExists: sinon.SinonStub;
  getMetadata: sinon.SinonStub;
}

export function createMockStorageProvider(): MockStorageProvider {
  return {
    uploadDocument: sinon.stub().resolves({ key: 'mock-key', url: 'https://mock-url.com/file' }),
    getBuffer: sinon.stub().resolves(Buffer.from('mock-file-content')),
    getSignedUrl: sinon.stub().resolves('https://mock-signed-url.com/file'),
    deleteDocument: sinon.stub().resolves(),
    listDocuments: sinon.stub().resolves([]),
    documentExists: sinon.stub().resolves(true),
    getMetadata: sinon.stub().resolves({ contentType: 'application/pdf', size: 1024 }),
  };
}
