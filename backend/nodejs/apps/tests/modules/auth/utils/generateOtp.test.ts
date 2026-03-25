import 'reflect-metadata';
import { expect } from 'chai';
import { generateOtp } from '../../../../src/modules/auth/utils/generateOtp';

describe('generateOtp', () => {
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
});
