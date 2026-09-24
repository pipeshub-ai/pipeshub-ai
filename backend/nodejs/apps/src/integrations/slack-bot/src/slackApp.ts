import { App, type Authorize, type Receiver } from '@slack/bolt';
import receiver from './receiver';
import authorizeFn from './authorizeFn';

export function createSlackApp(customReceiver: Receiver, customAuthorizeFn: Authorize<boolean>): App {
  return new App({
    authorize: customAuthorizeFn,
    receiver: customReceiver,
    socketMode: false,
  });
}

let app: App | undefined;

if (!app) {
  app = createSlackApp(receiver, authorizeFn);
}

export default app!;
