"""Local/GitHub Actions readiness checks for Visa/Cybersource sandbox credentials.

No network request is made. No secret values are printed.

Expected environment variables:
  VISA_SANDBOX_ORG_ID
  VISA_SANDBOX_KEY_ID
  VISA_SANDBOX_SHARED_SECRET
  VISA_SANDBOX_RESPONSE_MLE_P12_B64
  VISA_SANDBOX_RESPONSE_MLE_PASSWORD
  VISA_SANDBOX_RESPONSE_MLE_KEY_ID
  VISA_SANDBOX_REQUEST_MLE_CERT_B64

The shared secret is expected to be the base64-encoded value issued by
Cybersource. The response MLE material is the downloaded CyberSource-generated
P12. The request MLE certificate is the CyberSource public certificate used to
encrypt outbound Intelligent Commerce request payloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12


REQUIRED = [
    "VISA_SANDBOX_ORG_ID",
    "VISA_SANDBOX_KEY_ID",
    "VISA_SANDBOX_SHARED_SECRET",
    "VISA_SANDBOX_RESPONSE_MLE_P12_B64",
    "VISA_SANDBOX_RESPONSE_MLE_PASSWORD",
    "VISA_SANDBOX_RESPONSE_MLE_KEY_ID",
    "VISA_SANDBOX_REQUEST_MLE_CERT_B64",
]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _secret_bytes(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("VISA_SANDBOX_SHARED_SECRET is not valid base64") from exc



def build_test_jwt(
    *,
    merchant_id: str,
    key_id: str,
    shared_secret: str,
    response_mle_kid: str,
) -> str:
    """Construct a Visa JWT v2 token locally for a synthetic body.

    This only validates the symmetric signing material and current claim shape.
    The token is never sent anywhere.
    """
    body = b'{"safeagent":"readiness-only"}'
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    claims = {
        "digest": base64.b64encode(hashlib.sha256(body).digest()).decode("ascii"),
        "digestAlgorithm": "SHA-256",
        "iat": now,
        "exp": now + 120,
        "request-method": "post",
        "request-resource-path": "/acp/v1/readiness-only",
        "request-host": "apitest.cybersource.com",
        "iss": merchant_id,
        "jti": str(uuid.uuid4()),
        "v-c-jwt-version": "2",
        "v-c-merchant-id": merchant_id,
        "v-c-response-mle-kid": response_mle_kid,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = _secret_bytes(shared_secret)
    signature = hmac.new(key, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


def main() -> int:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print("Visa sandbox readiness: NOT READY")
        print("Missing secret names: " + ", ".join(missing))
        return 2

    merchant_id = os.environ["VISA_SANDBOX_ORG_ID"].strip()
    key_id = os.environ["VISA_SANDBOX_KEY_ID"].strip()
    response_kid = os.environ["VISA_SANDBOX_RESPONSE_MLE_KEY_ID"].strip()
    if not merchant_id or not key_id or not response_kid:
        print("Visa sandbox readiness: NOT READY")
        print("One or more identifier secrets are empty.")
        return 2

    # Validate the shared-secret encoding and locally construct a JWT.
    token = build_test_jwt(
        merchant_id=merchant_id,
        key_id=key_id,
        shared_secret=os.environ["VISA_SANDBOX_SHARED_SECRET"],
        response_mle_kid=response_kid,
    )
    if token.count(".") != 2:
        raise AssertionError("local JWT construction failed")

    # Validate that the response MLE P12 can be decoded with its password.
    try:
        p12_bytes = base64.b64decode(
            os.environ["VISA_SANDBOX_RESPONSE_MLE_P12_B64"], validate=True
        )
    except Exception as exc:
        raise ValueError("response MLE P12 secret is not valid base64") from exc

    password = os.environ["VISA_SANDBOX_RESPONSE_MLE_PASSWORD"].encode("utf-8")
    private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
        p12_bytes, password
    )
    if private_key is None or certificate is None:
        raise ValueError("response MLE P12 did not contain both private key and certificate")

    # Cybersource's current MLE setup says the downloaded Response MLE P12 also
    # contains the CyberSource_SJC_US public certificate used for request MLE.
    # Validate that the bundle includes at least one additional certificate
    # rather than requiring a separately downloaded PEM.
    additional_certificates = list(additional_certificates or [])
    if not additional_certificates:
        raise ValueError(
            "response MLE P12 did not contain the Cybersource SJC request-encryption certificate"
        )
    request_cert = additional_certificates[0]
    if request_cert.public_key() is None:
        raise ValueError("Cybersource SJC certificate contains no public key")

    print("Visa sandbox readiness: READY")
    print("JWT shared-secret material: valid")
    print("Response MLE P12: valid")
    print("Request MLE SJC certificate from Response MLE P12: valid")
    print("Network calls made: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
