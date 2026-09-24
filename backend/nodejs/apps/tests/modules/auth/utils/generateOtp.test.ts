import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import crypto from 'crypto';
import { generateOtp } from '../../../../src/modules/auth/utils/generateOtp';

const stubRandomInt = (
  value: number,
): sinon.SinonStub<[number, number], number> => {
  const stub = sinon.stub<[number, number], number>().returns(value);
  sinon.replace(
    crypto,
    'randomInt',
    stub as unknown as typeof crypto.randomInt,
  );
  return stub;
};

describe('generateOtp', () => {
  afterEach(() => {
    sinon.restore();
  });
  it('should return a string of length 6', () => {
    const otp = generateOtp();
    expect(otp).to.be.a('string');
    expect(otp).to.have.lengthOf(6);
  });

  it('should contain only digits', () => {
    const otp = generateOtp();
    expect(otp).to.match(/^\d{6}$/);
  });

  it('should generate different OTPs on successive calls (probabilistic)', () => {
    const otps = new Set<string>();
    for (let i = 0; i < 50; i++) {
      otps.add(generateOtp());
    }
    // With 6-digit OTPs and 50 calls, we should get at least a few unique values
    expect(otps.size).to.be.greaterThan(1);
  });

  it('should only contain characters from 0-9', () => {
    for (let i = 0; i < 20; i++) {
      const otp = generateOtp();
      for (const char of otp) {
        expect('0123456789').to.include(char);
      }
    }
  });

  it('should not use Math.random (GHSA-mqhm-crhq-mf45: OTP must come from a CSPRNG)', () => {
    const mathRandomSpy = sinon.spy(Math, 'random');
    generateOtp();
    expect(mathRandomSpy.called).to.be.false;
  });

  it('should draw the OTP from crypto.randomInt over the full 6-digit range', () => {
    const randomIntStub = stubRandomInt(123456);
    const otp = generateOtp();
    expect(otp).to.equal('123456');
    expect(randomIntStub.calledOnceWithExactly(0, 1_000_000)).to.be.true;
  });

  it('should zero-pad values below 100000 to 6 digits', () => {
    stubRandomInt(42);
    expect(generateOtp()).to.equal('000042');
  });

  it('should return 000000 for the minimum value', () => {
    stubRandomInt(0);
    expect(generateOtp()).to.equal('000000');
  });

  it('should return 999999 for the maximum value', () => {
    stubRandomInt(999999);
    expect(generateOtp()).to.equal('999999');
  });
});
