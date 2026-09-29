"""Credential-bearing Visa/Cybersource Intelligent Commerce sandbox transport.

This module implements the current Cybersource JWT-v2 + Message-Level Encryption
(MLE) path for the *test* host only. It intentionally refuses production hosts.

Credentials are supplied by the caller (for example GitHub Actions secrets) and
are never accepted inside an agent-visible ActionRequest payload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


CYBERSOURCE_TEST_HOST = "apitest.cybersource.com"
CYBERSOURCE_TEST_BASE_URL = f"https://{CYBERSOURCE_TEST_HOST}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _kid_from_cert(cert: x509.Certificate) -> str:
    attrs = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    if attrs and attrs[0].value:
        return attrs[0].value
    return str(cert.serial_number)


@dataclass(frozen=True)
class CybersourceMLEMaterial:
    response_private_key: rsa.RSAPrivateKey
    response_certificate: x509.Certificate
    request_certificate: x509.Certificate


def load_mle_material(p12_b64: str, password: str) -> CybersourceMLEMaterial:
    raw = base64.b64decode(p12_b64, validate=True)
    private_key, certificate, additional = pkcs12.load_key_and_certificates(
        raw, password.encode("utf-8")
    )
    if not isinstance(private_key, rsa.RSAPrivateKey) or certificate is None:
        raise ValueError("Response MLE P12 must contain an RSA private key and certificate")
    candidates = list(additional or [])
    if not candidates:
        raise ValueError("Response MLE P12 did not contain the Cybersource SJC certificate")
    sjc = None
    for cert in candidates:
        subject = cert.subject.rfc4514_string().upper()
        if "SJC" in subject or "CYBERSOURCE" in subject:
            sjc = cert
            break
    if sjc is None:
        sjc = candidates[0]
    if not isinstance(sjc.public_key(), rsa.RSAPublicKey):
        raise ValueError("Cybersource SJC certificate must contain an RSA public key")
    return CybersourceMLEMaterial(private_key, certificate, sjc)


def encrypt_request_jwe(payload: Mapping[str, Any], cert: x509.Certificate) -> str:
    public_key = cert.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("request MLE certificate must contain RSA public key")
    header = {
        "alg": "RSA-OAEP-256",
        "enc": "A256GCM",
        "cty": "JWT",
        "kid": _kid_from_cert(cert),
    }
    protected = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    cek = os.urandom(32)
    encrypted_key = public_key.encrypt(
        cek,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    iv = os.urandom(12)
    plaintext = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
    encrypted = AESGCM(cek).encrypt(iv, plaintext, protected.encode("ascii"))
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return ".".join(
        [
            protected,
            _b64url(encrypted_key),
            _b64url(iv),
            _b64url(ciphertext),
            _b64url(tag),
        ]
    )


def decrypt_response_jwe(compact: str, private_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    parts = compact.split(".")
    if len(parts) != 5:
        raise ValueError("expected compact JWE with five parts")
    protected, encrypted_key_b64, iv_b64, ciphertext_b64, tag_b64 = parts
    header = json.loads(_b64url_decode(protected))
    if header.get("alg") != "RSA-OAEP-256" or header.get("enc") != "A256GCM":
        raise ValueError("unexpected response JWE algorithms")
    cek = private_key.decrypt(
        _b64url_decode(encrypted_key_b64),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    plaintext = AESGCM(cek).decrypt(
        _b64url_decode(iv_b64),
        _b64url_decode(ciphertext_b64) + _b64url_decode(tag_b64),
        protected.encode("ascii"),
    )
    value = json.loads(plaintext)
    if not isinstance(value, dict):
        raise ValueError("decrypted Cybersource response was not a JSON object")
    return value


def build_jwt_v2(
    *,
    key_id: str,
    shared_secret_b64: str,
    merchant_id: str,
    response_mle_kid: str,
    method: str,
    resource_path: str,
    http_body: bytes,
    now: int | None = None,
) -> str:
    now = int(time.time()) if now is None else int(now)
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    claims = {
        "digestAlgorithm": "SHA-256",
        "digest": base64.b64encode(hashlib.sha256(http_body).digest()).decode("ascii"),
        "exp": now + 120,
        "iat": now,
        "iss": merchant_id,
        "jti": str(uuid.uuid4()),
        "request-host": CYBERSOURCE_TEST_HOST,
        "request-method": method.lower(),
        "request-resource-path": resource_path.lower(),
        "v-c-jwt-version": "2",
        "v-c-merchant-id": merchant_id,
        "v-c-response-mle-kid": response_mle_kid,
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}"
    secret = base64.b64decode(shared_secret_b64, validate=True)
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


class CybersourceSandboxTransport:
    """JWT-v2/MLE transport pinned to apitest.cybersource.com."""

    def __init__(
        self,
        *,
        merchant_id: str,
        key_id: str,
        shared_secret_b64: str,
        response_mle_kid: str,
        response_mle_p12_b64: str,
        response_mle_password: str,
        timeout_seconds: float = 20.0,
    ):
        self.merchant_id = merchant_id
        self.key_id = key_id
        self.shared_secret_b64 = shared_secret_b64
        self.response_mle_kid = response_mle_kid
        self.mle = load_mle_material(response_mle_p12_b64, response_mle_password)
        self.timeout_seconds = timeout_seconds

    def post(self, resource_path: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        if not resource_path.startswith("/acp/"):
            raise ValueError("Cybersource sandbox transport only permits /acp/ paths")
        encrypted_request = encrypt_request_jwe(payload, self.mle.request_certificate)
        body = json.dumps({"encryptedRequest": encrypted_request}, separators=(",", ":")).encode(
            "utf-8"
        )
        token = build_jwt_v2(
            key_id=self.key_id,
            shared_secret_b64=self.shared_secret_b64,
            merchant_id=self.merchant_id,
            response_mle_kid=self.response_mle_kid,
            method="post",
            resource_path=resource_path,
            http_body=body,
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                CYBERSOURCE_TEST_BASE_URL + resource_path,
                content=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "SafeAgent-Visa-Sandbox/0.1",
                },
            )
        try:
            outer = response.json()
        except Exception:
            outer = {"raw": response.text[:2000]}
        if isinstance(outer, dict) and isinstance(outer.get("encryptedResponse"), str):
            decrypted = decrypt_response_jwe(
                outer["encryptedResponse"], self.mle.response_private_key
            )
            return response.status_code, decrypted
        if not isinstance(outer, dict):
            outer = {"response": outer}
        return response.status_code, outer

    def confirm_transaction(self, instruction_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        status, response = self.post(
            f"/acp/v1/instructions/{instruction_id}/confirmations", payload
        )
        if status < 200 or status >= 300:
            raise httpx.HTTPStatusError(
                f"Cybersource sandbox returned HTTP {status}",
                request=httpx.Request("POST", CYBERSOURCE_TEST_BASE_URL),
                response=httpx.Response(status, json=response),
            )
        return response


__all__ = [
    "CYBERSOURCE_TEST_BASE_URL",
    "CYBERSOURCE_TEST_HOST",
    "CybersourceMLEMaterial",
    "CybersourceSandboxTransport",
    "build_jwt_v2",
    "decrypt_response_jwe",
    "encrypt_request_jwe",
    "load_mle_material",
]
