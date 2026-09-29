from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from safeagent_exec_guard.cybersource_transport import (
    build_jwt_v2,
    decrypt_response_jwe,
    encrypt_request_jwe,
)


def _cert(key, serial_name="SJC-TEST"):
    now = datetime.now(timezone.utc)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "CyberSource_SJC_US"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, serial_name),
        ]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


def _decode(value):
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


def test_request_jwe_uses_current_cybersource_algorithms_and_cert_kid():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _cert(key, "SJC-123")
    compact = encrypt_request_jwe({"hello": "world"}, cert)

    parts = compact.split(".")
    assert len(parts) == 5
    header = _decode(parts[0])
    assert header["alg"] == "RSA-OAEP-256"
    assert header["enc"] == "A256GCM"
    assert header["kid"] == "SJC-123"


def test_response_jwe_decrypt_round_trip():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = _cert(key)
    compact = encrypt_request_jwe({"status": "COMPLETED"}, cert)

    assert decrypt_response_jwe(compact, key) == {"status": "COMPLETED"}


def test_jwt_v2_claims_bind_test_host_path_and_body():
    token = build_jwt_v2(
        key_id="api-key-id",
        shared_secret_b64=base64.b64encode(b"shared-secret-material").decode(),
        merchant_id="zender_sandbox",
        response_mle_kid="response-key-id",
        method="POST",
        resource_path="/acp/v1/instructions/probe/confirmations",
        http_body=b'{"encryptedRequest":"abc"}',
        now=1_800_000_000,
    )
    header, claims, signature = token.split(".")
    assert _decode(header)["alg"] == "HS256"
    body = _decode(claims)
    assert body["v-c-jwt-version"] == "2"
    assert body["request-host"] == "apitest.cybersource.com"
    assert body["request-method"] == "post"
    assert body["request-resource-path"] == "/acp/v1/instructions/probe/confirmations"
    assert body["v-c-merchant-id"] == "zender_sandbox"
    assert body["v-c-response-mle-kid"] == "response-key-id"
    assert signature
