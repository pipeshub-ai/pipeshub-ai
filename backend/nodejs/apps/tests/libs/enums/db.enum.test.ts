import { expect } from 'chai';
import { ARANGO_DB_NAME, MONGO_DB_NAME } from '../../../src/libs/enums/db.enum';

describe('db.enum', () => {
  describe('ARANGO_DB_NAME', () => {
    it('should equal "es"', () => {
      expect(ARANGO_DB_NAME).to.equal('es');
    });

    it('should be a string', () => {
      expect(ARANGO_DB_NAME).to.be.a('string');
    });
  });

  describe('MONGO_DB_NAME', () => {
    it('should equal "es"', () => {
      expect(MONGO_DB_NAME).to.equal('es');
    });

    it('should be a string', () => {
      expect(MONGO_DB_NAME).to.be.a('string');
    });
  });
});
