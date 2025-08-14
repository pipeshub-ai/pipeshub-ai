import { ExpressReceiver } from '@slack/bolt';

const receiver = new ExpressReceiver({
  signingSecret: process.env.SLACK_SIGNING_SECRET || '',
});

export default receiver;
