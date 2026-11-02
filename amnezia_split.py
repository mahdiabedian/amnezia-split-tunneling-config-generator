#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
amnezia_split.py - Generate an AmneziaVPN split-tunneling site list (ip-list.json)
from a plain-text IPv4 CIDR file, or fetch an up-to-date country list automatically.

The produced JSON file can be imported in the AmneziaVPN app under:
    Settings -> Split Tunneling -> [...] (three dots) -> Import

Input format: one IPv4 CIDR (or single IP) per line, '#' comments allowed.
Output format (official Amnezia format):
    [
      { "hostname": "1.2.3.0/24", "ip": "" },
      ...
    ]

Examples:
    # automatic: download the current Iran list and build the config
    python3 amnezia_split.py

    # from a local file
    python3 amnezia_split.py ~/Documents/ipv4.txt

    python3 amnezia_split.py ~/Documents/ipv4.txt -o iran.json --mode except
    python3 amnezia_split.py --legacy      # buggy 5.0.0.x clients
    python3 amnezia_split.py --collapse    # merge adjacent networks
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_NO_NETWORKS = 2
EXIT_INPUT_ERROR = 3
EXIT_FETCH_ERROR = 4

__version__ = "1.0.0"

# (English label as shown in the AmneziaUI, Persian explanation)
MODE_LABELS = {
    "except": (
        "Addresses from the list should not be accessed via VPN",
        "ترافیک آدرس‌های این لیست بدون VPN برود (بقیهٔ ترافیک از طریق VPN)",
    ),
    "forward": (
        "Only the sites listed here will be accessed through the VPN",
        "فقط ترافیک آدرس‌های این لیست از طریق VPN برود (بقیهٔ ترافیک مستقیم)",
    ),
}

MAX_RECOMMENDED_ENTRIES = 5000

# A downloaded list with fewer networks than this is treated as broken.
MIN_FETCHED_NETWORKS = 10

DEFAULT_MAX_AGE = 6 * 60 * 60  # 6 hours, matches the upstream refresh interval
DEFAULT_TIMEOUT = 30
USER_AGENT = "amnezia-split-tunneling-config-generator/1.0"


def default_sources(country: str) -> list[tuple[str, str]]:
    """Return (name, url) candidates for a country, best first."""
    cc = country.strip().lower()
    sources: list[tuple[str, str]] = []
    if cc == "ir":
        # Updated every 6 hours from RIPE Stat; matches the reference list exactly.
        sources.append(
            (
                "farshidmousavii/iran-ip-ranges",
                "https://raw.githubusercontent.com/farshidmousavii/"
                "iran-ip-ranges/main/dist/raw/ipv4.txt",
            )
        )
    # Official RIPE Stat API: authoritative registry data, no third party.
    sources.append(
        (
            "RIPE Stat (official)",
            f"https://stat.ripe.net/data/country-resource-list/data.json"
            f"?resource={cc.upper()}&v4_format=prefix",
        )
    )
    sources.append(
        (
            "ipverse/rir-ip",
            f"https://raw.githubusercontent.com/ipverse/rir-ip/master/"
            f"country/{cc}/ipv4-aggregated.txt",
        )
    )
    sources.append(
        (
            "ipdeny.com",
            f"https://www.ipdeny.com/ipblocks/data/countries/{cc}.zone",
        )
    )
    return sources


def default_output_name() -> Path:
    """Timestamped default output, e.g. ip-list-20261102-143005.json."""
    return Path(f"ip-list-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="amnezia_split",
        description=(
            "Generate an AmneziaVPN split-tunneling site list (ip-list.json) "
            "from a local CIDR file, or download an up-to-date country list."
        ),
        epilog=(
            "examples:\n"
            "  amnezia_split.py                       # auto-download (Iran)\n"
            "  amnezia_split.py ~/Documents/ipv4.txt  # use a local file\n"
            "  amnezia_split.py -o iran.json --mode except\n"
            "  amnezia_split.py --legacy\n"
            "  amnezia_split.py --collapse\n"
            "  amnezia_split.py --allowedips allowed.txt\n"
            "  amnezia_split.py --country de -o de.json\n"
            "  amnezia_split.py --source https://example.com/list.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "input",
        type=Path,
        nargs="?",
        help=(
            "input file: one IPv4 CIDR per line, '#' comments allowed. "
            "Omit it to download the list automatically."
        ),
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "output JSON path (default: ip-list-<YYYYMMDD-HHMMSS>.json "
            "in the current directory)"
        ),
    )
    p.add_argument(
        "--mode",
        choices=tuple(MODE_LABELS),
        default="except",
        help=(
            "route mode to select in the Amnezia app: 'except' = listed IPs "
            "bypass the VPN (default), 'forward' = only listed IPs use the VPN"
        ),
    )
    p.add_argument(
        "--legacy",
        action="store_true",
        help=(
            "emit the workaround format for buggy Amnezia 5.0.0.x clients "
            "(CIDR kept in the 'ip' field instead of 'hostname')"
        ),
    )
    p.add_argument(
        "--collapse",
        action="store_true",
        help="merge adjacent/overlapping networks into the fewest CIDRs",
    )
    p.add_argument(
        "--allowedips",
        type=Path,
        metavar="PATH",
        help=(
            "also write the networks as a single comma-separated line, ready to "
            "paste into 'AllowedIPs' of a WireGuard/AmneziaWG .conf file"
        ),
    )

    fetch = p.add_argument_group("automatic download (used when INPUT is omitted)")
    fetch.add_argument(
        "--country",
        default="IR",
        metavar="CC",
        help="ISO country code to download (default: IR)",
    )
    fetch.add_argument(
        "--source",
        action="append",
        metavar="URL",
        help=(
            "custom source URL; may be repeated. Overrides the built-in "
            "sources for --country"
        ),
    )
    fetch.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE,
        metavar="SECONDS",
        help=(
            "reuse the cached download if it is younger than this "
            f"(default: {DEFAULT_MAX_AGE}, i.e. 6 hours)"
        ),
    )
    fetch.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cache and download again",
    )
    fetch.add_argument(
        "--offline",
        action="store_true",
        help="never download; use the cache only",
    )
    fetch.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
        / "amnezia-split",
        help="cache directory (default: ~/.cache/amnezia-split)",
    )
    fetch.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"network timeout (default: {DEFAULT_TIMEOUT})",
    )
    fetch.add_argument(
        "--min-networks",
        type=int,
        default=MIN_FETCHED_NETWORKS,
        metavar="N",
        help=(
            "treat a download with fewer than N networks as broken "
            f"(default: {MIN_FETCHED_NETWORKS})"
        ),
    )

    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only print the output path, no instructions",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help=(
            "compare the list against the official RIPE Stat registry data and "
            "print coverage statistics"
        ),
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return p.parse_args(argv)


def parse_cidr_file(text: str) -> tuple[list[ipaddress.IPv4Network], list[tuple[int, str]], int]:
    """Parse the input text.

    Returns (networks, invalid_lines, duplicate_count).
    """
    networks: list[ipaddress.IPv4Network] = []
    invalid: list[tuple[int, str]] = []
    seen: set[ipaddress.IPv4Network] = set()
    duplicates = 0

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            invalid.append((lineno, raw.strip()))
            continue
        if net.version != 4:
            # AmneziaVPN supports IPv4 only.
            invalid.append((lineno, raw.strip() + "   (IPv6: not supported by Amnezia)"))
            continue
        if net in seen:
            duplicates += 1
            continue
        seen.add(net)
        networks.append(net)

    return networks, invalid, duplicates


def build_entries(
    networks: list[ipaddress.IPv4Network], legacy: bool
) -> list[dict[str, str]]:
    """Build the Amnezia import JSON structure.

    Official format (Amnezia >= 5.0.1.5): CIDR lives in 'hostname'.
    Legacy format (buggy 5.0.0.x): CIDR lives in 'ip', 'hostname' is a unique
    non-resolving placeholder so normalizeHostname() cannot strip the prefix.
    """
    entries: list[dict[str, str]] = []
    for i, net in enumerate(networks, 1):
        if legacy:
            entries.append({"hostname": f"st-{i:05d}.invalid", "ip": str(net)})
        else:
            entries.append({"hostname": str(net), "ip": ""})
    return entries


# --------------------------------------------------------------------------- #
# Automatic download
# --------------------------------------------------------------------------- #


def cache_file(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.txt"


def read_cache(path: Path, max_age: int) -> str | None:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if max_age >= 0 and age > max_age:
        return None
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return None


def download(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize_download(text: str) -> str:
    """Convert a RIPE Stat JSON response into a plain CIDR list.

    Any other content is returned unchanged, so plain-text sources keep working.
    """
    if not text.lstrip().startswith("{"):
        return text
    try:
        payload = json.loads(text)
        prefixes = payload["data"]["resources"]["ipv4"]
    except (ValueError, KeyError, TypeError):
        return text
    if not isinstance(prefixes, list):
        return text
    return "\n".join(str(prefix) for prefix in prefixes) + "\n"


def ripe_url(country: str) -> str:
    return (
        "https://stat.ripe.net/data/country-resource-list/data.json"
        f"?resource={country.strip().upper()}&v4_format=prefix"
    )


def fetch_ripe_networks(country: str, timeout: float) -> list[ipaddress.IPv4Network]:
    """Fetch the official RIPE Stat registry list for a country."""
    text = normalize_download(download(ripe_url(country), timeout))
    networks, _, _ = parse_cidr_file(text)
    return networks


def coverage_gap(
    wanted: list[ipaddress.IPv4Network], covered_by: list[ipaddress.IPv4Network]
) -> list[ipaddress.IPv4Network]:
    """Return the parts of `wanted` that are not covered by `covered_by`."""
    remaining = list(wanted)
    for cover in covered_by:
        next_remaining: list[ipaddress.IPv4Network] = []
        for net in remaining:
            if cover.subnet_of(net):
                next_remaining.extend(net.address_exclude(cover))
            elif net.subnet_of(cover):
                continue
            else:
                next_remaining.append(net)
        remaining = next_remaining
    return remaining


def count_addresses(networks: list[ipaddress.IPv4Network]) -> int:
    return sum(net.num_addresses for net in networks)


def print_verification(
    networks: list[ipaddress.IPv4Network], country: str, timeout: float
) -> None:
    try:
        official = fetch_ripe_networks(country, timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"verify     : failed to reach RIPE Stat: {exc}", file=sys.stderr)
        return

    if not official:
        print("verify     : RIPE Stat returned no data", file=sys.stderr)
        return

    official = list(ipaddress.collapse_addresses(sorted(official)))
    current = list(ipaddress.collapse_addresses(sorted(networks)))

    missing = coverage_gap(official, current)
    extra = coverage_gap(current, official)
    official_total = count_addresses(official)
    missing_total = count_addresses(missing)
    extra_total = count_addresses(extra)

    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.2f}%" if whole else "0.00%"

    print(f"verify     : RIPE Stat registry ({country.upper()})")
    print(f"  registered addresses : {official_total:,}")
    print(f"  list addresses       : {count_addresses(current):,}")
    print(
        f"  registered missing   : {missing_total:,} "
        f"({pct(missing_total, official_total)})"
    )
    print(
        f"  extra (announced)    : {extra_total:,} "
        f"({pct(extra_total, official_total)})"
    )
    if missing:
        print(f"  missing sample       : {', '.join(str(n) for n in missing[:5])}")
    if extra:
        print(f"  extra sample         : {', '.join(str(n) for n in extra[:5])}")
    print(
        "  note: 'extra' ranges are announced by Iranian ASNs but registered "
        "to another country; they are expected."
    )


def load_source(
    sources: list[tuple[str, str]],
    cache_dir: Path,
    max_age: int,
    refresh: bool,
    offline: bool,
    timeout: float,
    min_networks: int = MIN_FETCHED_NETWORKS,
) -> tuple[str, str, str, bool]:
    """Return (text, source_name, url, from_cache).

    Tries each source in order, using a fresh cache entry when available.
    Raises RuntimeError when nothing usable could be obtained.
    """
    errors: list[str] = []

    for name, url in sources:
        path = cache_file(cache_dir, url)

        if not refresh:
            cached = read_cache(path, max_age)
            if cached is not None:
                return cached, name, url, True

        if offline:
            errors.append(f"{name}: no usable cache entry")
            continue

        try:
            text = download(url, timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
            continue

        text = normalize_download(text)
        networks, _, _ = parse_cidr_file(text)
        if len(networks) < min_networks:
            errors.append(
                f"{name}: downloaded data looks broken "
                f"({len(networks)} networks, expected >= {min_networks})"
            )
            continue

        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass  # caching is best-effort

        return text, name, url, False

    raise RuntimeError("; ".join(errors) or "no sources configured")


def write_meta(
    output: Path,
    source: str,
    url: str,
    from_cache: bool,
    networks: int,
    text: str,
) -> Path:
    meta = {
        "source": source,
        "url": url,
        "from_cache": from_cache,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "networks": networks,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    path = output.with_name(output.name + ".meta.json")
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def print_instructions(output: Path, mode: str, legacy: bool, quiet: bool) -> None:
    if quiet:
        print(output)
        return

    en_label, fa_label = MODE_LABELS[mode]
    print()
    print(f"فایل خروجی: {output}")
    print()
    print("مراحل import در AmneziaVPN:")
    print("  1. ابتدا حالت split tunneling را انتخاب کنید:")
    print(f"       «{en_label}»")
    print(f"       ({fa_label})")
    print("  2. روی دکمهٔ ⋮ (سه نقطه) بزنید و Import را انتخاب کنید.")
    print(f"  3. فایل {output.name} را انتخاب کنید و «Replace site list» را بزنید.")
    print("  4. کلید Split Tunneling را روشن کنید و به VPN وصل شوید.")
    print()
    if legacy:
        print(
            "نکته: خروجی با گزینهٔ --legacy ساخته شده و برای نسخه‌های باگ‌دار "
            "5.0.0.x است؛ در نسخهٔ 5.0.1.5 یا جدیدتر گزینهٔ --legacy را حذف کنید."
        )
    print("نکته:  تغییر لیست فقط وقتی اتصال قطع است ممکن است.")
    print("نکته:  در اتصال Free امکان split tunneling با IP وجود ندارد.")
    print(
        "عیب‌یابی: اگر پیام «Default server does not support split tunneling function» "
        "دیدید،"
    )
    print(
        "         AllowedIPs سرور باید برابر `0.0.0.0/0, ::/0` باشد "
        "(با فاصله بعد از کاما)."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output or default_output_name()

    fetched = None
    if args.input is None:
        if args.offline and args.refresh:
            print("error: --offline and --refresh cannot be used together", file=sys.stderr)
            return EXIT_INPUT_ERROR

        if args.source:
            sources = [(f"custom:{i}", url) for i, url in enumerate(args.source, 1)]
        else:
            sources = default_sources(args.country)

        try:
            text, source_name, url, from_cache = load_source(
                sources,
                cache_dir=args.cache_dir,
                max_age=args.max_age,
                refresh=args.refresh,
                offline=args.offline,
                timeout=args.timeout,
                min_networks=args.min_networks,
            )
        except RuntimeError as exc:
            print(f"error: could not obtain the IP list: {exc}", file=sys.stderr)
            return EXIT_FETCH_ERROR

        fetched = (source_name, url, from_cache)
        if not args.quiet:
            state = "cache" if from_cache else "download"
            print(f"source     : {source_name} ({state})")
            print(f"url        : {url}")
    else:
        if not args.input.is_file():
            print(f"error: input file not found: {args.input}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        try:
            text = args.input.read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
            return EXIT_INPUT_ERROR

    networks, invalid, duplicates = parse_cidr_file(text)

    if invalid:
        print(f"warning: skipped {len(invalid)} invalid line(s):", file=sys.stderr)
        for lineno, line in invalid[:20]:
            print(f"  line {lineno}: {line}", file=sys.stderr)
        if len(invalid) > 20:
            print(f"  ... and {len(invalid) - 20} more", file=sys.stderr)

    if not networks:
        print("error: no valid IPv4 networks found in input", file=sys.stderr)
        return EXIT_NO_NETWORKS

    if args.collapse:
        before = len(networks)
        networks = list(ipaddress.collapse_addresses(sorted(networks)))
        collapsed = before - len(networks)
    else:
        collapsed = 0

    if any(net.prefixlen == 0 for net in networks):
        print(
            "warning: input contains 0.0.0.0/0 - in 'except' mode this would "
            "send ALL traffic outside the VPN",
            file=sys.stderr,
        )

    if args.verify:
        if args.offline:
            print("verify     : skipped (--offline)", file=sys.stderr)
        else:
            print_verification(networks, args.country, args.timeout)

    if len(networks) > MAX_RECOMMENDED_ENTRIES:
        print(
            f"warning: {len(networks)} entries may slow down the Amnezia app; "
            "consider --collapse",
            file=sys.stderr,
        )

    entries = build_entries(networks, legacy=args.legacy)

    try:
        output.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.allowedips:
            args.allowedips.write_text(
                ", ".join(str(net) for net in networks) + "\n",
                encoding="utf-8",
            )
        meta_path = None
        if fetched is not None:
            meta_path = write_meta(
                output,
                source=fetched[0],
                url=fetched[1],
                from_cache=fetched[2],
                networks=len(networks),
                text=text,
            )
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not args.quiet:
        if args.input is not None:
            print(f"input      : {args.input}")
        print(f"networks   : {len(networks)} unique IPv4", end="")
        if duplicates:
            print(f" ({duplicates} duplicate line(s) removed)", end="")
        if collapsed:
            print(f", {collapsed} merged by --collapse", end="")
        print()
        print(f"format     : {'legacy (5.0.0.x)' if args.legacy else 'official (5.0.1.5+)'}")
        print(f"route mode : {args.mode}")
        if args.allowedips:
            print(f"allowedips : {args.allowedips}")
        if meta_path:
            print(f"metadata   : {meta_path}")

    print_instructions(output, args.mode, args.legacy, args.quiet)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
