import { KnowledgePublicationGateway } from '../application/knowledge-publications';
import { HarnessBinder } from './context';
import { AGENT_HARNESS_PUBLICATIONS } from './tokens';

export const bindPublications: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  const publications = dependencies.publications;
  if (
    publications === undefined ||
    container.isBound(AGENT_HARNESS_PUBLICATIONS)
  ) {
    return;
  }
  container
    .bind<KnowledgePublicationGateway>(AGENT_HARNESS_PUBLICATIONS)
    .toConstantValue(
      new KnowledgePublicationGateway({
        ...publications,
        ...(logger !== undefined ? { logger } : {}),
      }),
    );
};
