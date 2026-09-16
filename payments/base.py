class PaymentResult:
    """Uniform result returned by every provider, whatever its native
    response shape looks like, so app.py never branches on provider name."""

    def __init__(self, ok, reference=None, status='PENDING', message=None,
                 error=None, ussd=None, raw=None):
        self.ok = ok
        self.reference = reference
        self.status = status  # PENDING | SUCCESSFUL | FAILED
        self.message = message
        self.error = error
        self.ussd = ussd
        self.raw = raw

    def to_dict(self):
        return {
            'ok': self.ok,
            'reference': self.reference,
            'status': self.status,
            'message': self.message,
            'error': self.error,
            'ussd': self.ussd,
        }


class PaymentProvider:
    name = 'manual'

    def is_configured(self):
        """True when this provider has real credentials and can be used
        instead of the manual fallback."""
        return False

    def initiate_collection(self, phone, amount, external_ref, operator=None):
        """Ask the provider to prompt the customer's phone for a deposit."""
        raise NotImplementedError

    def initiate_payout(self, phone, amount, external_ref, operator=None):
        """Ask the provider to send money to the customer's phone."""
        raise NotImplementedError

    def check_status(self, reference):
        """Poll the provider for the current status of a transaction."""
        raise NotImplementedError

    def verify_webhook_signature(self, data):
        """Verify an incoming webhook payload's authenticity. Return True,
        False, or None ("not configured / not applicable" -- callers must
        not treat this as valid). Providers without a signed webhook can
        leave this as the default."""
        return None
