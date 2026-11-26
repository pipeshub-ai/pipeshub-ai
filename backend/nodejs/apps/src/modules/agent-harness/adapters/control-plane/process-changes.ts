import { kickOutbox } from '../../../../libs/services/outbox/outbox.dispatcher';
import { ChangeSubscribers, createChangeOutbox } from '../../../control-plane';

/**
 * One per process. Every control-plane transaction of this process, whichever
 * module's container built it, enqueues through `processChangeOutbox`; once
 * the write commits, this process's holders hear the event before the write
 * answers (read-your-writes, 03-interfaces §4.5) and the outbox dispatcher is
 * woken so other processes hear it without waiting out its poll (NFR-48).
 */
export const controlPlaneSubscribers = new ChangeSubscribers();

export const processChangeOutbox = createChangeOutbox({
  announce: (event) => {
    controlPlaneSubscribers.deliver(event);
    kickOutbox();
  },
});
