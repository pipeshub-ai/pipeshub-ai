import base64
import logging
import mimetypes
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# (filename, content_bytes, mime_type)
InMemoryAttachment = Tuple[str, bytes, str]

_MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MiB Gmail conservative cap


class GmailUtils:
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format using regex pattern"""
        if not email or not email.strip():
            return False

        email = email.strip()
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    @staticmethod
    def validate_email_list(email_list: List[str]) -> bool:
        """Validate email list format using regex pattern"""
        for email in email_list:
            if not GmailUtils.validate_email(email):
                return False
        return True

    @staticmethod
    def validate_subject(subject: str) -> bool:
        """Validate that subject is not empty"""
        return subject is not None and subject.strip() != ""

    @staticmethod
    def encode_message(message: Optional[str]) -> str:
        """Encode the message to base64"""
        if not message:
            return ""
        return base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8")

    @staticmethod
    def transform_message_body(
        mail_to: List[str],
        mail_subject: str,
        mail_cc: Optional[List[str]],
        mail_bcc: Optional[List[str]],
        mail_body: Optional[str],
        mail_attachments: Optional[List[str]],
        thread_id: Optional[str] = None,
        message_id: Optional[str] = None,
        in_memory_attachments: Optional[Sequence[InMemoryAttachment]] = None,
    ) -> dict:
        """Build the message body using MIME format.

        Args:
            mail_to: Recipient addresses.
            mail_cc: CC addresses.
            mail_bcc: BCC addresses.
            mail_subject: Subject line.
            mail_body: HTML body content.
            mail_attachments: Local file paths (legacy; agent code passes None).
            thread_id: Gmail thread ID for threading.
            message_id: Gmail message ID for In-Reply-To / References headers.
            in_memory_attachments: Sequence of (filename, bytes, mime_type) tuples
                resolved from PipesHub records. These are added after any file-path
                attachments and never touch the filesystem.

        Returns:
            dict with "raw" (base64url-encoded MIME) and optionally "threadId".

        Raises:
            ValueError: If the total attachment size exceeds the 25 MiB cap.
        """
        body_content = mail_body or ""
        body_content = body_content.replace("\\n", "\n").replace("\n", "<br>")

        has_path_attachments = bool(mail_attachments)
        has_memory_attachments = bool(in_memory_attachments)

        if has_path_attachments or has_memory_attachments:
            message = MIMEMultipart()
            text_part = MIMEText(body_content, "html")
            message.attach(text_part)

            total_bytes = 0

            # Legacy file-path attachments (kept for backward compat; agent code doesn't use these).
            if mail_attachments:
                for file_path in mail_attachments:
                    file_path_obj = Path(file_path)
                    if not file_path_obj.exists():
                        continue
                    content_type, encoding = mimetypes.guess_type(file_path)
                    if content_type is None or encoding is not None:
                        content_type = "application/octet-stream"
                    main_type, sub_type = content_type.split("/", 1)
                    try:
                        with open(file_path, "rb") as fh:
                            attachment_data = fh.read()
                        total_bytes += len(attachment_data)
                        if total_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
                            raise ValueError(
                                f"Total attachment size exceeds the {_MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MiB limit."
                            )
                        att = MIMEApplication(attachment_data, _subtype=sub_type)
                        att.add_header("Content-Disposition", "attachment", filename=file_path_obj.name)
                        message.attach(att)
                    except ValueError:
                        raise
                    except Exception as exc:
                        logger.error("Failed to attach file %s: %s", file_path, exc)
                        continue

            # In-memory attachments from PipesHub record resolution.
            if in_memory_attachments:
                for filename, content, mime_type in in_memory_attachments:
                    total_bytes += len(content)
                    if total_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
                        raise ValueError(
                            f"Total attachment size exceeds the {_MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MiB limit "
                            f"while adding '{filename}'."
                        )
                    _, sub_type = mime_type.split("/", 1) if "/" in mime_type else ("application", mime_type)
                    att = MIMEApplication(content, _subtype=sub_type)
                    att.add_header("Content-Disposition", "attachment", filename=filename)
                    message.attach(att)
        else:
            message = MIMEText(body_content, "html")

        message["to"] = ", ".join(mail_to)
        message["from"] = "me"
        message["subject"] = mail_subject

        if mail_cc:
            message["Cc"] = ", ".join(mail_cc)
        if mail_bcc:
            message["Bcc"] = ", ".join(mail_bcc)

        if thread_id and message_id:
            message["In-Reply-To"] = message_id
            message["References"] = message_id

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        email_data: dict = {"raw": raw_message}
        if thread_id:
            email_data["threadId"] = thread_id

        return email_data
