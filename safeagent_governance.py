"""
safeagent_governance.py
-----------------------
Additive governance layer for SafeAgent.

Provides:
  - BIP-340 Schnorr signed claim envelopes (admission_invariant)
  - OpenTimestamps Bitcoin anchoring (anchoring_invariant)
  - Offline-verifiable receipts compatible with babyblueviper1/preaction-governance-conformance

Zero changes to existing SafeAgent behavior. All new fields are additive.
Pure stdlib BIP-340 implementation — zero extra dependencies for signing/verifying.

Key generation (run once, store as Railway env vars):
    python safeagent_governance.py --keygen
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BIP-340 Schnorr — pure stdlib, zero dependencies
# Reference: https://github.com/bitcoin/bips/blob/master/bip-0340/reference.py
# ---------------------------------------------------------------------------

_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(P1, P2):
    if P1 is None: return P2
    if P2 is None: return P1
    if P1[0] == P2[0] and P1[1] != P2[1]: return None
    if P1 == P2:
        lam = (3 * P1[0] * P1[0] * pow(2 * P1[1], _P - 2, _P)) % _P
    else:
        lam = ((P2[1] - P1[1]) * pow(P2[0] - P1[0], _P - 2, _P)) % _P
    x3 = (lam * lam - P1[0] - P2[0]) % _P
    return x3, (lam * (P1[0] - x3) - P1[1]) % _P


def _point_mul(P1, n):
    R = None
    for i in range(256):
        if (n >> i) & 1: R = _point_add(R, P1)
        P1 = _point_add(P1, P1)
    return R


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    h = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(h + h + msg).digest()


def _bip340_sign(privkey_bytes: bytes, msg32: bytes) -> bytes:
    """BIP-340 Schnorr signature. Returns 64 bytes."""
    aux = secrets.token_bytes(32)
    d0 = int.from_bytes(privkey_bytes, "big")
    P1 = _point_mul(_G, d0)
    d = d0 if P1[1] % 2 == 0 else _N - d0
    t = d ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux), "big")
    rand = _tagged_hash(
        "BIP0340/nonce",
        t.to_bytes(32, "big") + P1[0].to_bytes(32, "big") + msg32,
    )
    k0 = int.from_bytes(rand, "big") % _N
    R = _point_mul(_G, k0)
    k = k0 if R[1] % 2 == 0 else _N - k0
    e = (
        int.from_bytes(
            _tagged_hash(
                "BIP0340/challenge",
                R[0].to_bytes(32, "big") + P1[0].to_bytes(32, "big") + msg32,
            ),
            "big",
        )
        % _N
    )
    return R[0].to_bytes(32, "big") + ((k + e * d) % _N).to_bytes(32, "big")


def _bip340_verify(pubkey_x_bytes: bytes, msg32: bytes, sig64: bytes) -> bool:
    """BIP-340 Schnorr verify. pubkey_x_bytes = 32-byte x-only public key."""
    try:
        r = int.from_bytes(sig64[:32], "big")
        s = int.from_bytes(sig64[32:], "big")
        if r >= _P or s >= _N:
            return False
        px = int.from_bytes(pubkey_x_bytes, "big")
        P_y_sq = (pow(px, 3, _P) + 7) % _P
        P_y = pow(P_y_sq, (_P + 1) // 4, _P)
        if pow(P_y, 2, _P) != P_y_sq:
            return False
        Py = P_y if P_y % 2 == 0 else _P - P_y
        Ppt = (px, Py)
        e = (
            int.from_bytes(
                _tagged_hash(
                    "BIP0340/challenge",
                    r.to_bytes(32, "big") + pubkey_x_bytes + msg32,
                ),
                "big",
            )
            % _N
        )
        R = _point_add(_point_mul(_G, s), _point_mul(Ppt, _N - e))
        if R is None or R[1] % 2 != 0 or R[0] != r:
            return False
        return True
    except Exception:
        return False


def _privkey_to_xonly_pubkey(privkey_bytes: bytes) -> bytes:
    """Return the 32-byte x-only BIP-340 public key."""
    d0 = int.from_bytes(privkey_bytes, "big")
    px = _point_mul(_G, d0)[0]
    return px.to_bytes(32, "big")


# ---------------------------------------------------------------------------
# JCS (JSON Canonical Serialization) — RFC 8785, pure stdlib
# ---------------------------------------------------------------------------

def _jcs(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def generate_keypair() -> Tuple[str, str]:
    """
    Generate a fresh BIP-340 keypair for SafeAgent governance.
    Prints private key (set as SAFEAGENT_GOVERNANCE_PRIVKEY in Railway)
    and public key (publish in llms.txt, README, /.well-known/safeagent.json).
    """
    privkey_bytes = secrets.token_bytes(32)
    privkey_hex = privkey_bytes.hex()
    pubkey_hex = _privkey_to_xonly_pubkey(privkey_bytes).hex()
    print(f"SAFEAGENT_GOVERNANCE_PRIVKEY={privkey_hex}")
    print(f"  → keep secret, set as Railway env var")
    print(f"SAFEAGENT_GOVERNANCE_PUBKEY={pubkey_hex}")
    print(f"  → publish this everywhere (llms.txt, README, safeagent.json)")
    return privkey_hex, pubkey_hex


def _load_privkey() -> Optional[bytes]:
    raw = os.getenv("SAFEAGENT_GOVERNANCE_PRIVKEY", "").strip()
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
        if len(key) != 32:
            log.warning("SAFEAGENT_GOVERNANCE_PRIVKEY must be 32 bytes (64 hex chars)")
            return None
        return key
    except Exception as e:
        log.warning("SAFEAGENT_GOVERNANCE_PRIVKEY parse error: %s", e)
        return None


def get_pubkey_hex() -> Optional[str]:
    """Return the governance public key hex, or None if not configured."""
    # Prefer explicit env var (faster, no derivation needed at runtime)
    explicit = os.getenv("SAFEAGENT_GOVERNANCE_PUBKEY", "").strip()
    if explicit:
        return explicit
    privkey = _load_privkey()
    if privkey is None:
        return None
    return _privkey_to_xonly_pubkey(privkey).hex()


# ---------------------------------------------------------------------------
# Canonical envelope
# ---------------------------------------------------------------------------

def build_envelope(
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at_ms: int,
) -> Dict[str, Any]:
    """
    Build a deterministic claim envelope.
    envelope_hash = SHA-256(JCS(envelope)) — content-addressed.
    """
    envelope = {
        "action": action,
        "action_ref": request_id,
        "actor_id": agent_id or "",
        "canonicalization": "jcs-sha256-v1",
        "claimed_at_ms": claimed_at_ms,
        "issuer": "safeagent",
    }
    canonical_bytes = _jcs(envelope)
    envelope_hash = _sha256hex(canonical_bytes)
    return {
        "envelope": envelope,
        "canonical_bytes_utf8": canonical_bytes.decode(),
        "envelope_hash": envelope_hash,
    }


# ---------------------------------------------------------------------------
# Sign
# ---------------------------------------------------------------------------

def sign_envelope(envelope_hash: str) -> Optional[Dict[str, Any]]:
    """
    BIP-340 sign the envelope_hash. Returns None if no key configured.
    The returned dict is safe to embed in the PROCEED response.
    Third-party offline verification:
        msg32   = bytes.fromhex(envelope_hash)
        sig64   = bytes.fromhex(governance["signature"])
        pubkey  = bytes.fromhex(governance["verifier_pubkey"])
        # use any BIP-340 verifier, e.g. python-bitcoinlib or the
        # pure-stdlib verifier in this module: _bip340_verify(pubkey, msg32, sig64)
    """
    privkey = _load_privkey()
    if privkey is None:
        return None

    pubkey_hex = _privkey_to_xonly_pubkey(privkey).hex()
    msg32 = bytes.fromhex(envelope_hash)
    sig = _bip340_sign(privkey, msg32)

    return {
        "verifier_pubkey": pubkey_hex,
        "signature": sig.hex(),
        "sig_scheme": "bip340-schnorr",
        "envelope_hash": envelope_hash,
    }


# ---------------------------------------------------------------------------
# OpenTimestamps anchoring (background task, network call)
# ---------------------------------------------------------------------------

def stamp_envelope(envelope_hash: str) -> Optional[bytes]:
    """
    Submit envelope_hash to OpenTimestamps Bitcoin calendar servers.
    Returns .ots proof bytes (incomplete — Bitcoin confirmation is async).
    Run in a background task, never in the /claim hot path.
    """
    try:
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.calendar import RemoteCalendar
        import opentimestamps.core.serialize as ots_ser

        digest = bytes.fromhex(envelope_hash) if envelope_hash else bytes(32)

        # Build a DetachedTimestampFile-style timestamp
        # The calendar expects the raw digest bytes, not a Timestamp object
        ts = Timestamp(digest)

        calendars = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
        ]

        for cal_url in calendars:
            try:
                cal = RemoteCalendar(cal_url)
                # submit() takes the digest bytes directly in newer versions
                promise = cal.submit(digest)
                # Attach the promise to our timestamp
                ts.merge(promise)
                log.info("OTS: submitted %s to %s", envelope_hash[:16], cal_url)
                ctx = ots_ser.BytesSerializationContext()
                ts.serialize(ctx)
                return ctx.getbytes()
            except Exception as e:
                log.warning("OTS calendar %s failed: %s", cal_url, e)

        log.warning("OTS: all calendars failed for %s", envelope_hash[:16])
        return None

    except Exception as e:
        log.warning("OTS stamping error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Full governance receipt — background task called after PROCEED is sent
# ---------------------------------------------------------------------------

async def attach_governance_async(
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at_ms: int,
    store,
) -> None:
    """
    Background task: sign + OTS stamp + persist.
    Called after the PROCEED response is already on the wire.
    """
    try:
        built = build_envelope(request_id, action, agent_id, claimed_at_ms)
        envelope_hash = built["envelope_hash"]

        sig_data = sign_envelope(envelope_hash)
        if sig_data is None:
            log.info("governance: signing skipped (SAFEAGENT_GOVERNANCE_PRIVKEY not set)")
            return

        ots_bytes = stamp_envelope(envelope_hash)
        ots_hex = ots_bytes.hex() if ots_bytes else None

        if hasattr(store, "attach_governance"):
            store.attach_governance(
                request_id=request_id,
                envelope_hash=envelope_hash,
                canonical_bytes=built["canonical_bytes_utf8"],
                signature=sig_data["signature"],
                verifier_pubkey=sig_data["verifier_pubkey"],
                ots_proof_hex=ots_hex,
            )
            log.info(
                "governance: attached to %s (ots=%s)",
                request_id[:8],
                "yes" if ots_hex else "pending",
            )
        else:
            log.info("governance: store lacks attach_governance — skipping persist")

    except Exception as e:
        log.warning("governance background task error: %s", e)


# ---------------------------------------------------------------------------
# Offline verifier — mirrors babyblueviper1/preaction-governance-conformance
# ---------------------------------------------------------------------------

def verify_claim_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Offline verification of a SafeAgent governance receipt.
    No network calls. Mirrors the three invariants:

      chain_invariant     — envelope hash recomputes correctly
      admission_invariant — independent party signed the envelope hash
      anchoring_invariant — OTS proof present (Bitcoin confirmation async)

    receipt keys:
        canonical_bytes_utf8, envelope_hash,
        verifier_pubkey, signature,
        envelope (dict with actor_id),
        ots_proof_hex (optional)
    """
    errors = []

    # chain_invariant
    chain_ok = False
    try:
        cb = receipt["canonical_bytes_utf8"].encode()
        recomputed = _sha256hex(cb)
        declared = receipt["envelope_hash"]
        if recomputed == declared:
            chain_ok = True
        else:
            errors.append(f"chain: hash mismatch recomputed={recomputed} declared={declared}")
    except Exception as e:
        errors.append(f"chain error: {e}")

    # admission_invariant
    admission_ok = False
    try:
        sig64 = bytes.fromhex(receipt["signature"])
        msg32 = bytes.fromhex(receipt["envelope_hash"])
        pubkey_hex = receipt["verifier_pubkey"]
        actor_id = receipt.get("envelope", {}).get("actor_id", "")
        if pubkey_hex == actor_id:
            errors.append("admission: signer == actor (self-attested, fails independence)")
        else:
            pubkey_x = bytes.fromhex(pubkey_hex)
            if _bip340_verify(pubkey_x, msg32, sig64):
                admission_ok = True
            else:
                errors.append("admission: BIP-340 signature verification failed")
    except Exception as e:
        errors.append(f"admission error: {e}")

    # anchoring_invariant
    anchoring_ok = None
    if receipt.get("ots_proof_hex"):
        anchoring_ok = True  # proof submitted; run `ots verify` for full Bitcoin confirmation
    else:
        errors.append("anchoring: no OTS proof present (pending submission or not configured)")

    return {
        "chain_invariant": chain_ok,
        "admission_invariant": admission_ok,
        "anchoring_invariant": anchoring_ok,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# OTS confirmation check
# ---------------------------------------------------------------------------

def check_ots_confirmation(ots_proof_hex: str, envelope_hash: str = "") -> Optional[Dict[str, Any]]:
    """
    Check if an OTS proof has been Bitcoin-confirmed.
    Returns {confirmed: bool, block_time: str|None} or None on error.
    Calls back to the OTS calendar to upgrade the incomplete timestamp.
    """
    try:
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.calendar import RemoteCalendar
        import opentimestamps.core.serialize as ots_ser
        import opentimestamps.core.serialize as ots_ser2

        proof_bytes = bytes.fromhex(ots_proof_hex)

        # Deserialize the incomplete timestamp
        # Timestamp.deserialize(ctx, initial_msg) where initial_msg is the digest
        digest = bytes.fromhex(envelope_hash) if envelope_hash else bytes(32)
        ctx = ots_ser2.BytesDeserializationContext(proof_bytes)
        ts = Timestamp.deserialize(ctx, digest)

        calendars = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
        ]

        upgraded = False
        for cal_url in calendars:
            try:
                cal = RemoteCalendar(cal_url)
                cal.upgrade(ts)
                upgraded = True
                break
            except Exception as e:
                log.debug("OTS upgrade calendar %s: %s", cal_url, e)

        if not upgraded:
            return {"confirmed": False, "block_time": None}

        # Check if any attestation is present (Bitcoin confirmation)
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.core.timestamp import BitcoinBlockHeaderAttestation

        def _find_bitcoin_attestation(timestamp):
            for attestation in timestamp.attestations:
                if isinstance(attestation, BitcoinBlockHeaderAttestation):
                    return attestation
            for op, stamp in timestamp.ops.items():
                result = _find_bitcoin_attestation(stamp)
                if result:
                    return result
            return None

        attestation = _find_bitcoin_attestation(ts)
        if attestation:
            # Block height confirmed — use height as proxy for block time
            block_time = f"bitcoin-block-{attestation.height}"
            return {"confirmed": True, "block_time": block_time}

        return {"confirmed": False, "block_time": None}

    except Exception as e:
        log.warning("OTS confirmation check error: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if "--keygen" in sys.argv:
        generate_keypair()
    elif "--verify" in sys.argv:
        import json as _json
        path = sys.argv[sys.argv.index("--verify") + 1]
        with open(path) as f:
            receipt = _json.load(f)
        result = verify_claim_receipt(receipt)
        print(_json.dumps(result, indent=2))
    else:
        print("Usage: python safeagent_governance.py --keygen")
        print("       python safeagent_governance.py --verify receipt.json")
