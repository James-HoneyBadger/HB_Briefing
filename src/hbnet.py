#!/usr/bin/env python3
"""
HB Briefing - network trust boundary

Every outbound request the application makes goes through this module. Keeping the checks in
one place means the answer to "what can this program reach, and who decided that" is a small
file instead of a scatter of `urlopen` calls.

The model is a *purpose*, not a global allow/deny switch. A feed URL was typed or imported by
the user, so reaching a LAN address is legitimate. A link lifted out of feed content was chosen
by a stranger, so it may only reach the public internet. A local model server is expected on
loopback. Link-local space and cloud metadata endpoints are never acceptable: nothing this
application does needs them, and they are the classic SSRF pivot.

What this module does *not* do is claim to sandbox the process. A user-configured fetch command
(`WATCHDOG_FETCH_CMD`) and a user-configured HTTP proxy are trust decisions the operator makes;
they are documented as such rather than silently honoured.

Checks applied to every request:

  * scheme is http or https, nothing else
  * no embedded credentials (`https://user:pass@host/`)
  * no whitespace, control characters or absurd length
  * hostname is not a known metadata name
  * every address the host resolves to is permitted for the purpose, so one bad A record
    cannot be hidden behind a good one
  * the connection is pinned to the address that was validated, which closes the
    time-of-check/time-of-use gap a second resolution would reopen (when no proxy is in use)
  * redirects are followed by hand, so each hop is validated under the original purpose
  * responses are read through a byte cap

Callers see `NetworkError`, which carries a short explanation suitable for a log line.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

__all__ = [
    "NetworkError",
    "PURPOSES",
    "PURPOSE_FEED",
    "PURPOSE_ARTICLE",
    "PURPOSE_AGGREGATOR",
    "PURPOSE_AI",
    "PURPOSE_WEBHOOK",
    "MAX_REDIRECTS",
    "classify_host",
    "refers_to_local",
    "validate_url",
    "shape_ok",
    "open_url",
    "read_capped",
]

MAX_URL_LENGTH = 4096
MAX_REDIRECTS = 5
REDIRECT_CODES = (301, 302, 303, 307, 308)

PURPOSE_FEED = "feed"
PURPOSE_ARTICLE = "article"
PURPOSE_AGGREGATOR = "aggregator"
PURPOSE_AI = "ai"
PURPOSE_WEBHOOK = "webhook"

# Address kinds a purpose may reach. Everything else is refused.
#
#   feed        the user named this endpoint, so LAN and loopback feeds keep working
#   article     feed content chose this link; public internet only
#   aggregator  a fixed, published redirector; public internet only
#   ai          a local inference server is the preferred configuration
#   webhook     self-hosted ntfy/gotify commonly lives on loopback or the LAN
#
# "link_local" and "metadata" are absent everywhere on purpose, and so are the reserved,
# multicast and unspecified ranges.
PURPOSES = {
    PURPOSE_FEED: frozenset({"global", "private", "loopback"}),
    PURPOSE_ARTICLE: frozenset({"global"}),
    PURPOSE_AGGREGATOR: frozenset({"global"}),
    PURPOSE_AI: frozenset({"global", "private", "loopback"}),
    PURPOSE_WEBHOOK: frozenset({"global", "private", "loopback"}),
}

# Names that resolve to instance credentials in various clouds. Refused before DNS, because
# these hosts may resolve differently depending on where the request is made.
METADATA_HOSTS = frozenset({
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
})

# Addresses that hand out credentials or administrative access. They are refused even for
# purposes that may otherwise reach a private network.
METADATA_IPS = frozenset({
    "169.254.169.254",      # AWS, GCP, Azure, OpenStack
    "169.254.170.2",        # AWS ECS task metadata
    "100.100.100.200",      # Alibaba Cloud
    "fd00:ec2::254",        # AWS IMDS over IPv6
})

_LOOPBACK = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))
_LINK_LOCAL = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))
_PRIVATE = tuple(
    ipaddress.ip_network(net) for net in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",        # carrier-grade NAT, also used by some container networks
        "fc00::/7",             # unique local IPv6
    )
)
_RESERVED = tuple(
    ipaddress.ip_network(net) for net in (
        "0.0.0.0/8",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "2001:db8::/32",
        "::/128",
        "64:ff9b::/96",
    )
)
_MULTICAST = (ipaddress.ip_network("224.0.0.0/4"), ipaddress.ip_network("ff00::/8"))

_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")


class NetworkError(Exception):
    """A request was refused or failed a policy check, with a reason fit for a log line."""


def _address_kind(ip):
    """Classify one resolved address by what reaching it would mean."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if str(ip) in METADATA_IPS:
        return "metadata"
    if any(ip in net for net in _LOOPBACK):
        return "loopback"
    if any(ip in net for net in _LINK_LOCAL):
        return "link_local"
    if any(ip in net for net in _PRIVATE):
        return "private"
    if any(ip in net for net in _MULTICAST):
        return "multicast"
    if any(ip in net for net in _RESERVED):
        return "reserved"
    if ip.is_unspecified or ip.is_reserved or ip.is_multicast:
        return "reserved"
    return "global"


def classify_host(host):
    """Kind of address a host literal refers to, or 'name' when it needs DNS."""
    try:
        return _address_kind(ipaddress.ip_address(host))
    except ValueError:
        return "name"


def refers_to_local(host):
    """True when a hostname obviously names this machine or its LAN.

    Purely lexical: naming `localhost` in a configuration file is the user saying "my own
    machine", and answering that question must not require a DNS lookup.
    """
    name = (host or "").strip().rstrip(".").lower()
    if not name:
        return False
    if name in ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"):
        return True
    if name.endswith(".local") or name.endswith(".internal") or name.endswith(".home.arpa"):
        return True
    return classify_host(name) in ("loopback", "private")


def _purpose_set(purpose):
    try:
        return PURPOSES[purpose]
    except KeyError:
        raise NetworkError(f"internal error: unknown request purpose {purpose!r}") from None


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def shape_ok(url):
    """Return the URL when it is structurally fetchable, else raise NetworkError.

    No DNS, no policy: this is the cheap check for links that are only stored or displayed,
    such as feed item links, where resolving every one would be wasteful and would leak the
    user's reading list to a resolver. Embedded spaces are kept, because publishers ship
    them and the renderers percent-encode on the way out.
    """
    text = (url or "").strip()
    if not text:
        raise NetworkError("empty URL")
    if len(text) > MAX_URL_LENGTH:
        raise NetworkError("URL is implausibly long")
    if _CONTROL.search(text):
        raise NetworkError("URL contains control characters")
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise NetworkError("not an http(s) link")
    if parts.username or parts.password or "@" in parts.netloc:
        raise NetworkError("URL carries embedded credentials")
    return parts.geturl()


def _split(url, purpose):
    """Parse and apply the checks that need no network access."""
    text = (url or "").strip()
    if not text:
        raise NetworkError("empty URL")
    if len(text) > MAX_URL_LENGTH:
        raise NetworkError("URL is implausibly long")
    if _CONTROL_OR_SPACE.search(text):
        raise NetworkError("URL contains whitespace or control characters")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise NetworkError(f"scheme {scheme or '(none)'!r} is not allowed; use http or https")
    if not parts.hostname:
        raise NetworkError("URL has no host")
    if parts.username or parts.password or "@" in parts.netloc:
        raise NetworkError("URL carries embedded credentials")
    try:
        port = parts.port
    except ValueError:
        raise NetworkError("URL has an invalid port") from None
    host = parts.hostname.strip().rstrip(".").lower()
    if not host:
        raise NetworkError("URL has no host")
    if host in METADATA_HOSTS:
        raise NetworkError(f"{host} is a cloud metadata endpoint and is never fetched")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return urlsplit(text), scheme, host, port


def _resolve(host, port):
    """Every address `host` currently resolves to. Raises NetworkError on failure."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkError(f"could not resolve {host}: {exc.strerror or exc}") from None
    addresses = []
    for info in infos:
        attempt = info[4][0]
        if attempt not in addresses:
            addresses.append(attempt)
    if not addresses:
        raise NetworkError(f"could not resolve {host}")
    return addresses


class Target(object):
    """A URL that passed policy, plus the exact address to connect to."""

    __slots__ = ("url", "scheme", "host", "port", "address", "kind")

    def __init__(self, url, scheme, host, port, address, kind):
        self.url = url
        self.scheme = scheme
        self.host = host
        self.port = port
        self.address = address
        self.kind = kind

    def __repr__(self):
        return f"Target({self.url!r}, {self.kind}, {self.address})"


def validate_url(url, purpose):
    """Check `url` against the policy for `purpose` and pin it to one resolved address.

    All addresses are checked, not just the one that will be used, so a round-robin DNS
    answer cannot smuggle a private address past the check.
    """
    allowed = _purpose_set(purpose)
    parts, scheme, host, port = _split(url, purpose)
    literal = classify_host(host)

    if literal != "name":
        kind = literal
        if kind not in allowed:
            raise NetworkError(f"{host} is a {kind.replace('_', '-')} address; "
                               f"{purpose} requests may not reach it")
        return Target(parts.geturl(), scheme, host, port, host, kind)

    addresses = _resolve(host, port)
    chosen, chosen_kind = None, None
    for address in addresses:
        kind = _address_kind(ipaddress.ip_address(address))
        if kind not in allowed:
            raise NetworkError(f"{host} points at a {kind.replace('_', '-')} address; "
                               f"{purpose} requests may not reach it")
        if chosen is None:
            chosen, chosen_kind = address, kind
    return Target(parts.geturl(), scheme, host, port, chosen, chosen_kind)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials the validated address instead of resolving again."""

    pinned_address = None

    def connect(self):
        self.sock = socket.create_connection(
            (self.pinned_address, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection to the validated address, still verifying the certificate for the name.

    SNI and hostname verification both use the original host, so pinning the socket does not
    weaken certificate checks.
    """

    pinned_address = None

    def connect(self):
        sock = socket.create_connection(
            (self.pinned_address, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _handler_classes(target):
    http_conn = type("PinnedHTTP", (_PinnedHTTPConnection,),
                     {"pinned_address": target.address})
    https_conn = type("PinnedHTTPS", (_PinnedHTTPSConnection,),
                      {"pinned_address": target.address})
    return http_conn, https_conn


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, connection_class):
        urllib.request.HTTPHandler.__init__(self)
        self._connection_class = connection_class

    def http_open(self, req):
        return self.do_open(self._connection_class, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, connection_class):
        urllib.request.HTTPSHandler.__init__(self)
        self._connection_class = connection_class

    def https_open(self, req):
        # Passing the context keeps certificate verification on; do_open hands it to the
        # connection class as a keyword argument.
        return self.do_open(self._connection_class, req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects inside urllib.

    `open_url` follows them itself so every hop is validated. If urllib followed one here, the
    destination would never be checked — and a redirect could reach `file:` or an internal
    address that the policy exists to refuse.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _proxies_for(scheme):
    """Proxy wall for `scheme`, if the environment configures one.

    A proxy is an explicit operator decision, and pinning cannot pass through it, so a proxied
    request relies on address validation alone. That limitation is documented rather than
    papered over.
    """
    proxies = urllib.request.getproxies()
    return proxies.get(scheme) or proxies.get("all")


_lock = threading.Lock()


def _build_opener(target, use_proxy):
    """An opener with an explicit handler list.

    `build_opener` would quietly add `HTTPRedirectHandler`, `FTPHandler`, `FileHandler` and
    `DataHandler`. The redirect handler would bypass per-hop validation; the file and data
    handlers would turn a hostile redirect into a local-file read. Both are unacceptable, so
    the handlers are assembled here instead of inherited.
    """
    http_conn, https_conn = _handler_classes(target)
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.ProxyHandler()
                       if use_proxy else urllib.request.ProxyHandler({}))
    if use_proxy:
        # Through a proxy the connection belongs to the proxy, so pinning is not possible.
        opener.add_handler(urllib.request.HTTPHandler())
        opener.add_handler(urllib.request.HTTPSHandler())
    else:
        opener.add_handler(_PinnedHTTPHandler(http_conn))
        opener.add_handler(_PinnedHTTPSHandler(https_conn))
    opener.add_handler(_NoRedirect())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(urllib.request.UnknownHandler())
    return opener


def open_url(url, purpose, timeout=15, headers=None, method=None, data=None,
             max_redirects=MAX_REDIRECTS):
    """Open `url` under `purpose`'s policy, returning the live response object.

    Redirects are followed here, one validated hop at a time. A non-2xx final response raises
    `urllib.error.HTTPError`, exactly as `urlopen` would, so existing callers keep working.
    """
    current = url
    visited = []
    for _hop in range(max_redirects + 1):
        target = validate_url(current, purpose)
        use_proxy = bool(_proxies_for(target.scheme))
        opener = _build_opener(target, use_proxy)
        request = urllib.request.Request(target.url, data=data, headers=dict(headers or {}),
                                         method=method)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code not in REDIRECT_CODES or not location:
                raise
            visited.append(target.url)
            # A relative Location is resolved against the hop that produced it, and the next
            # loop iteration validates the result under the same purpose.
            current = urljoin(target.url, location)
            if current in visited:
                raise NetworkError("redirect loop") from None
            continue
        return response
    raise NetworkError(f"more than {max_redirects} redirects")


def read_capped(response, limit):
    """Read at most `limit` bytes, failing before the payload is fully drained.

    Content-Length is checked first so an oversized body is refused without reading it, and
    the read itself is bounded so a lying or chunked response cannot grow without limit.
    """
    headers = getattr(response, "headers", None)
    declared = headers.get("Content-Length") if headers else None
    if declared:
        try:
            if int(declared) > limit:
                raise NetworkError(f"response declares {declared} bytes; limit is {limit}")
        except ValueError:
            pass
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise NetworkError(f"response exceeds the {limit // 1024} KiB limit")
    return raw
