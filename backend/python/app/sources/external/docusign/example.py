"""DocuSign Data Source Example

This example demonstrates using the DocuSign API via the DocuSign client.

Setup Requirements:
1. Create a DocuSign developer account at https://developers.docusign.com/
2. Generate API credentials (account ID, client ID, client secret)
3. Obtain an access token following DocuSign's OAuth or JWT flow

Usage:
    export DOCUSIGN_ACCOUNT_ID="your_account_id"
    export DOCUSIGN_CLIENT_ID="your_client_id"
    export DOCUSIGN_CLIENT_SECRET="your_client_secret"
    export DOCUSIGN_ACCESS_TOKEN="your_access_token"
    python -m app.sources.external.docusign.example
"""

import sys



# Check if DocuSign SDK is installed
try:
    import importlib.util
    if importlib.util.find_spec("docusign_esign"):
        print("DocuSign SDK is installed")
    else:
        print("DocuSign SDK not found")
        sys.exit(1)
except ImportError:
    print("DocuSign SDK not installed. Run: pip install docusign-esign")
    sys.exit(1)

# Import our modules
from app.sources.client.docusign.docusign import DocuSignClient
from app.sources.external.docusign.docusign import DocuSignDataSource

# Constants
DISPLAY_LIMIT = 3


def main() -> None:
    """Run the DocuSign example."""
    # Get authentication details from environment variables
   

    if not all([account_id, client_id, client_secret, access_token]):
        print("Error: Required environment variables not set.")
        print("Please set DOCUSIGN_ACCOUNT_ID, DOCUSIGN_CLIENT_ID, DOCUSIGN_CLIENT_SECRET, and DOCUSIGN_ACCESS_TOKEN")
        return

    # Create a DocuSign client
    client = DocuSignClient(
        account_id=account_id,
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
    )

    # Create a DocuSign data source
    docusign = DocuSignDataSource(client=client)

    print("Fetching DocuSign data via REST API...")
    print()

    # Get user information
    print("=== Getting user info ===")
    user_info = docusign.get_user_info()
    if user_info.success:
        print("User info retrieved successfully")
        if user_info.data:
            # Print selected fields, skipping complex objects
            simple_fields = ["name", "email", "userId", "userName", "accountId", "accountName"]
            for field in simple_fields:
                if field in user_info.data:
                    print(f"{field}: {user_info.data.get(field)}")
    else:
        print(f"Error getting user info: {user_info.error}")
    print()

    # List envelopes with proper date format
    print("=== Listing recent envelopes ===")
    # Use ISO format date for better compatibility
    from_date = "2020-01-01T00:00:00.000Z"
    envelopes_response = docusign.list_envelopes(from_date=from_date, count=10)

    if envelopes_response.success:
        # Safely extract envelopes list with fallbacks at each step
        envelopes_data = envelopes_response.data or {}
        envelope_list = envelopes_data.get("envelopes", []) or []
        print(f"Envelopes: {len(envelope_list)}")

        # If we have envelopes, show details
        if envelope_list:
            for idx, env in enumerate(envelope_list[:DISPLAY_LIMIT], 1):
                print(f"  {idx}. Subject: {env.get('emailSubject', 'No subject')}")
                print(f"     Status: {env.get('status', 'Unknown')}")
                created_time = env.get("createdDateTime", "Unknown")
                if created_time and isinstance(created_time, str):
                    created_time = created_time.split(".")[0]  # Truncate for cleaner output
                print(f"     Created: {created_time}")
                print(f"     ID: {env.get('envelopeId', 'Unknown')}")

            # Get details of first envelope if available
            envelope_id = envelope_list[0].get("envelopeId")
            if envelope_id:
                print(f"\n=== Getting details for envelope {envelope_id} ===")
                envelope = docusign.get_envelope(envelope_id=envelope_id)
                if envelope.success and envelope.data:
                    print(f"Subject: {envelope.data.get('emailSubject', 'No subject')}")
                    print(f"Status: {envelope.data.get('status', 'Unknown')}")
                    sent_time = envelope.data.get("sentDateTime", "Not sent")
                    if sent_time and isinstance(sent_time, str):
                        sent_time = sent_time.split(".")[0]  # Truncate for cleaner output
                    print(f"Sent: {sent_time}")

                    # Get recipients
                    print(f"\n=== Getting recipients for envelope {envelope_id} ===")
                    recipients = docusign.get_envelope_recipients(envelope_id=envelope_id)
                    if recipients.success and recipients.data:
                        recipient_types = ["signers", "carbonCopies", "certifiedDeliveries"]

                        # Count total recipients across all types
                        total_recipients = sum(
                            len(recipients.data.get(t, [])) for t in recipient_types if recipients.data.get(t)
                        )

                        print(f"Total recipients: {total_recipients}")

                        # Show signers
                        signers = recipients.data.get("signers", [])
                        if signers:
                            print(f"Signers: {len(signers)}")
                            for idx, signer in enumerate(signers[:DISPLAY_LIMIT], 1):
                                print(f"  {idx}. {signer.get('name', 'Unknown')} - {signer.get('email', 'No email')}")
                                print(f"      Status: {signer.get('status', 'Unknown')}")
                    else:
                        print("No recipients found or error getting recipients")
                        if recipients.error:
                            print(f"Error: {recipients.error}")

                    # Get documents
                    print(f"\n=== Getting documents for envelope {envelope_id} ===")
                    documents = docusign.get_envelope_documents(envelope_id=envelope_id)
                    if documents.success and documents.data:
                        doc_list = documents.data.get("documents", []) or []
                        print(f"Documents: {len(doc_list)}")
                        for idx, doc in enumerate(doc_list[:DISPLAY_LIMIT], 1):
                            print(f"  {idx}. {doc.get('name', 'Unnamed')} (pages: {doc.get('pages', 'Unknown')})")
                    else:
                        print("No documents found or error getting documents")
                        if documents.error:
                            print(f"Error: {documents.error}")
                else:
                    print(f"Error getting envelope: {envelope.error}")
        else:
            print("No envelopes found in the account")
    else:
        print(f"Error listing envelopes: {envelopes_response.error}")
    print()

    # List templates
    print("=== Listing templates ===")
    templates_response = docusign.list_templates(count=10)
    if templates_response.success:
        templates_data = templates_response.data or {}
        template_list = templates_data.get("envelopeTemplates", []) or []
        print(f"Templates: {len(template_list)}")

        # Show template details if available
        if template_list:
            for idx, tmpl in enumerate(template_list[:DISPLAY_LIMIT], 1):
                print(f"  {idx}. Name: {tmpl.get('name', 'Unnamed')}")
                created_time = tmpl.get("created", "Unknown")
                if created_time and isinstance(created_time, str):
                    created_time = created_time.split(".")[0]  # Truncate for cleaner output
                print(f"     Created: {created_time}")
                print(f"     ID: {tmpl.get('templateId', 'Unknown')}")

            # Get details of first template
            template_id = template_list[0].get("templateId")
            if template_id:
                print(f"\n=== Getting details for template {template_id} ===")
                template = docusign.get_template(template_id=template_id)
                if template.success and template.data:
                    print(f"Name: {template.data.get('name', 'Unnamed')}")
                    print(f"Description: {template.data.get('description', 'No description')}")
                    created_time = template.data.get("created", "Unknown")
                    if created_time and isinstance(created_time, str):
                        created_time = created_time.split(".")[0]  # Truncate for cleaner output
                    print(f"Created: {created_time}")

                    # Count documents
                    documents = template.data.get("documents", []) or []
                    print(f"Documents: {len(documents)}")

                    # Count recipients
                    recipients = template.data.get("recipients", {}) or {}
                    recipient_types = ["signers", "carbonCopies", "certifiedDeliveries"]
                    total_recipients = sum(
                        len(recipients.get(t, [])) for t in recipient_types if recipients.get(t)
                    )
                    print(f"Recipients: {total_recipients}")
                else:
                    print(f"Error getting template: {template.error}")
        else:
            print("No templates found in the account")
    else:
        print(f"Error listing templates: {templates_response.error}")

    print("\nDone!")


if __name__ == "__main__":
    main()
