import { ExpressReceiver } from '@slack/bolt';

const signingSecret = process.env.SLACK_SIGNING_SECRET;
if (!signingSecret) {
  throw new Error('SLACK_SIGNING_SECRET is not set in environment variables.');
}
const receiver = new ExpressReceiver({
  signingSecret,
});

export default receiver;
