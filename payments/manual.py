import uuid

from .base import PaymentProvider, PaymentResult


class ManualProvider(PaymentProvider):
    """The historical flow: no API call, the student is shown a USSD code /
    a Mobile Money number to transfer to, and an admin validates the deposit
    or withdrawal by hand from the CMS. Used automatically whenever Campay
    or Maviance credentials are not configured, so the app stays fully
    usable while the real merchant accounts are being set up."""

    name = 'manual'

    def is_configured(self):
        return True

    def initiate_collection(self, phone, amount, external_ref, operator=None):
        reference = external_ref or f"MANUAL_{uuid.uuid4().hex[:10]}"
        return PaymentResult(
            ok=True, reference=reference, status='PENDING',
            message="Demande enregistrée. En attente de validation par l'admin.",
        )

    def initiate_payout(self, phone, amount, external_ref, operator=None):
        reference = external_ref or f"MANUAL_{uuid.uuid4().hex[:10]}"
        return PaymentResult(
            ok=True, reference=reference, status='PENDING',
            message="Retrait enregistré. Sera traité manuellement par l'admin.",
        )

    def check_status(self, reference):
        # Manual transactions never self-update; the admin action is what
        # changes their status in the database.
        return PaymentResult(ok=True, reference=reference, status='PENDING')
