import 'reflect-metadata';
import { expect } from 'chai';
import { demoDataPreferenceSchema } from '../../../../src/modules/knowledge_base/validators/validators';

describe('demoDataPreferenceSchema', () => {
  it('accepts showing, hiding, and going back to the default', () => {
    for (const include of [true, false, null]) {
      expect(demoDataPreferenceSchema.safeParse({ body: { include } }).success).to.equal(true);
    }
  });

  it('refuses anything but the one switch', () => {
    expect(demoDataPreferenceSchema.safeParse({ body: {} }).success).to.equal(false);
    expect(demoDataPreferenceSchema.safeParse({ body: { include: 'yes' } }).success).to.equal(false);
    // A person's own switch only: no field to set it for someone else.
    expect(demoDataPreferenceSchema.safeParse({ body: { include: false, userId: 'u2' } }).success).to.equal(false);
  });
});
