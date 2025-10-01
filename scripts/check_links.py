#!/usr/bin/env python3
"""Simple link checker for static HTML files.

Reads an HTML file, extracts all absolute HTTP(S) links, and reports
whether they respond with a successful status code. Designed for
offline validation; run it locally where network access is available.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional, Set
import urllib.error
import urllib.request

USER_AGENT = "LinkChecker/0.1 (+https://github.com/wanghero/hao.github.io)"


@dataclass
class LinkResult:
    url: str
    status: Optional[int]
    ok: bool
    error: Optional[str]
    final_url: Optional[str]


class AnchorExtractor(HTMLParser):
    """Collects anchor tag href attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.links: Set[str] = set()

    def handle_starttag(self, tag: str, attrs: Iterable[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        for attr, value in attrs:
            if attr.lower() == "href" and value:
                self.links.add(value.strip())


def fetch(url: str, method: str, timeout: float) -> tuple[int, str]:
    """Perform an HTTP request and return (status_code, final_url)."""
    request = urllib.request.Request(url=url, method=method, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.geturl()


def check_link(url: str, timeout: float) -> LinkResult:
    """Check a single URL, preferring HEAD and falling back to GET."""
    try:
        status, final_url = fetch(url, "HEAD", timeout)
        return LinkResult(url, status, 200 <= status < 400, None, final_url)
    except urllib.error.HTTPError as err:
        # Some servers reject HEAD; retry with GET for those cases.
        if err.code in {400, 401, 403, 405, 500}:
            try:
                status, final_url = fetch(url, "GET", timeout)
                return LinkResult(url, status, 200 <= status < 400, None, final_url)
            except urllib.error.HTTPError as err_get:
                return LinkResult(url, err_get.code, False, str(err_get.reason), err_get.geturl() or url)
            except urllib.error.URLError as err_get:
                return LinkResult(url, None, False, str(err_get.reason), None)
        return LinkResult(url, err.code, False, str(err.reason), err.geturl() or url)
    except urllib.error.URLError as err:
        return LinkResult(url, None, False, str(err.reason), None)


def extract_http_links(html_path: Path) -> list[str]:
    extractor = AnchorExtractor()
    extractor.feed(html_path.read_text(encoding="utf-8"))
    return sorted(link for link in extractor.links if link.startswith(("http://", "https://")))


def run_checker(urls: list[str], timeout: float, workers: int) -> list[LinkResult]:
    results: list[LinkResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(check_link, url, timeout): url for url in urls}
        for future in concurrent.futures.as_completed(future_map):
            results.append(future.result())
    # Preserve original ordering for readability.
    order = {url: index for index, url in enumerate(urls)}
    results.sort(key=lambda item: order.get(item.url, 0))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Check for broken HTTP(S) links in an HTML file.")
    parser.add_argument("html", nargs="?", default="index.html", help="HTML file to scan (default: index.html)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Request timeout in seconds (default: 8)")
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Maximum number of concurrent requests (default: 8)",
    )
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"❌ File not found: {html_path}")

    urls = extract_http_links(html_path)
    if not urls:
        print("No http(s) links found.")
        return

    print(f"Checking {len(urls)} links from {html_path} with timeout={args.timeout}s ...")
    results = run_checker(urls, timeout=args.timeout, workers=args.workers)

    failures = 0
    for result in results:
        status_display = str(result.status) if result.status is not None else "ERR"
        final_hint = f" -> {result.final_url}" if result.final_url and result.final_url != result.url else ""
        outcome = "OK" if result.ok else "FAIL"
        message = f"[{outcome:4}] {status_display:>4} {result.url}{final_hint}"
        if result.error and not result.ok:
            message += f" ({result.error})"
        print(message)
        if not result.ok:
            failures += 1

    if failures:
        print(f"\n⚠️  Completed with {failures} failing link(s).")
    else:
        print("\n✅ All links responded with success codes.")


if __name__ == "__main__":
    main()
