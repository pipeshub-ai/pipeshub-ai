#!/usr/bin/env python3
"""
Test script for the Docling service client
"""

import asyncio
import sys
from pathlib import Path

# Add the backend/python directory to Python path
backend_python_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_python_path))

async def test_docling_client() -> None:
    """Test the Docling client functionality"""
    print("🧪 Testing Docling Client")

    # Initialize client
    client = DoclingClient(service_url="http://localhost:8081")

    # Test health check
    print("🔍 Testing health check...")
    is_healthy = await client.health_check()
    print(f"Health check result: {'✅ Healthy' if is_healthy else '❌ Unhealthy'}")

    if not is_healthy:
        print("❌ Docling service is not running. Please start it first.")
        return

    # Test with a sample PDF (you would need to provide a real PDF file)
    print("📄 Testing PDF processing...")

    # Create a minimal test PDF binary (this is just for testing the client, not actual PDF processing)
    test_pdf_binary = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n>>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n174\n%%EOF"

    try:
        result = await client.process_pdf("test_document.pdf", test_pdf_binary)
        if result:
            print("✅ PDF processing test successful")
            print(f"Result type: {type(result)}")
        else:
            print("❌ PDF processing test failed - no result returned")
    except Exception as e:
        print(f"❌ PDF processing test failed with error: {str(e)}")


if __name__ == "__main__":
    # Import inside main to satisfy import-order lint rule
    from app.services.docling.client import DoclingClient
    asyncio.run(test_docling_client())
