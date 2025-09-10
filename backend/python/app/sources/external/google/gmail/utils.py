import base64
import logging
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class GmailUtils:
    @staticmethod
    def transform_message_body(
    mail_to: List[str],
    mail_subject: str,
    mail_cc: Optional[List[str]] = None,
    mail_bcc: Optional[List[str]] = None,
    mail_body: Optional[str] = None,
    mail_attachments: Optional[List[str]] = None,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> dict:
        """
        Build a Gmail API message payload (with attachments) using EmailMessage
        so MIME/encodings are correct. Returns {"raw": ..., "threadId": ...?}
        """
        if not mail_to:
            raise ValueError("'mail_to' must contain at least one recipient.")
        if not mail_subject or not mail_subject.strip():
            raise ValueError("'mail_subject' must be non-empty.")

        # Convert plain newlines to <br> so Gmail renders as HTML
        body_html = (mail_body or "").replace("\\n", "\n").replace("\n", "<br>")

        msg = EmailMessage()
        msg["From"] = "me"  # Gmail API maps "me" to the authenticated user
        msg["To"] = ", ".join(mail_to)
        msg["Subject"] = mail_subject
        if mail_cc:
            msg["Cc"] = ", ".join(mail_cc)
        # You can set Bcc; Gmail will remove it from the delivered headers (that’s okay)
        if mail_bcc:
            msg["Bcc"] = ", ".join(mail_bcc)

        # Threading (replying in a conversation)
        if thread_id and message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = message_id

        # Set the main body as HTML
        msg.set_content("This is an HTML email. Your client does not support HTML.")
        msg.add_alternative(body_html, subtype="html")

        # Attach files (if any)
        for file_path in (mail_attachments or []):
            p = Path(file_path)
            if not p.exists():
                logger.warning("Attachment skipped (not found): %s", file_path)
                continue

            ctype, enc = mimetypes.guess_type(p.name)
            if ctype is None or enc is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)

            try:
                data = p.read_bytes()
            except Exception as e:
                logger.error("Attachment skipped (read error) %s: %s", file_path, e)
                continue

            # EmailMessage handles Content-Transfer-Encoding and headers correctly
            msg.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=p.name,
            )

        # Encode for Gmail API
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        return payload
