"""Email notification interface for the public repository."""


class EmailHandler:
    """Keep email features available in the UI without external sending."""

    def send(self, subject, body, to_email, to_name=None, html=False):
        return {
            "success": False,
            "error": "Email sending is disabled in the public repository."
        }

    def send_bulk(self, subject, body, to_emails, html=False):
        emails = list(to_emails or [])
        return {
            "success": 0,
            "failed": len(emails),
            "errors": [{
                "error": "Email sending is disabled in the public repository."
            }]
        }

    def update_config(self, data=None):
        return False
