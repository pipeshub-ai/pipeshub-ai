import {
  CONTROL_PLANE_BINDINGS,
  createBindingService,
  type BindingService,
} from '../../control-plane';
import { HarnessBinder } from './context';

/**
 * CPD-8: the control plane's credential bindings ("My connections", the
 * migration's and the token refresher's internal routes), over the
 * deployment's control-plane transaction and the credential vault's KV store.
 */
export const bindCredentialBindings: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
}) => {
  const vault = dependencies.credentialBindings;
  if (vault === undefined || container.isBound(CONTROL_PLANE_BINDINGS)) return;
  container
    .bind<BindingService>(CONTROL_PLANE_BINDINGS)
    .toConstantValue(
      createBindingService({ ...vault, transaction: controlPlane }),
    );
};
