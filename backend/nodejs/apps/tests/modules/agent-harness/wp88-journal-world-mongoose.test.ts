import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { connectMongoWorld } from './support/journal-mongo-world';

/**
 * WP88-PF05-2 (C7.04): PF-05's journal server is Node on MongoDB as the app runs it. The app's Mongo service
 * sets Mongoose's process-wide options (strict queries; PERFD-11's stack trace is gone in Mongoose 9); the rig's server
 * connected without them, so the gate measured a Node the app is not.
 */
describe('WP88-PF05-2: the journal server connects to MongoDB with the app\'s Mongoose settings', () => {
  afterEach(() => sinon.restore());

  it('applies them before it connects', async () => {
    mongoose.set('strictQuery', false);
    let seen: { strict: unknown } | undefined;
    sinon.stub(mongoose, 'connect').callsFake(async () => {
      seen = { strict: mongoose.get('strictQuery') };
      throw new Error('no store in a unit test');
    });
    let failed = false;
    try {
      await connectMongoWorld('mongodb://unused.invalid:1', 'pxh_unused');
    } catch {
      failed = true;
    }
    expect(failed).to.equal(true);
    expect(seen).to.deep.equal({ strict: true });
  });
});
