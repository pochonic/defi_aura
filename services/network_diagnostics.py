"""Conservative diagnostics for simultaneous provider failures."""

import socket
import ssl
import sys
import urllib.request
from datetime import datetime, timezone


HOSTS = ("api-v3.raydium.io", "api.orca.so", "dlmm.datapi.meteora.ag")


def _error(error):
    cause = getattr(error, "reason", None)
    source = cause if isinstance(cause, BaseException) else error
    return {
        "class": error.__class__.__name__,
        "errno": getattr(source, "errno", getattr(error, "errno", None)),
        "winerror": getattr(source, "winerror", getattr(error, "winerror", None)),
        "message": str(error),
    }


def diagnose(hosts=HOSTS, timeout=5):
    result = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python_executable": sys.executable, "hostname": socket.gethostname(), "hosts": {}}
    for host in hosts:
        item = {"dns": None, "https": None}
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            item["dns"] = {"ok": True, "addresses": sorted({address[4][0] for address in addresses})}
        except Exception as error:
            item["dns"] = {"ok": False, "error": _error(error)}
        try:
            request = urllib.request.Request(f"https://{host}/", method="HEAD", headers={"User-Agent": "crypto-radar-diagnostic/1.0"})
            with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                item["https"] = {"ok": True, "status": response.status}
        except Exception as error:
            item["https"] = {"ok": False, "error": _error(error)}
        result["hosts"][host] = item
    return result
