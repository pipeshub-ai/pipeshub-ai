import { HttpSandboxReaper } from '../adapters/http/http-sandbox-reaper';
import { HarnessAuthority } from '../application/harness-authority';
import { SandboxFolderEraser, SandboxReaper } from '../ports/sandbox-reaper';
import { HarnessBinder } from './context';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_SANDBOX_REAPER,
} from './tokens';

export const bindSandboxReaper: HarnessBinder = ({
  container,
  dependencies,
}) => {
  const brokerEndpoint = dependencies.sandboxBrokerEndpoint;
  if (
    brokerEndpoint === undefined ||
    container.isBound(AGENT_HARNESS_SANDBOX_REAPER)
  ) {
    return;
  }
  container
    .bind<SandboxReaper & SandboxFolderEraser>(AGENT_HARNESS_SANDBOX_REAPER)
    .toConstantValue(
      new HttpSandboxReaper(brokerEndpoint, () =>
        container
          .get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
          .workerCredential(),
      ),
    );
};
