#!/usr/bin/env python3
"""Send adverts and direct messages as *other* MeshCore identities through a
repeater's web panel (firmware built with WEBCONFIG_RAW_TX).

All key handling, signing and encryption happens here. The node only receives
finished packet payloads over:

    POST /api/advert   {"advert": hex, "route": "FLOOD" | "ZERO-HOP"}
    POST /api/send     {"payload": hex, "path": hex, "path_hash_size": n}
    GET  /api/tx/result?id=N

Examples:
    webui_tx.py keygen --out bot.json --name "Hjallese Bot"
    webui_tx.py advert --key bot.json                      # FLOOD (default)
    webui_tx.py advert --key bot.json --route ZERO-HOP
    webui_tx.py send --key bot.json --to <64-hex pubkey> --text "hej"

Auth: run `txtoken new` once in the panel's CLI tab and pass the printed token
with --token or MESHCORE_TX_TOKEN. Without a token the script logs in with the
admin password instead.

The recipient only accepts a message after it has heard (and saved) this
identity's advert, so advertise first. Replies and ACKs go to this identity,
which no node can decrypt, so the sender's side never sees "delivered".

Needs: python-cryptography (AES). Everything else is stdlib.
"""

import argparse
import getpass
import hashlib
import hmac
import http.cookiejar
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover
    sys.exit("needs the 'cryptography' package (pacman -S python-cryptography / pip install cryptography)")

# ---------------------------------------------------------------------------
# Ed25519 / X25519, matching lib/ed25519 (orlp) as used by MeshCore.
# MeshCore private keys are 64 bytes: clamped scalar (32) + hash prefix (32),
# i.e. SHA-512(seed) with the first half clamped. There is no seed to recover,
# so standard Ed25519 libraries can't sign with them; this does it by hand.
# ---------------------------------------------------------------------------

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _inv(x):
    return pow(x, P - 2, P)


def _recover_x(y, sign):
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        if sign:
            raise ValueError("invalid point")
        return 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        raise ValueError("invalid point")
    if (x & 1) != sign:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % P)  # extended coords


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _encode(pt):
    zi = _inv(pt[2])
    x, y = pt[0] * zi % P, pt[1] * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def derive_pub(prv):
    return _encode(_mul(int.from_bytes(prv[:32], "little"), _B))


def keypair_from_seed(seed):
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248
    h[31] &= 63
    h[31] |= 64
    prv = bytes(h)
    return prv, derive_pub(prv)


def sign(prv, pub, msg):
    r = int.from_bytes(hashlib.sha512(prv[32:] + msg).digest(), "little") % L
    R = _encode(_mul(r, _B))
    k = int.from_bytes(hashlib.sha512(R + pub + msg).digest(), "little") % L
    a = int.from_bytes(prv[:32], "little")
    s = (r + k * a) % L
    return R + s.to_bytes(32, "little")


def verify(pub, msg, sig):
    y = int.from_bytes(pub, "little")
    A = (_recover_x(y & ((1 << 255) - 1), y >> 255), y & ((1 << 255) - 1))
    A = (A[0], A[1], 1, A[0] * A[1] % P)
    s = int.from_bytes(sig[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % L
    lhs = _encode(_mul(s, _B))
    rhs = _encode(_add(_decode(sig[:32]), _mul(k, A)))
    return lhs == rhs


def _decode(b):
    y = int.from_bytes(b, "little")
    yy = y & ((1 << 255) - 1)
    x = _recover_x(yy, y >> 255)
    return (x, yy, 1, x * yy % P)


def shared_secret(prv, other_pub):
    """ed25519_key_exchange(): X25519 with our clamped scalar against the
    peer's Ed25519 key converted to Montgomery u = (1+y)/(1-y)."""
    e = bytearray(prv[:32])
    e[0] &= 248
    e[31] &= 63
    e[31] |= 64
    k = int.from_bytes(e, "little")
    y = int.from_bytes(other_pub, "little") & ((1 << 255) - 1)
    u = (1 + y) * _inv((1 - y) % P) % P

    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in range(254, -1, -1):
        bit = (k >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e_ = (aa - bb) % P
        c, d = (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e_ * (aa + 121665 * e_) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * _inv(z2) % P).to_bytes(32, "little")


# ---------------------------------------------------------------------------
# MeshCore payloads
# ---------------------------------------------------------------------------

ADV_TYPES = {"none": 0, "chat": 1, "repeater": 2, "room": 3, "sensor": 4}
ADV_LATLON = 0x10
ADV_NAME = 0x80
MAX_ADVERT_DATA = 32
MAX_TEXT_LEN = 160
MAX_PACKET_PAYLOAD = 184


def advert_app_data(adv_type, name, lat=None, lon=None):
    flags = ADV_TYPES[adv_type]
    out = b""
    if lat is not None and lon is not None:
        flags |= ADV_LATLON
        out += struct.pack("<ii", int(lat * 1e6), int(lon * 1e6))
    if name:
        nb = name.encode("utf-8")[: MAX_ADVERT_DATA - 1 - len(out)]
        nb = nb.decode("utf-8", "ignore").encode("utf-8")  # don't split a UTF-8 char
        if nb:
            flags |= ADV_NAME
            out += nb
    return bytes([flags]) + out


def build_advert(prv, pub, app_data, timestamp=None):
    ts = struct.pack("<I", int(time.time()) if timestamp is None else timestamp)
    sig = sign(prv, pub, pub + ts + app_data)
    return pub + ts + sig + app_data


def encrypt_then_mac(secret, plain):
    pad = (-len(plain)) % 16
    enc = Cipher(algorithms.AES(secret[:16]), modes.ECB()).encryptor()
    ct = enc.update(plain + b"\0" * pad) + enc.finalize()
    mac = hmac.new(secret, ct, hashlib.sha256).digest()[:2]
    return mac + ct


def build_txt_msg(prv, pub, dest_pub, text, attempt=0, timestamp=None):
    tb = text.encode("utf-8")
    if len(tb) > MAX_TEXT_LEN:
        raise ValueError(f"text is {len(tb)} bytes, max {MAX_TEXT_LEN}")
    ts = int(time.time()) if timestamp is None else timestamp
    plain = struct.pack("<I", ts) + bytes([attempt & 3]) + tb
    secret = shared_secret(prv, dest_pub)
    payload = dest_pub[:1] + pub[:1] + encrypt_then_mac(secret, plain)
    assert len(payload) <= MAX_PACKET_PAYLOAD
    return payload


# ---------------------------------------------------------------------------
# Key files
# ---------------------------------------------------------------------------

def load_key(path):
    with open(path) as f:
        data = json.load(f)
    prv = bytes.fromhex(data["private_key"])
    if len(prv) != 64:
        sys.exit(f"{path}: private_key must be 64 bytes (128 hex chars)")
    pub = derive_pub(prv)
    if "public_key" in data and bytes.fromhex(data["public_key"]) != pub:
        sys.exit(f"{path}: public_key does not match private_key")
    return prv, pub, data.get("name", "")


def save_key(path, prv, pub, name):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"name": name, "public_key": pub.hex(), "private_key": prv.hex()}, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Web panel client
# ---------------------------------------------------------------------------

class Panel:
    """Auth with the node's TX token (X-Api-Token, from `txtoken new`) when one is
    given; otherwise log in with the admin password, which also logs the
    browser out of the panel (the node keeps a single session)."""

    def __init__(self, host, token=None, password=None, timeout=10):
        self.base = host if host.startswith("http") else f"http://{host}"
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        if token:
            self.headers["X-Api-Token"] = token
        else:
            self._call("POST", "/api/login", {"password": password})

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=self.headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", "")
            except Exception:
                err = ""
            if e.code == 404:
                err = "endpoint missing - is the firmware built with WEBCONFIG_RAW_TX?"
            elif e.code == 401 and "X-Api-Token" in self.headers:
                err = "token rejected - check it, or run `txtoken new` in the panel CLI"
            sys.exit(f"{method} {path}: HTTP {e.code} {err}")

    def submit(self, path, body, wait_s=8.0):
        _, res = self._call("POST", path, body)
        tx_id = res["id"]
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(0.25)
            _, st = self._call("GET", f"/api/tx/result?id={tx_id}")
            if st.get("state") == "done":
                return st
            if st.get("state") == "unknown":
                break
        return {"id": tx_id, "state": "timeout", "ok": False, "msg": "no result from node"}


def connect(args):
    token = args.token or os.environ.get("MESHCORE_TX_TOKEN")
    if token:
        return Panel(args.host, token=token)
    pw = args.password or os.environ.get("MESHCORE_WEB_PASSWORD") or getpass.getpass("Panel admin password: ")
    return Panel(args.host, password=pw)


def finish(path, body, args):
    if args.dry_run:
        print(json.dumps({"endpoint": path, "body": body}, indent=2))
        return
    st = connect(args).submit(path, body)
    print(f"{'OK' if st.get('ok') else 'FAILED'}: {st.get('msg', st)}")
    if not st.get("ok"):
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_keygen(args):
    prv, pub = keypair_from_seed(os.urandom(32))
    save_key(args.out, prv, pub, args.name or "")
    print(f"wrote {args.out} (mode 600)\npublic key: {pub.hex()}\nhash byte:  {pub[:1].hex()}")


def cmd_import(args):
    prv = bytes.fromhex(args.private_key)
    if len(prv) != 64:
        sys.exit("private key must be 64 bytes (128 hex chars), as exported by the MeshCore app")
    pub = derive_pub(prv)
    save_key(args.out, prv, pub, args.name or "")
    print(f"wrote {args.out} (mode 600)\npublic key: {pub.hex()}")


def cmd_pubkey(args):
    _, pub, name = load_key(args.key)
    print(f"name:       {name}\npublic key: {pub.hex()}\nhash byte:  {pub[:1].hex()}")


def cmd_advert(args):
    prv, pub, name = load_key(args.key)
    name = args.name or name
    for ch in "[]\\:,?*":
        if ch in name:
            print(f"warning: '{ch}' in the name may be rejected by some clients", file=sys.stderr)
    app = advert_app_data(args.type, name, args.lat, args.lon)
    advert = build_advert(prv, pub, app)
    finish("/api/advert", {"advert": advert.hex(), "route": args.route}, args)


def cmd_send(args):
    prv, pub, _ = load_key(args.key)
    dest = bytes.fromhex(args.to)
    if len(dest) != 32:
        sys.exit("--to must be the recipient's full 32-byte public key (64 hex chars)")
    payload = build_txt_msg(prv, pub, dest, args.text, attempt=args.attempt)
    body = {"payload": payload.hex()}
    if args.path:
        body["path"] = args.path
        body["path_hash_size"] = args.path_hash_size
    finish("/api/send", body, args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("MESHCORE_HOST", "192.168.1.175"))
    ap.add_argument("--token", help="TX token from `txtoken new` (or MESHCORE_TX_TOKEN); preferred")
    ap.add_argument("--password", help="panel admin password if no token (or MESHCORE_WEB_PASSWORD, or prompt)")
    ap.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen", help="create a new identity file")
    p.add_argument("--out", required=True)
    p.add_argument("--name")
    p.set_defaults(fn=cmd_keygen)

    p = sub.add_parser("import", help="store an existing 64-byte MeshCore private key")
    p.add_argument("--out", required=True)
    p.add_argument("--private-key", required=True)
    p.add_argument("--name")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("pubkey", help="show an identity's public key")
    p.add_argument("--key", required=True)
    p.set_defaults(fn=cmd_pubkey)

    p = sub.add_parser("advert", help="broadcast a signed advert for an identity")
    p.add_argument("--key", required=True)
    p.add_argument("--name", help="override the name in the key file")
    p.add_argument("--type", choices=sorted(ADV_TYPES), default="chat")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--route", default="FLOOD", type=str.upper, choices=["FLOOD", "ZERO-HOP"])
    p.set_defaults(fn=cmd_advert)

    p = sub.add_parser("send", help="send a direct text message")
    p.add_argument("--key", required=True)
    p.add_argument("--to", required=True, help="recipient public key, 64 hex chars")
    p.add_argument("--text", required=True)
    p.add_argument("--path", help="direct route as hex hop hashes (omit to flood)")
    p.add_argument("--path-hash-size", type=int, default=1, choices=[1, 2, 3])
    p.add_argument("--attempt", type=int, default=0, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_send)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
