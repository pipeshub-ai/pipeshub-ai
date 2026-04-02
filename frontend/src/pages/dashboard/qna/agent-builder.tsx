import { Helmet } from 'react-helmet-async';
import { useParams, useNavigate } from 'react-router-dom';

import { UserProvider } from 'src/context/UserContext';
import { AuthProvider } from 'src/auth/context/jwt';

import { AgentBuilder } from 'src/sections/qna/agents';
import { Agent } from 'src/types/agent';
import { paths } from 'src/routes/paths';

// ----------------------------------------------------------------------

const metadata = { title: `Flow Agent Builder` };

export default function Page() {
  const { agentKey } = useParams<{ agentKey?: string }>();
  const navigate = useNavigate();

  const isEditMode = Boolean(agentKey);

  const handleSuccess = (agent: any) => {
    if (!agent?._key) {
      navigate('/agents');
      return;
    }

    // Updating an existing agent → always go to chat so the user can test changes.
    if (isEditMode) {
      navigate(`/agents/${agent._key}`);
      return;
    }

    // Creating a new agent:
    //   • Service account → stay in the builder so the user can immediately
    //     configure per-toolset agent credentials before chatting.
    //     (The ServiceAccountConfirmDialog already does this for the quick-save
    //     path; this guards any other create path that may produce a svc account.)
    if (agent.isServiceAccount) {
      navigate(paths.dashboard.agent.edit(agent._key), {
        state: { serviceAccountJustCreated: true },
      });
      return;
    }

    // Regular new agent → go straight to the chat view.
    navigate(`/agents/${agent._key}`);
  };

  const handleClose = () => {
    navigate('/agents');
  };

  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <AuthProvider>
        <UserProvider>
            <AgentBuilder
              editingAgent={agentKey ? ({ _key: agentKey } as Agent) : null}
              onSuccess={handleSuccess}
              onClose={handleClose}
            />
        </UserProvider>
      </AuthProvider>
    </>
  );
}
