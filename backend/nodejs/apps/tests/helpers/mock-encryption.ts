import sinon from 'sinon';

export interface MockEncryptionService {
  encrypt: sinon.SinonStub;
  decrypt: sinon.SinonStub;
}

export function createMockEncryptionService(): MockEncryptionService {
  return {
    encrypt: sinon.stub().returns({
      encryptedData: 'mock-encrypted-data',
      iv: 'mock-iv',
      tag: 'mock-tag',
    }),
    decrypt: sinon.stub().returns('mock-decrypted-data'),
  };
}
