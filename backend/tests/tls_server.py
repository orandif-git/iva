"""A small HTTPS server on localhost for integration tests of outgoing calls (self-signed certificate)."""
import asyncio
import datetime
import ipaddress
import ssl
import tempfile
import threading
import time

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_cert(dirpath: str, names=("localhost",)):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    sans = []
    for n in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            sans.append(x509.DNSName(n))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = f"{dirpath}/cert.pem", f"{dirpath}/key.pem"
    open(cert_path, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(key_path, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


class TlsServer:
    def __init__(self, app, host="127.0.0.1"):
        self.dir = tempfile.mkdtemp()
        # cert names both spellings so the URL can stay 127.0.0.1; "localhost" resolves to ::1 on macOS while uvicorn binds IPv4 only.
        self.cert, key = make_cert(self.dir, names=("localhost", "127.0.0.1"))
        self.host = host
        cfg = uvicorn.Config(app, host=host, port=0, ssl_certfile=self.cert, ssl_keyfile=key, log_level="error", lifespan="off")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve()), daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started and self.server.servers and self.server.servers[0].sockets:
                break
            time.sleep(0.05)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)

    def ssl_context(self):
        return ssl.create_default_context(cafile=self.cert)

    def url(self, path="/"):
        return f"https://{self.host}:{self.port}{path}"
