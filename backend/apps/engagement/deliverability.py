"""
Deliverability pre-flight (Phase 6.4): before a real campaign, check the sending
domain publishes SPF and DMARC, and optionally a DKIM selector. Pure parsing over
a resolver function so it's testable offline; the default resolver uses dnspython
if available, else the check reports that DNS lookups aren't available here.
"""


def _default_resolver(name: str, rdtype: str) -> list[str]:
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        raise RuntimeError("DNS lookups need the dnspython package.")
    return [b"".join(r.strings).decode() if hasattr(r, "strings") else str(r)
            for r in dns.resolver.resolve(name, rdtype)]


def check_domain(domain: str, *, dkim_selector: str | None = None, resolver=_default_resolver) -> dict:
    domain = domain.strip().rstrip(".").lower()
    checks = {}

    def txt(name):
        try:
            return resolver(name, "TXT"), None
        except Exception as exc:  # noqa: BLE001 — report the lookup failure, don't raise
            return [], str(exc)

    spf_records, spf_err = txt(domain)
    spf = [r for r in spf_records if r.lower().startswith("v=spf1")]
    checks["spf"] = {"ok": bool(spf), "record": spf[0] if spf else None, "error": spf_err}

    dmarc_records, dmarc_err = txt(f"_dmarc.{domain}")
    dmarc = [r for r in dmarc_records if r.lower().startswith("v=dmarc1")]
    policy = None
    if dmarc:
        for part in dmarc[0].split(";"):
            if part.strip().lower().startswith("p="):
                policy = part.strip()[2:]
    checks["dmarc"] = {"ok": bool(dmarc), "record": dmarc[0] if dmarc else None, "policy": policy, "error": dmarc_err}

    if dkim_selector:
        dkim_records, dkim_err = txt(f"{dkim_selector}._domainkey.{domain}")
        dkim = [r for r in dkim_records if "v=dkim1" in r.lower() or "k=" in r.lower()]
        checks["dkim"] = {"ok": bool(dkim), "selector": dkim_selector, "record": dkim[0] if dkim else None, "error": dkim_err}

    checks["ready"] = checks["spf"]["ok"] and checks["dmarc"]["ok"]
    return {"domain": domain, "checks": checks}
