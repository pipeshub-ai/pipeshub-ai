import { App } from '@slack/bolt';
import receiver from './receiver';
import authorizeFn from './authorizeFn';

let app: App | undefined;

if (!app) {
  app = new App({
    authorize: authorizeFn, 
    signingSecret: process.env.SLACK_SIGNING_SECRET || '',
    receiver: receiver,
    socketMode: false,
  });
}

export default app!;
