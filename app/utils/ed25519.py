"""Ed25519 signatures (RFC 8032), in pure Python.

The updater uses it to check that an update was published by the developer:
each release carries update.json and a signature of it, made with a private
key that never leaves the developer's PC (tools/release_sign.py), and the app
checks it against the public key built into it (config.UPDATE_PUBLIC_KEY).
Nothing in the app's bundle provides Ed25519, and a dependency for one
verification per update isn't worth it: this is the RFC's own reference
construction, checked against its test vectors and against an independent
implementation (Node's crypto) in tests/test_updater.py.

Speed is irrelevant here (one verify per update check, a fraction of a
second); side channels too -- the app only ever *verifies*, which uses no
secret. Signing (tools/release_sign.py) runs on the developer's own PC.
"""
import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _inv(x):
    return pow(x, _P - 2, _P)


# Points in extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, xy = T/Z.
def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 % _P == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(p):
    zinv = _inv(p[2])
    x = p[0] * zinv % _P
    y = p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed):
    if len(seed) != 32:
        raise ValueError("an Ed25519 private key is 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed):
    """The 32-byte public key for a 32-byte private key (seed)."""
    a, _ = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed, message):
    """A 64-byte signature of `message` (bytes)."""
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = int.from_bytes(_sha512(prefix + message), "little") % _L
    big_r = _compress(_mul(r, _G))
    h = int.from_bytes(_sha512(big_r + pub + message), "little") % _L
    s = (r + h * a) % _L
    return big_r + int.to_bytes(s, 32, "little")


def verify(public, message, signature):
    """True only if `signature` is a valid signature of `message` by `public`."""
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        a = _decompress(public)
        r = _decompress(signature[:32])
        if a is None or r is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        h = int.from_bytes(_sha512(signature[:32] + public + message), "little") % _L
        return _equal(_mul(s, _G), _add(r, _mul(h, a)))
    except Exception:  # noqa: BLE001 -- any malformed input is simply "not valid"
        return False
