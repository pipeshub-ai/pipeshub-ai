import { expect } from 'chai';
import { storedError } from '../../../src/libs/services/pg-queue.service';

describe('storedError', () => {
  it('redacts URL userinfo, password pairs and bearer tokens', () => {
    const out = storedError(
      'connect postgresql://app:hunter2pw@db:5432/x failed password=hunter2pw Bearer abc.def.ghi',
    );
    expect(out).to.not.include('hunter2pw');
    expect(out).to.not.include('abc.def.ghi');
  });

  it('cuts the text to 500 characters', () => {
    expect(storedError('x'.repeat(5000))).to.have.length(500);
  });

  it('does not leave half a secret at the cut', () => {
    expect(storedError(`${'a'.repeat(495)} password=hunter2pw`)).to.not.include(
      'hunter2',
    );
  });
});
