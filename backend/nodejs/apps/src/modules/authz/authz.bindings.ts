import { Container } from 'inversify';
import { IServiceTokenIssuer } from '../../libs/services/service-token.issuer';
import { COLLAB_TYPES } from '../enterprise_search/services/collaboration/collab.types';
import { ITeamDirectory } from '../user_management/services/team-directory.service';
import { AccessPreviewService } from './access-preview.service';
import { AuthzController } from './authz.controller';
import { ExplainService } from './explain.service';
import { IAuthorizationService, IChatAccessLoader } from './ports';
import { IProjectAccessPort } from './ports/project-access.port';
import { SubjectTeamResolver } from './subject-team.resolver';

export interface AuthzBindingDeps {
  authz: IAuthorizationService;
  chats: IChatAccessLoader;
  projects: IProjectAccessPort;
  teams: ITeamDirectory;
  tokens: IServiceTokenIssuer;
}

/** Binds the controller behind the user-facing `/api/v1/authz` routes. */
export function bindAuthzUserRoutes(
  container: Container,
  deps: AuthzBindingDeps,
): void {
  container
    .bind<AuthzController>(COLLAB_TYPES.AuthzController)
    .toConstantValue(
      new AuthzController(
        new ExplainService(deps.authz, deps.chats),
        new AccessPreviewService(deps.chats, deps.projects),
        new SubjectTeamResolver(deps.teams, deps.tokens),
      ),
    );
}
