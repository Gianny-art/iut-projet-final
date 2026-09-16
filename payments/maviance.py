import os

import requests
from requests.auth import HTTPBasicAuth

from .base import PaymentProvider, PaymentResult


class MavianceProvider(PaymentProvider):
    """Maviance Smobilpay (S3P) API (https://apidocs.smobilpay.com).

    Unlike Campay, Smobilpay has no documented push webhook: the flow is a
    mandatory quote-then-collect-then-poll sequence:
      1. POST /quotestd (payment item id + amount) -> quoteid
      2. POST /collectstd (quoteid [+ external txid]) -> a payment
         transaction number (PTN), status PENDING
      3. GET /verifytx (ptn or txid) -> PENDING | ERRORED | SUCCESS | REVERSED

    Because there is no webhook, app.py polls check_status() for PENDING
    maviance payments from the existing before_request housekeeping
    (same pattern as update_match_statuses()).

    NOTE: `payment_item` identifies which cash-in service to use and is
    assigned per-merchant during Maviance onboarding -- set it via
    MAVIANCE_PAYMENT_ITEM once the merchant account exists. Field names
    here follow the public S3P API Concepts/Reference docs; reconfirm
    against the live Postman/OpenAPI spec Maviance provides on onboarding
    before going to production.
    """

    name = 'maviance'

    def __init__(self):
        self.base_url = (os.getenv('MAVIANCE_BASE_URL') or 'https://s3p.smobilpay.com').rstrip('/')
        self.api_key = os.getenv('MAVIANCE_API_KEY')
        self.api_secret = os.getenv('MAVIANCE_API_SECRET')
        self.payment_item = os.getenv('MAVIANCE_PAYMENT_ITEM')

    def is_configured(self):
        return bool(self.api_key and self.api_secret and self.payment_item)

    def _auth(self):
        return HTTPBasicAuth(self.api_key, self.api_secret)

    def _quote(self, amount, external_ref):
        resp = requests.post(
            f'{self.base_url}/quotestd',
            json={'paymentitemid': self.payment_item, 'amount': str(int(amount)), 'clientphone': external_ref},
            auth=self._auth(), timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def initiate_collection(self, phone, amount, external_ref, operator=None):
        try:
            quote = self._quote(amount, phone)
            quote_id = quote.get('quoteid')
            if not quote_id:
                return PaymentResult(ok=False, error='Devis Maviance invalide', raw=quote)
            resp = requests.post(
                f'{self.base_url}/collectstd',
                json={'quoteid': quote_id, 'customerphonenumber': phone, 'trid': external_ref},
                auth=self._auth(), timeout=20,
            )
            data = resp.json() if resp.content else {}
            ptn = data.get('ptn')
            if resp.ok and ptn:
                return PaymentResult(
                    ok=True, reference=ptn, status='PENDING', raw=data,
                    message="Confirmez le paiement sur votre téléphone.",
                )
            return PaymentResult(ok=False, error=data.get('message') or f'HTTP {resp.status_code}', raw=data)
        except Exception as exc:
            return PaymentResult(ok=False, error=str(exc))

    def initiate_payout(self, phone, amount, external_ref, operator=None):
        # Smobilpay payouts require a separate disbursement payment item;
        # not exposed until a merchant account defines one. Until then,
        # withdrawals for the "maviance" provider fall back to manual admin
        # processing at the call site in app.py.
        return PaymentResult(ok=False, error='Retrait automatique Maviance non configuré')

    def check_status(self, reference):
        try:
            resp = requests.get(
                f'{self.base_url}/verifytx', params={'ptn': reference},
                auth=self._auth(), timeout=15,
            )
            data = resp.json() if resp.content else {}
            status = (data.get('status') or 'PENDING').upper()
            if status == 'SUCCESS':
                status = 'SUCCESSFUL'
            elif status in ('ERRORED', 'REVERSED'):
                status = 'FAILED'
            return PaymentResult(ok=True, reference=reference, status=status, raw=data)
        except Exception as exc:
            return PaymentResult(ok=False, reference=reference, error=str(exc))
