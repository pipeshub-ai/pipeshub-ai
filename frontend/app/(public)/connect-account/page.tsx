import { ConnectAccountClient } from './connect-account-client';

/**
 * Landing page of an emailed "connect your account" link (`/connect-account?token=…`, optionally `&optOut=1`).
 * No login: the link token is the credential.
 */
export default function ConnectAccountPage() {
  return <ConnectAccountClient />;
}
