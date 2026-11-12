"""The server's own certificate authority and TLS certificate (made on first start, in <data>/certs).

The CA is name-constrained to the game's four NTL hostnames (the only HTTPS names the game uses): players
who trust it (the /join script) can't be fooled by it for any other website, Konami's real sites included.
A CA from an older version (no or broader constraint) is replaced, and the startup banner tells the host
that players set up with the old join line must run it again (mod users: nothing to do; the mod accepts
the server's certificate for these four names by itself).
"""
import datetime, ipaddress, logging, os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger("pesserver.certs")
NTL = ["ntl.service.konami.net", "ntleu.service.konami.net", "ntlus.service.konami.net", "ntljp.service.konami.net"]
CA_REPLACED = False          # set when an old CA was swapped out this start (shown in the banner)


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write(path, data, private=False):
    """Whole or not at all (a start that is cut short must not leave half a key behind)."""
    with open(os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644), "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def _pem_key(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                           serialization.NoEncryption())


def ca_ok(path):
    """True if the CA at path exists and carries the current name constraint."""
    try:
        ca = x509.load_pem_x509_certificate(open(path, "rb").read())
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    return x509.DNSName("ntljp.service.konami.net") in (nc.permitted_subtrees or [])


def _leaf_ok(path, ca_path):
    """The server certificate exists, was signed by the current CA's key and has a month or more left.
    (The name alone isn't enough: a replaced CA has the same name as the old one.)"""
    try:
        certs = x509.load_pem_x509_certificates(open(path, "rb").read())
        ca = x509.load_pem_x509_certificate(open(ca_path, "rb").read())
        leaf = certs[0]
        leaf.verify_directly_issued_by(ca)
        with open(os.path.join(os.path.dirname(path), "server.key"), "rb") as f:
            if serialization.load_pem_private_key(f.read(), None).public_key().public_numbers() != leaf.public_key().public_numbers():
                return False                        # (its key is from another write)
    except Exception:  # noqa: BLE001 - unreadable, not a certificate, another issuer, a bad signature
        return False
    left = leaf.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
    return left > datetime.timedelta(days=30)


def _ca_key_ok(d):
    """ca.key is there and is the key of ca.crt."""
    try:
        ca = x509.load_pem_x509_certificate(open(os.path.join(d, "ca.crt"), "rb").read())
        with open(os.path.join(d, "ca.key"), "rb") as f:
            return serialization.load_pem_private_key(f.read(), None).public_key().public_numbers() == ca.public_key().public_numbers()
    except Exception:  # noqa: BLE001
        return False


def _make_ca(d):
    k = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PES2021 Private Server CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    nc = x509.NameConstraints(permitted_subtrees=[x509.DNSName(n) for n in NTL],
                              excluded_subtrees=[x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
                                                 x509.IPAddress(ipaddress.ip_network("::/0"))])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(nc, critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
            .sign(k, hashes.SHA256()))
    _write(os.path.join(d, "ca.key"), _pem_key(k), private=True)
    _write(os.path.join(d, "ca.crt"), cert.public_bytes(serialization.Encoding.PEM))


def _make_leaf(d):
    ca_pem = open(os.path.join(d, "ca.crt"), "rb").read()
    ca = x509.load_pem_x509_certificate(ca_pem)
    ca_key = serialization.load_pem_private_key(open(os.path.join(d, "ca.key"), "rb").read(), None)
    k = _key()
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NTL[0])]))
            .issuer_name(ca.subject).public_key(k.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in NTL]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(os.path.join(d, "server.key"), _pem_key(k), private=True)
    _write(os.path.join(d, "server.crt"), cert.public_bytes(serialization.Encoding.PEM) + ca_pem)


def _make_admin(d):
    """Self-signed certificate for the admin page over HTTPS (opened from the internet). Browsers warn once,
    since nobody vouches for it; the admin page shows its fingerprint so the host can check it."""
    k = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PES 2021 server admin page")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(k, hashes.SHA256()))
    _write(os.path.join(d, "admin.key"), _pem_key(k), private=True)
    _write(os.path.join(d, "admin.crt"), cert.public_bytes(serialization.Encoding.PEM))


def admin_fingerprint(d):
    """SHA-256 of the admin certificate, as browsers show it (AB:CD:...)."""
    try:
        cert = x509.load_pem_x509_certificate(open(os.path.join(d, "admin.crt"), "rb").read())
    except (OSError, ValueError):
        return ""
    return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))


def ensure(d):
    """Create (or renew) <d>/ca.crt, <d>/server.crt + server.key (the game's network test) and
    <d>/admin.crt + admin.key (the admin page over HTTPS); returns (certfile, keyfile)."""
    global CA_REPLACED
    os.makedirs(d, exist_ok=True)
    ca = os.path.join(d, "ca.crt")
    if not (ca_ok(ca) and _ca_key_ok(d)):
        if os.path.exists(ca):
            log.warning("replacing the old certificate authority with a narrower one "
                        "(players set up with the old join line: run it again; mod users: nothing to do)")
            os.replace(ca, ca + ".old")
            CA_REPLACED = True
        log.info("creating the server certificate authority")
        _make_ca(d)
    srv = os.path.join(d, "server.crt")
    if not _leaf_ok(srv, ca):
        log.info("issuing the server certificate")
        _make_leaf(d)
    if not os.path.exists(os.path.join(d, "admin.crt")):
        _make_admin(d)
    return srv, os.path.join(d, "server.key")
