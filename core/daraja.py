"""Small Daraja client for M-PESA Express and B2C disbursements.

No credentials or payment authority belong in application code. Configuration is
read from Django settings and the client deliberately exposes only these two
operations.
"""
import base64
import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone


class DarajaError(Exception):
    pass


class DarajaUnknownOutcome(DarajaError):
    """Transport failed after submission may have reached Safaricom."""


def configured(*names):
    return all(getattr(settings, name, "") for name in names)


def api_root():
    return "https://sandbox.safaricom.co.ke" if settings.DARAJA_ENV == "sandbox" else "https://api.safaricom.co.ke"


def _request(url, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, headers=headers or {}, method="POST" if body else "GET")
    try:
        with urlopen(request, timeout=25) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        if exc.code >= 500:
            raise DarajaUnknownOutcome("Daraja returned a server error. Check the transaction status before retrying.") from exc
        raise DarajaError("Daraja rejected the request. Check your shortcode, product access, and request configuration.") from exc
    except (URLError, TimeoutError) as exc:
        raise DarajaUnknownOutcome("The connection to Daraja timed out. Check the transaction status before retrying.") from exc
    except json.JSONDecodeError as exc:
        raise DarajaUnknownOutcome("Daraja returned an unreadable response. Check the transaction status before retrying.") from exc
    if not isinstance(result, dict):
        raise DarajaError("Daraja returned an invalid response.")
    return result


def access_token():
    if not configured("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET"):
        raise DarajaError("Daraja API credentials are not configured.")
    raw = f"{settings.DARAJA_CONSUMER_KEY}:{settings.DARAJA_CONSUMER_SECRET}".encode()
    credentials = base64.b64encode(raw).decode()
    result = _request(
        f"{api_root()}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}"},
    )
    token = result.get("access_token")
    if not token:
        raise DarajaError("Daraja did not provide an access token.")
    return token


def normalize_phone(phone):
    digits = "".join(character for character in phone if character.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("+254"):
        digits = digits[1:]
    if len(digits) != 12 or not digits.startswith(("2547", "2541")):
        raise DarajaError("Enter a valid Kenyan M-Pesa number, for example 0712345678.")
    return digits


def callback_url(kind):
    if not settings.PUBLIC_BASE_URL or not settings.DARAJA_CALLBACK_TOKEN:
        raise DarajaError("Set PUBLIC_BASE_URL and DARAJA_CALLBACK_TOKEN before enabling payments.")
    if not settings.PUBLIC_BASE_URL.startswith("https://"):
        raise DarajaError("PUBLIC_BASE_URL must be a publicly reachable HTTPS address for Safaricom callbacks.")
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/daraja/{kind}/{settings.DARAJA_CALLBACK_TOKEN}/"


def initiate_stk_push(payment, phone):
    required = ("DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET", "DARAJA_SHORTCODE", "DARAJA_PASSKEY")
    if not configured(*required):
        raise DarajaError("M-Pesa collection is not configured yet.")
    phone = normalize_phone(phone)
    amount = Decimal(payment.amount)
    if amount != amount.quantize(Decimal("1")) or amount < 1:
        raise DarajaError("M-Pesa payments must be a whole number of Kenyan shillings.")
    now = timezone.localtime()
    timestamp = now.strftime("%Y%m%d%H%M%S")
    shortcode = str(settings.DARAJA_SHORTCODE)
    password = base64.b64encode(f"{shortcode}{settings.DARAJA_PASSKEY}{timestamp}".encode()).decode()
    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": settings.DARAJA_STK_TRANSACTION_TYPE,
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback_url("stk"),
        "AccountReference": f"EH{payment.pk}",
        "TransactionDesc": f"EstateHub payment {payment.pk}"[:40],
    }
    result = _request(
        f"{api_root()}/mpesa/stkpush/v1/processrequest", payload,
        headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"},
    )
    if str(result.get("ResponseCode", "")) != "0" or not result.get("CheckoutRequestID"):
        raise DarajaError(result.get("ResponseDescription") or "Daraja did not accept the payment request.")
    return result, phone


def initiate_b2c(payment, phone):
    required = (
        "DARAJA_CONSUMER_KEY", "DARAJA_CONSUMER_SECRET", "DARAJA_B2C_SHORTCODE",
        "DARAJA_B2C_INITIATOR_NAME", "DARAJA_B2C_SECURITY_CREDENTIAL",
    )
    if not settings.DARAJA_B2C_ENABLED or not configured(*required):
        raise DarajaError("Automated worker payouts are not configured. Use the manual payout option.")
    phone = normalize_phone(phone)
    amount = Decimal(payment.amount)
    if amount != amount.quantize(Decimal("1")) or amount < 10 or amount > 250000:
        raise DarajaError("Automated M-Pesa payouts must be whole amounts between KES 10 and KES 250,000.")
    payload = {
        "InitiatorName": settings.DARAJA_B2C_INITIATOR_NAME,
        "SecurityCredential": settings.DARAJA_B2C_SECURITY_CREDENTIAL,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": str(settings.DARAJA_B2C_SHORTCODE),
        "PartyB": phone,
        "Remarks": f"EstateHub job {payment.job_id}"[:100],
        "QueueTimeOutURL": callback_url("b2c-timeout"),
        "ResultURL": callback_url("b2c-result"),
    }
    result = _request(
        f"{api_root()}/mpesa/b2c/v3/paymentrequest", payload,
        headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"},
    )
    if str(result.get("ResponseCode", "")) != "0" or not result.get("OriginatorConversationID"):
        raise DarajaError(result.get("ResponseDescription") or "Daraja did not accept the payout request.")
    return result, phone
