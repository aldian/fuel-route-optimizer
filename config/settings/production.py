"""Production settings profile."""
import os
import socket
from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

if not ALLOWED_HOSTS:
    raise ValueError("ALLOWED_HOSTS environment variable must be specified in production.")

# Allow Kubernetes kubelet / Cloud Load Balancer health probes that send the Pod's private IP in Host header
try:
    _pod_ip = socket.gethostbyname(socket.gethostname())
except OSError:
    _pod_ip = ""

for _extra_host in (os.environ.get("POD_IP", "").strip(), _pod_ip):
    if _extra_host and _extra_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_extra_host)

# Security Hardening
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() == "true"
SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/?$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
