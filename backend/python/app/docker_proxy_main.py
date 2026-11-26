"""Entry point for the sandbox Docker API proxy (see app/sandbox/docker_proxy.py)."""

from app.sandbox.docker_proxy import main

# Read by the agent-loop route checks: this main serves no FastAPI routes.
NO_HTTP_APP = "an asyncio socket proxy for the sandbox Docker API, not an HTTP app"

if __name__ == "__main__":
    main()
