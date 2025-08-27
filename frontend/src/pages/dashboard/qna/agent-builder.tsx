import { Helmet } from 'react-helmet-async';
import { useParams, useNavigate } from 'react-router-dom';

import { UserProvider } from 'src/context/UserContext';
import { AuthProvider } from 'src/auth/context/jwt';

import { AgentBuilder } from 'src/sections/qna/agents';
import { GroupsProvider } from 'src/context/GroupsContext';
import { Agent } from 'src/types/agent';

// ----------------------------------------------------------------------

const metadata = { title: `Flow Agent Builder` };

export default function Page() {
  const { agentKey } = useParams<{ agentKey?: string }>();
  const navigate = useNavigate();

  const handleSuccess = (agent: any) => {
    // Redirect to the agent chat or management page
    if (agent?._key) {
      navigate(`/agents/${agent._key}`);
    } else {
      navigate('/agents');
    }
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
          <GroupsProvider>
            <AgentBuilder
              editingAgent={agentKey ? ({ _key: agentKey } as Agent) : null}
              onSuccess={handleSuccess}
              onClose={handleClose}
            />
          </GroupsProvider>
        </UserProvider>
      </AuthProvider>
    </>
  );
}
