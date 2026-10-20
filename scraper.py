import os
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

REQUEST_TIMEOUT = 30
COMMUNITY_PATH_RE = re.compile(r"^/communities/[^/]+/?$")
LOCALITY_RE = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")


def _get_site_base() -> str:
    load_dotenv()
    base = os.getenv("SITE_BASE")
    if not base:
        raise RuntimeError("SITE_BASE environment variable is not set.")
    return base.rstrip("/")


def _get_page(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def _community_url(href: str, page_url: str, base: str) -> str | None:
    resolved = urlsplit(urljoin(page_url, href))
    site = urlsplit(base)
    if resolved.netloc != site.netloc or not COMMUNITY_PATH_RE.fullmatch(
        resolved.path
    ):
        return None
    path = resolved.path.rstrip("/")
    return urlunsplit((site.scheme, site.netloc, path, "", ""))


def _community_links(
    soup: BeautifulSoup, page_url: str, base: str
) -> set[str]:
    links: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = _community_url(anchor["href"], page_url, base)
        if url:
            links.add(url)
    return links


def _directory_pages(
    session: requests.Session, base: str
) -> tuple[dict[str, set[str]], set[str]]:
    page_url = f"{base}/communities"
    visited_pages: set[str] = set()
    discovered_from: dict[str, set[str]] = {}

    while page_url not in visited_pages:
        visited_pages.add(page_url)
        soup = _get_page(session, page_url)
        for url in _community_links(soup, page_url, base):
            discovered_from.setdefault(url, set()).add(page_url)

        current_query = parse_qs(urlsplit(page_url).query)
        current_page = int(current_query.get("page", ["1"])[0])
        next_pages: list[tuple[int, str]] = []
        for anchor in soup.select('a[href^="/communities"]'):
            candidate = urlsplit(urljoin(page_url, anchor["href"]))
            if candidate.path.rstrip("/") != "/communities":
                continue
            page_values = parse_qs(candidate.query).get("page", [])
            if not page_values or not page_values[0].isdigit():
                continue
            candidate_page = int(page_values[0])
            if candidate_page > current_page:
                next_pages.append((candidate_page, urlunsplit(candidate)))

        if not next_pages:
            break
        page_url = min(next_pages)[1]

    return discovered_from, visited_pages


def _extract_location(
    session: requests.Session, url: str, discovered_from: set[str]
) -> dict[str, Any]:
    soup = _get_page(session, url)
    heading = soup.find("h1")
    if heading is None:
        raise RuntimeError(f"Community detail page has no name heading: {url}")

    fields: dict[str, Any] = {}
    for label in soup.select("dl.detail dt"):
        value = label.find_next_sibling("dd")
        if value is not None:
            fields[label.get_text(" ", strip=True).casefold()] = value

    address = fields.get("address")
    if address is None:
        raise RuntimeError(f"Community detail page has no address: {url}")
    address_lines = [
        line.strip()
        for line in address.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    if not address_lines:
        raise RuntimeError(f"Community address has an invalid format: {url}")

    if len(address_lines) >= 2:
        street = " ".join(address_lines[:-1])
        locality = address_lines[-1]
        match = LOCALITY_RE.fullmatch(locality)
    else:
        match = re.fullmatch(
            r"(.+?)\s+(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)",
            address_lines[0],
        )
        if match is None:
            raise RuntimeError(f"Community address has an invalid format: {url}")
        street, city, state, postal_code = match.groups()

    if len(address_lines) >= 2:
        if match is None:
            raise RuntimeError(f"Community address has an invalid format: {url}")
        city, state, postal_code = match.groups()

    care_field = fields.get("care offerings")
    if care_field is None:
        raise RuntimeError(f"Community detail page has no care offerings: {url}")
    care_offerings = [
        badge.get_text(" ", strip=True)
        for badge in care_field.select(".badge")
        if badge.get_text(" ", strip=True)
    ]
    if not care_offerings:
        care_offerings = [care_field.get_text(" ", strip=True)]

    return {
        "name": heading.get_text(" ", strip=True),
        "street": street,
        "city": city,
        "state": state,
        "zip": postal_code,
        "care_offerings": care_offerings,
        "url": url,
        "discovered_from": sorted(discovered_from),
    }


def get_website_locations() -> list[dict[str, Any]]:
    base = _get_site_base()
    homepage_url = f"{base}/"
    about_url = f"{base}/about"

    with requests.Session() as session:
        discovered_from, _ = _directory_pages(session, base)

        for source_url in (homepage_url, about_url):
            soup = _get_page(session, source_url)
            for url in _community_links(soup, source_url, base):
                discovered_from.setdefault(url, set()).add(source_url)

        return [
            _extract_location(session, url, discovered_from[url])
            for url in sorted(discovered_from)
        ]
