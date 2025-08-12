import base64
import logging
import mimetypes
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class GmailUtils:
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format using regex pattern"""
        if not email or not email.strip():
            return False

        email = email.strip()
        # RFC 5322 compliant email regex pattern
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
        message_id: Optional[str] = None) -> dict:
        """Build the message body using MIME format
        Args:
            mail_to: List of email addresses to send the email to
            mail_cc: List of email addresses to send the email to
            mail_bcc: List of email addresses to send the email to
            mail_subject: The subject of the email
            mail_body: The body of the email
            mail_attachments: List of attachments to send with the email (file paths)
            thread_id: The thread id of the email
            message_id: The message id of the email
        Returns:
            dict: The message body
        """
        # Prepare the body content
        body_content = mail_body or ""
        body_content = body_content.replace("\\n", "\n").replace("\n", "<br>")

        # Create multipart message if attachments exist, otherwise simple text message
        if mail_attachments and len(mail_attachments) > 0:
            message = MIMEMultipart()

            # Add the text body
            text_part = MIMEText(body_content, "html")
            message.attach(text_part)

            # Add attachments
            for file_path in mail_attachments:
                file_path_obj = Path(file_path)
                if not file_path_obj.exists():
                    continue  # Skip non-existent files

                # Guess the content type based on the file extension
                content_type, encoding = mimetypes.guess_type(file_path)
                if content_type is None or encoding is not None:
                    content_type = "application/octet-stream"

                main_type, sub_type = content_type.split("/", 1)

                # Read file and create attachment
                try:
                    with open(file_path, "rb") as file:
                        attachment_data = file.read()

                    attachment = MIMEApplication(attachment_data, _subtype=sub_type)
                    attachment.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=file_path_obj.name
                    )
                    message.attach(attachment)
                except Exception as e:
                    # Log error but continue with other attachments
                    logger.error(f"Failed to attach file {file_path}: {e}")
                    continue
        else:
            # Simple text message without attachments
            message = MIMEText(body_content, "html")

        # Set required headers
        message["to"] = ", ".join(mail_to)
        message["from"] = "me"
        message["subject"] = mail_subject

        # Set optional headers
        if mail_cc:
            message["Cc"] = ", ".join(mail_cc)

        if mail_bcc:
            message["Bcc"] = ", ".join(mail_bcc)

        # Add reply headers if this is a response
        if thread_id and message_id:
            message["In-Reply-To"] = message_id
            message["References"] = message_id

        # Encode the message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        # Build the email data structure
        email_data = {"raw": raw_message}

        # Add thread ID if this is part of a conversation
        if thread_id:
            email_data["threadId"] = thread_id

        return email_data
