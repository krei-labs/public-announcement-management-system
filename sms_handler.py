"""SMS notification interface for the public repository."""


class SMSHandler:
    """Keep SMS features available in the UI without external sending."""

    def send(self, message, to_number):
        return {
            "success": False,
            "error": "SMS sending is disabled in the public repository."
        }

    def send_bulk(self, message, phone_numbers):
        return {
            "success": False,
            "sent": 0,
            "failed": len(phone_numbers or []),
            "errors": [{
                "error": "SMS sending is disabled in the public repository."
            }]
        }

    def update_config(self, data=None):
        return False
