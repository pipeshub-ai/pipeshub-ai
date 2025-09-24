#!/usr/bin/env python3
"""
Startup script for the Docling service
This can be used to run the service directly without Docker
"""

import os
import sys
from pathlib import Path

# Add the backend/python directory to Python path
backend_python_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_python_path))

if __name__ == "__main__":
    # Import here to satisfy import-order lint rule when file is used as script
    from app.services.docling.docling_service import run
    # Get configuration from environment variables
    host = os.getenv("DOCLING_HOST", "0.0.0.0")
    port = int(os.getenv("DOCLING_PORT", "8081"))
    reload = os.getenv("DOCLING_RELOAD", "false").lower() == "true"

    print(f"🚀 Starting Docling service on {host}:{port}")
    print(f"📁 Working directory: {os.getcwd()}")
    print(f"🐍 Python path: {sys.path[0]}")

    run(host=host, port=port, reload=reload)
