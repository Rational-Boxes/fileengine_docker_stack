"""Auth verifiers for the BCF-API (§12): the shared-secret bearer verifier and the
gateway key:secret (scope "bcf") service-credential verifier."""
import base64
import hashlib
import hmac
import json
import time

from bcf_service import auth
from bcf_service.auth import Identity, make_secret_verifier, make_service_cred_verifier


def _jwt(secret: str, claims: dict) -> str:
    def seg(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    h = seg({"alg": "HS256", "typ": "JWT"})
    p = seg(claims)
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def test_bearer_verifier_accepts_valid_and_rejects_tampered():
    verify = make_secret_verifier("s3cret", default_tenant="default")
    tok = _jwt("s3cret", {"sub": "alice", "tenant": "acme", "roles": ["users"], "exp": time.time() + 60})
    ident = verify(tok)
    assert ident and ident.user == "alice" and ident.tenant == "acme"
    assert verify(_jwt("WRONG", {"sub": "alice"})) is None  # bad signature
    assert verify("not.a.jwt") is None


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._b = json.dumps(body).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_service_cred_verifier_calls_ldap_manager_and_maps_identity(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("X-internal-auth")
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp(200, {"uid": "svc-user", "tenant": "acme"})

    monkeypatch.setattr(auth.urllib.request, "urlopen", fake_urlopen)
    verify = make_service_cred_verifier("http://idm:8093/", "int-secret", scope="bcf", default_tenant="default")
    ident = verify("fek_k", "fesks_s", "acme", "10.0.0.9")
    assert ident == Identity(user="svc-user", tenant="acme", roles=[])
    assert captured["url"] == "http://idm:8093/internal/service-cred/verify"
    assert captured["auth"] == "int-secret"
    assert captured["body"] == {"key_id": "fek_k", "secret": "fesks_s", "tenant": "acme",
                                "scope": "bcf", "source_ip": "10.0.0.9"}


def test_service_cred_verifier_denies_on_error_paths(monkeypatch):
    verify = make_service_cred_verifier("http://idm:8093", "int-secret")
    # Empty inputs never call out.
    assert verify("", "s", "t") is None
    # Non-200 / missing uid / network error all deny.
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *a, **k: _FakeResp(401, {"detail": "bad"}))
    assert verify("k", "s", "t") is None
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *a, **k: _FakeResp(200, {"tenant": "t"}))
    assert verify("k", "s", "t") is None  # no uid
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(auth.urllib.request, "urlopen", boom)
    assert verify("k", "s", "t") is None
