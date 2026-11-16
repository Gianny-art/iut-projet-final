import base64
import hashlib
import hmac
import os
import time

import requests

from .base import PaymentProvider, PaymentResult


class CampayProvider(PaymentProvider):
    """Campay (https://www.campay.net) collect/withdraw API.

    Flow: POST /api/token/ (username+password -> bearer token, cached until
    it's near expiry) -> POST /api/collect/ or /api/withdraw/ (returns a
    `reference`, status PENDING) -> Campay pushes the final status to our
    /webhook/campay endpoint (GET or POST, signed), with
    GET /api/transaction/<reference>/ as a polling fallback.

    Verified 2026-09-16 against Campay's official Postman collection
    (documenter.getpostman.com/view/2391374) and their official Python SDK
    (github.com/CamPay/campay-python-sdk).
    """

    name = 'campay'

    def __init__(self):
        self.base_url = (os.getenv('CAMPAY_BASE_URL') or 'https://demo.campay.net').rstrip('/')
        self.username = os.getenv('CAMPAY_APP_USERNAME')
        self.password = os.getenv('CAMPAY_APP_PASSWORD')
        # A permanent app token (dashboard > App Keys) can be used instead of
        # username/password to skip the /api/token/ round trip entirely.
        self.permanent_token = os.getenv('CAMPAY_PERMANENT_TOKEN')
        self.webhook_key = os.getenv('CAMPAY_WEBHOOK_KEY')
        self._token = None
        self._token_expires_at = 0

    def is_configured(self):
        return bool(self.permanent_token or (self.username and self.password))

    def _get_token(self):
        if self.permanent_token:
            return self.permanent_token
        if self._token and time.time() < self._token_expires_at:
            return self._token
        resp = requests.post(
            f'{self.base_url}/api/token/',
            json={'username': self.username, 'password': self.password},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data['token']
        # Refresh a little early; default to 55 minutes if Campay omits it.
        self._token_expires_at = time.time() + int(data.get('expires_in', 3300)) - 60
        return self._token

    def _headers(self):
        return {
            'Authorization': f'Token {self._get_token()}',
            'Content-Type': 'application/json',
        }

    @staticmethod
    def _normalize_phone(phone):
        """Campay requires the 237 country code with no '+' (e.g. 237699123456)."""
        digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
        if digits.startswith('237'):
            return digits
        return f'237{digits}'

    def initiate_collection(self, phone, amount, external_ref, operator=None):
        try:
            payload = {
                'amount': str(int(amount)),
                'currency': 'XAF',
                'from': self._normalize_phone(phone),
                'description': 'Depot ManyBet',
                'external_reference': external_ref,
            }
            resp = requests.post(
                f'{self.base_url}/api/collect/', json=payload,
                headers=self._headers(), timeout=20,
            )
            data = resp.json() if resp.content else {}
            if resp.ok and (data.get('reference') or data.get('ussd_code')):
                return PaymentResult(
                    ok=True, reference=data.get('reference', external_ref),
                    status='PENDING', ussd=data.get('ussd_code'), raw=data,
                    message="Confirmez le paiement sur votre téléphone.",
                )
            return PaymentResult(ok=False, error=data.get('message') or f'HTTP {resp.status_code}', raw=data)
        except Exception as exc:
            return PaymentResult(ok=False, error=str(exc))

    def initiate_payout(self, phone, amount, external_ref, operator=None):
        try:
            payload = {
                'amount': str(int(amount)),
                'currency': 'XAF',
                'to': self._normalize_phone(phone),
                'description': 'Retrait ManyBet',
                'external_reference': external_ref,
            }
            resp = requests.post(
                f'{self.base_url}/api/withdraw/', json=payload,
                headers=self._headers(), timeout=20,
            )
            data = resp.json() if resp.content else {}
            # A payout response only means the transfer was *initiated* --
            # Campay confirms success/failure later via webhook or polling,
            # same as a collection.
            if resp.ok and data.get('reference'):
                return PaymentResult(ok=True, reference=data.get('reference'), status='PENDING', raw=data)
            return PaymentResult(ok=False, error=data.get('message') or f'HTTP {resp.status_code}', raw=data)
        except Exception as exc:
            return PaymentResult(ok=False, error=str(exc))

    def check_status(self, reference):
        try:
            resp = requests.get(
                f'{self.base_url}/api/transaction/{reference}/',
                headers=self._headers(), timeout=15,
            )
            data = resp.json() if resp.content else {}
            status = (data.get('status') or 'PENDING').upper()
            return PaymentResult(ok=True, reference=reference, status=status, raw=data)
        except Exception as exc:
            return PaymentResult(ok=False, reference=reference, error=str(exc))

    def verify_webhook_signature(self, data):
        """Verify the HS256 JWT Campay puts in the webhook's `signature`
        field, using CAMPAY_WEBHOOK_KEY (dashboard > App Keys > Webhook key).

        Returns True (valid), False (missing/invalid), or None when no
        webhook key is configured -- callers must treat None as "could not
        verify" and decide how cautious to be, never as "valid".
        """
        if not self.webhook_key:
            return None
        token = (data or {}).get('signature')
        if not token or token.count('.') != 2:
            return False
        header_b64, payload_b64, sig_b64 = token.split('.')
        try:
            expected = hmac.new(
                self.webhook_key.encode(), f'{header_b64}.{payload_b64}'.encode(), hashlib.sha256
            ).digest()
            expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b'=').decode()
            return hmac.compare_digest(expected_b64, sig_b64)
        except Exception:
            return False
