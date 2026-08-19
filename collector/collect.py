#!/usr/bin/env python3
"""Collect heterogeneous inputs into SPECTRA source_record v0.2.

Collectors intentionally stop at the Bronze layer. They never infer importance,
create evidence claims, or overwrite source text with an LLM summary.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import arxiv
import feedparser


SCHEMA_VERSION = "0.2"
ROOT = Path(__file__).resolve().parents[1]
TRACKING_KEYS = {"fbclid", "gclid", "ref", "source", "spm", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}


def load_local_env(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE entries from ignored .env.local without overwriting process env."""
    env_path = path or ROOT / ".env.local"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Context:
    collected_at: datetime
    window_start: datetime
    window_end: datetime
    newscrawler_command: Optional[str]


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    if host in {"arxiv.org", "www.arxiv.org"}:
        scheme = "https"
        host = "arxiv.org"
        path = re.sub(r"v\d+$", "", path)
    # The Batch publishes multiple independently titled stories inside one
    # issue page. Preserve its heading fragment so each story remains a unique,
    # directly navigable source_record; discard fragments elsewhere as before.
    fragment = parts.fragment if host in {"deeplearning.ai", "www.deeplearning.ai"} and path.startswith("/the-batch/issue-") else ""
    return urlunsplit((scheme, host, path, urlencode(query), fragment))


def clean_html(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def stable_id(url: str) -> str:
    return "src_" + hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:20]


def content_hash(title: str, text: Optional[str], excerpt: Optional[str]) -> str:
    material = "\n".join([title, text or "", excerpt or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def language_hint(text: str, configured: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh-CN"
    return configured or "und"


def make_record(*, source: dict[str, Any], url: str, title: str, ctx: Context,
                published_at: Optional[datetime] = None, authors: Optional[list[str]] = None,
                raw_text: Optional[str] = None, raw_excerpt: Optional[str] = None,
                access_status: str = "success", http_status: Optional[int] = None,
                failure_reason: Optional[str] = None, rights_scope: str = "excerpt",
                discovery_context: Optional[str] = None) -> dict[str, Any]:
    canonical = canonicalize_url(url)
    safe_title = title.strip() or "Untitled source"
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "source_record",
        "source_id": stable_id(canonical),
        "registry_id": source.get("registry_id"),
        "source_type": source["source_type"],
        "source_name": source["source_name"],
        "publisher": source.get("publisher"),
        "author": authors or [],
        "source_url": url,
        "canonical_url": canonical,
        "raw_title": safe_title,
        "raw_text": raw_text,
        "raw_excerpt": raw_excerpt,
        "published_at": iso(published_at),
        "collected_at": iso(ctx.collected_at),
        "language": language_hint(" ".join([safe_title, raw_excerpt or ""]), source.get("language", "und")),
        "content_hash": content_hash(safe_title, raw_text, raw_excerpt),
        "access_status": access_status,
        "http_status": http_status,
        "failure_reason": failure_reason,
        "discovered_by": "manual" if source["adapter"] == "newscrawler" else "registry",
        "discovery_context": discovery_context or source["adapter"],
        "rights_scope": rights_scope,
        "processing_status": "collected" if access_status == "success" else "needs_review"
    }


def parse_feed_datetime(entry: Any) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if value:
            try:
                return parsedate_to_datetime(value)
            except (TypeError, ValueError):
                pass
    return None


def in_window(value: Optional[datetime], ctx: Context) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return ctx.window_start <= value.astimezone(timezone.utc) <= ctx.window_end


def keyword_match(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def fetch_bytes(url: str, attempts: int = 3, timeout: int = 45,
                headers: Optional[dict[str, str]] = None) -> bytes:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            request_headers = {"User-Agent": "SPECTRA-Collector/0.2 (+research prototype)"}
            request_headers.update(headers or {})
            request = Request(url, headers=request_headers)
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def parse_publication_date(value: str) -> Optional[datetime]:
    """Parse the date formats used by official investor-relations indexes."""
    cleaned = html.unescape(clean_html(value) or "").replace("\u00a0", " ").strip()
    formats = (
        "%Y.%m.%d", "%Y/%m/%d", "%Y-%m-%d",
        "%B %d, %Y", "%b %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def configured_focus_context(source: dict[str, Any]) -> str:
    terms = [str(item).strip() for item in source.get("focus_terms", []) if str(item).strip()]
    return f"watchlist_focus:{','.join(terms)}" if terms else ""


def extract_pdf_pages(payload: bytes) -> list[str]:
    """Extract page-addressable text from a primary-source PDF."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required for financial PDF extraction; install requirements-collector.txt"
        ) from exc
    if not payload.startswith(b"%PDF"):
        raise ValueError("downloaded document is not a PDF")
    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("encrypted PDF cannot be read") from exc
    return [re.sub(r"\s+", " ", page.extract_text() or "").strip() for page in reader.pages]


def focused_pdf_excerpt(pages: list[str], focus_terms: list[str], *, max_pages: int = 5,
                        max_chars: int = 6000) -> str:
    """Keep auditable page windows around configured business signals.

    Focus terms decide which source-authored passages are shown first; they are
    not copied into the excerpt and never become evidence by themselves.
    """
    terms = [term.strip() for term in focus_terms if term.strip()]
    ranked = []
    for page_number, text in enumerate(pages, 1):
        lowered = text.lower()
        matches = []
        for term in terms:
            start = lowered.find(term.lower())
            if start >= 0:
                weight = max(1, len(term.split()))
                matches.append((weight, start, term.lower()))
        if matches:
            strongest = max(matches, key=lambda item: (item[0], -item[1]))
            weighted_score = sum(item[0] for item in matches)
            ranked.append((weighted_score, len(matches), page_number, text, strongest[1]))
    if not ranked:
        ranked = [(0, 0, page_number, text, 0) for page_number, text in enumerate(pages[:2], 1) if text]
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    excerpts = []
    for _, _, page_number, text, first_hit in ranked[:max_pages]:
        start = max(0, first_hit - 240)
        end = min(len(text), start + 1500)
        excerpts.append(f"[Page {page_number}] {text[start:end]}")
    return "\n\n".join(excerpts)[:max_chars]


def enrich_pdf_records(records: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    """Attach full text and focused, page-located excerpts to PDF records."""
    attempted = extracted = 0
    errors = []
    max_bytes = int(source.get("pdf_max_bytes", 25_000_000))
    for record in records:
        if record.get("access_status") != "success":
            continue
        if not urlsplit(record.get("canonical_url") or "").path.lower().endswith(".pdf"):
            continue
        attempted += 1
        try:
            payload = fetch_bytes(
                record["canonical_url"],
                attempts=int(source.get("attempts", 2)),
                timeout=int(source.get("pdf_timeout_seconds", 45)),
                headers={"Accept": "application/pdf"},
            )
            if len(payload) > max_bytes:
                raise ValueError(f"PDF exceeds configured limit of {max_bytes} bytes")
            pages = extract_pdf_pages(payload)
            full_text = "\n\n".join(
                f"[Page {page_number}] {text}"
                for page_number, text in enumerate(pages, 1) if text
            )
            if len(full_text) < 200:
                raise ValueError("PDF text extraction returned insufficient text")
            record["raw_text"] = full_text
            record["raw_excerpt"] = focused_pdf_excerpt(pages, source.get("focus_terms", []))
            record["content_hash"] = content_hash(
                record["raw_title"], record["raw_text"], record["raw_excerpt"]
            )
            record["rights_scope"] = "primary_source_internal_text"
            record["processing_status"] = "text_extracted"
            record["discovery_context"] = (
                f"{record.get('discovery_context') or source['adapter']}; "
                f"pdf_text:pypdf; pdf_pages:{len(pages)}"
            )
            extracted += 1
        except Exception as exc:
            errors.append({
                "source_id": record.get("source_id"),
                "url": record.get("canonical_url"),
                "error": f"{type(exc).__name__}: {exc}",
            })
            record["processing_status"] = "pdf_text_needs_review"
    return {"attempted": attempted, "extracted": extracted, "failed": len(errors), "errors": errors}


def _nearby_date(page: str, start: int, end: int) -> Optional[datetime]:
    """Find the nearest visible date around a listing anchor."""
    date_patterns = (
        r"\b20\d{2}[./-]\d{1,2}[./-]\d{1,2}\b",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b",
        r"\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}\b",
    )
    for fragment in (page[end:end + 700], page[max(0, start - 700):start]):
        visible = clean_html(fragment) or ""
        candidates: list[tuple[int, str]] = []
        for pattern in date_patterns:
            candidates.extend((match.start(), match.group(0)) for match in re.finditer(pattern, visible, flags=re.I))
        for _, value in sorted(candidates):
            parsed = parse_publication_date(value)
            if parsed:
                return parsed
    return None


def collect_official_ir_index(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect dated earnings/report links from an official company IR page.

    Focus terms are kept in discovery_context as research scope. They are never
    blended into the source-authored excerpt or presented as evidence.
    """
    try:
        candidate_urls = []
        for candidate in [source["url"]] + source.get("fallback_urls", []):
            if isinstance(candidate, str):
                normalized = candidate.strip()
                if normalized and normalized not in candidate_urls:
                    candidate_urls.append(normalized)

        fetch_errors: list[str] = []
        page: Optional[str] = None
        resolved_url = source["url"]
        attempts = int(source.get("attempts", 2))
        timeout = int(source.get("timeout_seconds", 25))

        for candidate_url in candidate_urls:
            try:
                page = fetch_bytes(candidate_url, attempts=attempts, timeout=timeout).decode("utf-8", errors="replace")
                resolved_url = candidate_url
                break
            except Exception as exc:
                fetch_errors.append(f"{candidate_url}: {type(exc).__name__}: {exc}")

        if page is None:
            reason = "; ".join(fetch_errors) if fetch_errors else "no valid official IR url"
            return [make_record(
                source=source,
                url=source["url"],
                title=f"Collection failed: {source['source_name']}",
                ctx=ctx,
                access_status="failed",
                failure_reason=reason,
                rights_scope="metadata_only",
            )], {
                "status": "failed",
                "error": reason,
                "attempted_urls": candidate_urls,
                "fetch_errors": fetch_errors,
            }

        entries: list[tuple[str, str, datetime]] = []
        layout = source.get("layout", "dated_links")
        if layout == "alibaba_ssr":
            pattern = re.compile(
                r'"documentId":"(?P<id>\d+)".*?'
                r'"documentPublishTime":(?P<timestamp>\d+).*?'
                r'"documentTitle":"(?P<title>(?:\\.|[^"\\])*)"',
                flags=re.S,
            )
            for match in pattern.finditer(page):
                title = json.loads(f'"{match.group("title")}"')
                published = unix_datetime(match.group("timestamp"))
                if published:
                    entries.append((title, urljoin(resolved_url, f"/en-US/document-{match.group('id')}"), published))
        else:
            anchor_pattern = re.compile(
                r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>',
                flags=re.I | re.S,
            )
            for match in anchor_pattern.finditer(page):
                title = clean_html(match.group("label")) or ""
                if len(title) < 12:
                    continue
                published = _nearby_date(page, match.start(), match.end())
                if published:
                    entries.append((title, urljoin(resolved_url, html.unescape(match.group("href"))), published))

        include_terms = [term.lower() for term in source.get("title_include_terms", [])]
        exclude_terms = [term.lower() for term in source.get("title_exclude_terms", [])]
        records = []
        seen_urls = set()
        scanned = 0
        for title, url, published in entries:
            lowered = title.lower()
            if include_terms and not any(term in lowered for term in include_terms):
                continue
            if any(term in lowered for term in exclude_terms):
                continue
            scanned += 1
            canonical = canonicalize_url(url)
            # IR indexes usually expose only a calendar date. Compare dates,
            # otherwise an item on the first day of a seven-day window would
            # be incorrectly dropped because its unknown time defaulted to midnight.
            if canonical in seen_urls or not (ctx.window_start.date() <= published.date() <= ctx.window_end.date()):
                continue
            seen_urls.add(canonical)
            report_source = {**source, "publisher": source.get("publisher") or source["source_name"]}
            context_parts = [
                f"official_ir_index:{source['url']}",
                f"resolved_url:{resolved_url}",
                configured_focus_context(source),
            ]
            records.append(make_record(
                source=report_source,
                url=url,
                title=title,
                ctx=ctx,
                published_at=published,
                raw_excerpt=f"Official investor-relations disclosure dated {published.date().isoformat()}.",
                rights_scope="metadata_only",
                discovery_context="; ".join(part for part in context_parts if part),
            ))
        return records, {
            "status": "success",
            "scanned": scanned,
            "accepted": len(records),
            "layout": layout,
            "focus_terms": source.get("focus_terms", []),
            "attempted_urls": candidate_urls,
            "resolved_url": resolved_url,
            "fallback_used": resolved_url != source["url"],
            "fetch_errors": [err for err in fetch_errors if err],
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(
            source=source, url=source["url"], title=f"Collection failed: {source['source_name']}", ctx=ctx,
            access_status="failed", failure_reason=reason, rights_scope="metadata_only",
        )], {"status": "failed", "error": reason}


def collect_hkex_title_search(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract issuer announcements from an HKEX title-search result table."""
    try:
        page = fetch_bytes(
            source["url"],
            attempts=int(source.get("attempts", 2)),
            timeout=int(source.get("timeout_seconds", 20)),
        ).decode("utf-8", errors="replace")
        row_pattern = re.compile(r"<tr\b[^>]*>(?P<row>.*?)</tr>", flags=re.I | re.S)
        date_pattern = re.compile(r"\b(?P<date>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})\b")
        category_pattern = re.compile(
            r'<div\b[^>]*class=["\'][^"\']*headline[^"\']*["\'][^>]*>(?P<value>.*?)</div>',
            flags=re.I | re.S,
        )
        document_pattern = re.compile(
            r'<div\b[^>]*class=["\'][^"\']*doc-link[^"\']*["\'][^>]*>.*?'
            r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
            flags=re.I | re.S,
        )
        include_terms = [term.lower() for term in source.get("title_include_terms", [])]
        exclude_terms = [term.lower() for term in source.get("title_exclude_terms", [])]
        expected_codes = [str(code).strip() for code in source.get("stock_codes", []) if str(code).strip()]
        hong_kong_tz = timezone(timedelta(hours=8))
        records = []
        parsed_rows = 0
        eligible_rows = 0
        for row_match in row_pattern.finditer(page):
            row = row_match.group("row")
            date_match = date_pattern.search(clean_html(row) or "")
            category_match = category_pattern.search(row)
            document_match = document_pattern.search(row)
            if not date_match or not document_match:
                continue
            visible_row = clean_html(row) or ""
            if expected_codes and not any(code in visible_row for code in expected_codes):
                continue
            parsed_rows += 1
            category = html.unescape(clean_html(category_match.group("value")) or "") if category_match else ""
            title = html.unescape(clean_html(document_match.group("title")) or "Untitled HKEX announcement")
            searchable = f"{category or ''} {title}".lower()
            if include_terms and not any(term in searchable for term in include_terms):
                continue
            if any(term in searchable for term in exclude_terms):
                continue
            eligible_rows += 1
            published = datetime.strptime(date_match.group("date"), "%d/%m/%Y %H:%M").replace(tzinfo=hong_kong_tz)
            if not in_window(published, ctx):
                continue
            document_url = urljoin(source["url"], html.unescape(document_match.group("href")))
            context_parts = [
                f"hkex_title_search:{source['url']}",
                f"stock_codes:{','.join(expected_codes)}" if expected_codes else "",
                configured_focus_context(source),
            ]
            records.append(make_record(
                source=source,
                url=document_url,
                title=title,
                ctx=ctx,
                published_at=published,
                raw_excerpt=(
                    f"HKEX issuer announcement released at {date_match.group('date')} HKT. "
                    f"Category: {category or 'Unspecified'}."
                ),
                rights_scope="metadata_only",
                discovery_context="; ".join(part for part in context_parts if part),
            ))
        if parsed_rows == 0:
            raise ValueError("HKEX page was reachable but no issuer announcement rows were parsed")
        return records, {
            "status": "success",
            "scanned": parsed_rows,
            "eligible": eligible_rows,
            "accepted": len(records),
            "stock_codes": expected_codes,
            "focus_terms": source.get("focus_terms", []),
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(
            source=source,
            url=source["url"],
            title=f"Collection failed: {source['source_name']}",
            ctx=ctx,
            access_status="failed",
            failure_reason=reason,
            rights_scope="metadata_only",
        )], {"status": "failed", "error": reason}


def collect_sec_submissions(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect recent company filings from the official SEC submissions API."""
    try:
        cik = str(source["cik"]).zfill(10)
        url = source.get("url") or f"https://data.sec.gov/submissions/CIK{cik}.json"
        user_agent = os.getenv(
            "SEC_USER_AGENT",
            "SPECTRA-Collector/0.2 contact=wenzhengouyang@users.noreply.github.com",
        )
        payload = json.loads(fetch_bytes(
            url,
            attempts=int(source.get("attempts", 2)),
            timeout=int(source.get("timeout_seconds", 20)),
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        ).decode("utf-8"))
        recent = payload.get("filings", {}).get("recent", {})
        allowed_forms = set(source.get("forms", ["10-Q", "10-K", "8-K"])); records = []
        scanned = 0
        columns = (
            recent.get("accessionNumber", []), recent.get("filingDate", []),
            recent.get("acceptanceDateTime", []), recent.get("form", []),
            recent.get("primaryDocument", []), recent.get("primaryDocDescription", []),
        )
        for accession, filing_date, accepted_at, form, primary_document, description in zip(*columns):
            if form not in allowed_forms:
                continue
            scanned += 1
            published = parse_iso_datetime(accepted_at) or parse_iso_datetime(filing_date)
            if not in_window(published, ctx):
                continue
            accession_compact = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_compact}/{primary_document}"
            )
            title = (description or "").strip() or f"{form} filing"
            if form not in title:
                title = f"{form}: {title}"
            report_source = {**source, "publisher": source.get("publisher") or payload.get("name")}
            records.append(make_record(
                source=report_source,
                url=filing_url,
                title=title,
                ctx=ctx,
                published_at=published,
                raw_excerpt=f"Official SEC {form} filing submitted on {filing_date}.",
                rights_scope="metadata_only",
                discovery_context=(
                    f"sec_submissions:{url}; form:{form}; "
                    f"{configured_focus_context(source)}"
                ).rstrip("; "),
            ))
        return records, {
            "status": "success",
            "scanned": scanned,
            "accepted": len(records),
            "forms": sorted(allowed_forms),
            "cik": cik,
            "focus_terms": source.get("focus_terms", []),
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(
            source=source,
            url=source.get("url") or f"https://data.sec.gov/submissions/CIK{str(source.get('cik', '')).zfill(10)}.json",
            title=f"Collection failed: {source['source_name']}",
            ctx=ctx,
            access_status="failed",
            failure_reason=reason,
            rights_scope="metadata_only",
        )], {"status": "failed", "error": reason}


def collect_sitemap(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover recently changed official report pages from an XML sitemap.

    Sitemap dates are modification timestamps, not asserted publication dates;
    that distinction is retained in discovery_context for human verification.
    """
    try:
        raw = fetch_bytes(
            source["url"],
            attempts=int(source.get("attempts", 2)),
            timeout=int(source.get("timeout_seconds", 20)),
        )
        root = ET.fromstring(raw)
        include_terms = [term.lower() for term in source.get("url_include_terms", [])]
        exclude_terms = [term.lower() for term in source.get("url_exclude_terms", [])]
        records = []
        scanned = 0
        for node in root.findall("{*}url"):
            location = (node.findtext("{*}loc") or "").strip()
            modified_text = (node.findtext("{*}lastmod") or "").strip()
            if not location or not modified_text:
                continue
            lowered = location.lower()
            if include_terms and not any(term in lowered for term in include_terms):
                continue
            if any(term in lowered for term in exclude_terms):
                continue
            scanned += 1
            modified = parse_iso_datetime(modified_text)
            if not in_window(modified, ctx):
                continue
            slug = urlsplit(location).path.rstrip("/").split("/")[-1]
            title = re.sub(r"[-_]+", " ", slug).strip().title() or source["source_name"]
            records.append(make_record(
                source=source,
                url=location,
                title=title,
                ctx=ctx,
                published_at=modified,
                raw_excerpt=f"Official sitemap reports this page changed at {iso(modified)}.",
                rights_scope="metadata_only",
                discovery_context=(
                    f"sitemap:{source['url']}; date_semantics:last_modified; "
                    f"{configured_focus_context(source)}"
                ).rstrip("; "),
            ))
        return records, {
            "status": "success",
            "scanned": scanned,
            "accepted": len(records),
            "date_semantics": "last_modified",
            "focus_terms": source.get("focus_terms", []),
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(
            source=source,
            url=source["url"],
            title=f"Collection failed: {source['source_name']}",
            ctx=ctx,
            access_status="failed",
            failure_reason=reason,
            rights_scope="metadata_only",
        )], {"status": "failed", "error": reason}


def werss_request_json(base_url: str, path: str, *, token: Optional[str] = None,
                       form: Optional[dict[str, str]] = None,
                       json_body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Call the local WeRSS API without storing credentials in the registry."""
    if form is not None and json_body is not None:
        raise ValueError("form and json_body are mutually exclusive")
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(form).encode("utf-8")
    elif json_body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"WeRSS API error: {payload.get('message') or payload.get('detail') or payload}")
    return payload.get("data") or {}


def unix_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def normalize_werss_article(item: dict[str, Any], source: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """Map WeRSS article metadata to the shared Bronze-layer source_record."""
    publisher = str(item.get("mp_name") or "未知公众号").strip()
    article_source = {**source, "source_name": publisher, "publisher": publisher}
    return make_record(
        source=article_source,
        url=str(item.get("url") or "").strip(),
        title=str(item.get("title") or "").strip(),
        ctx=ctx,
        published_at=unix_datetime(item.get("publish_time")),
        raw_excerpt=clean_html(item.get("description")),
        rights_scope="excerpt",
        discovery_context=f"werss:{item.get('mp_id') or publisher}",
    )


def collect_werss(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect authorized WeChat watchlist articles from a local WeRSS service."""
    password = os.getenv(source.get("password_env", "WERSS_PASSWORD"))
    if not password:
        return [], {"status": "failed", "error": "WERSS_PASSWORD is not set"}
    try:
        base_url = source.get("base_url") or source["url"]
        login = werss_request_json(
            base_url,
            "/api/v1/wx/auth/login",
            form={"username": os.getenv("WERSS_USERNAME", source.get("username", "admin")), "password": password},
        )
        token = login["access_token"]
        wx_auth = werss_request_json(base_url, "/api/v1/wx/auth/qr/status", token=token)
        if not wx_auth.get("login_status"):
            return [], {
                "status": "failed",
                "error": "WeRSS WeChat authorization is expired; scan a new QR code",
                "reauth_required": True,
            }
        watchlist = json.loads(Path(source["watchlist"]).read_text(encoding="utf-8"))
        allowed_names = {
            item["account_name"] for item in watchlist["accounts"] if item.get("enabled", True)
        }
        feeds = werss_request_json(base_url, "/api/v1/wx/mps?offset=0&limit=100&kw=", token=token)
        allowed_feeds = {
            item["mp_name"]: item for item in feeds.get("list", []) if item.get("mp_name") in allowed_names
        }
        allowed_feed_ids = {item["id"] for item in allowed_feeds.values()}

        refresh_results: list[dict[str, Any]] = []
        if source.get("refresh_before_read", False) and allowed_feeds:
            priority_rank = {"P0": 0, "P1": 1, "P2": 2}
            enabled_accounts = [item for item in watchlist["accounts"] if item.get("enabled", True)]
            enabled_accounts.sort(key=lambda item: (priority_rank.get(item.get("priority", "P2"), 9), item["account_name"]))
            available_accounts = [item for item in enabled_accounts if item["account_name"] in allowed_feeds]
            refresh_limit = max(0, min(int(source.get("refresh_limit", 1)), len(available_accounts)))
            if refresh_limit:
                # Monday/Thursday runs cover complementary halves of the
                # watchlist. The weekly anchor rotates so no publisher stays
                # permanently at the edge of the selection.
                run_date = ctx.window_end.date()
                week_anchor = run_date.toordinal() - run_date.weekday()
                half_week_offset = refresh_limit if run_date.weekday() >= 3 else 0
                start = (week_anchor + half_week_offset) % len(available_accounts)
                selected = (available_accounts + available_accounts)[start:start + refresh_limit]
                for account in selected:
                    feed = allowed_feeds[account["account_name"]]
                    refresh_channel = source.get("refresh_channel", "wechat")
                    try:
                        if refresh_channel == "weread":
                            refreshed = werss_request_json(
                                base_url,
                                "/api/v1/wx/weread/collect",
                                token=token,
                                json_body={
                                    "mp_id": feed["id"],
                                    "mp_name": feed["mp_name"],
                                    "faker_id": feed["id"],
                                    "max_page": 1,
                                    "gather_content": True,
                                },
                            )
                            accepted = int(refreshed.get("collected") or 0)
                        else:
                            refreshed = werss_request_json(
                                base_url,
                                f"/api/v1/wx/mps/update/{feed['id']}?start_page=0&end_page=1",
                                token=token,
                            )
                            accepted = int(refreshed.get("total") or 0)
                        refresh_results.append({
                            "account_name": account["account_name"],
                            "status": "success" if accepted else "no_articles",
                            "channel": refresh_channel,
                            "accepted": accepted,
                        })
                    except Exception as exc:
                        refresh_result = {
                            "account_name": account["account_name"],
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        if refresh_channel != "weread" and source.get("weread_fallback", False) and "frequency control" in str(exc).lower():
                            try:
                                fallback = werss_request_json(
                                    base_url,
                                    "/api/v1/wx/weread/collect",
                                    token=token,
                                    json_body={
                                        "mp_id": feed["id"],
                                        "mp_name": feed["mp_name"],
                                        "faker_id": feed["id"],
                                        "max_page": 1,
                                        "gather_content": True,
                                    },
                                )
                                refresh_result.update({
                                    "status": "fallback_success",
                                    "fallback": "weread",
                                    "accepted": int(fallback.get("collected") or 0),
                                })
                            except Exception as fallback_exc:
                                refresh_result.update({
                                    "fallback": "weread",
                                    "fallback_error": f"{type(fallback_exc).__name__}: {fallback_exc}",
                                })
                        refresh_results.append(refresh_result)

        records: list[dict[str, Any]] = []
        scanned = 0
        missing_url = 0
        page_size = min(int(source.get("page_size", 100)), 100)
        max_scan = int(source.get("max_scan", 500))
        offset = 0
        total = None
        while scanned < max_scan and (total is None or offset < total):
            page = werss_request_json(
                base_url,
                f"/api/v1/wx/articles?offset={offset}&limit={page_size}",
                token=token,
            )
            items = page.get("list") or []
            total = int(page.get("total") or 0)
            if not items:
                break
            for item in items:
                scanned += 1
                if item.get("mp_id") not in allowed_feed_ids:
                    continue
                published_at = unix_datetime(item.get("publish_time"))
                if not in_window(published_at, ctx):
                    continue
                if not str(item.get("url") or "").strip():
                    missing_url += 1
                    continue
                records.append(normalize_werss_article(item, source, ctx))
                if scanned >= max_scan:
                    break
            offset += len(items)

        feed_names = {item.get("mp_name") for item in feeds.get("list", [])}
        sync_pending = bool(allowed_feed_ids) and not (total or 0)
        health = {
            "status": "failed" if sync_pending else "success",
            "configured_accounts": len(allowed_names),
            "matched_accounts": len(allowed_feed_ids),
            "missing_accounts": sorted(allowed_names - feed_names),
            "available_articles": total or 0,
            "scanned": scanned,
            "accepted": len(records),
            "missing_url": missing_url,
            "reauth_required": False,
            "sync_pending": sync_pending,
            "refresh_attempts": len(refresh_results),
            "refresh_results": refresh_results,
        }
        if sync_pending:
            health["error"] = "WeRSS is authorized and subscribed, but its article store is empty; initial sync is required"
        return records, health
    except Exception as exc:
        return [], {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def collect_feed(source: dict[str, Any], ctx: Context, keywords: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        parsed = feedparser.parse(fetch_bytes(source["url"]))
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(source=source, url=source["url"], title=f"Collection failed: {source['source_name']}", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    if parsed.get("bozo") and not parsed.entries:
        reason = str(parsed.get("bozo_exception", "feed parse failed"))
        return [make_record(source=source, url=source["url"], title=f"Collection failed: {source['source_name']}", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    records = []
    scanned = 0
    for entry in parsed.entries:
        scanned += 1
        published = parse_feed_datetime(entry)
        if not in_window(published, ctx):
            continue
        title = clean_html(entry.get("title")) or "Untitled feed entry"
        excerpt = clean_html(entry.get("summary") or entry.get("description"))
        searchable = " ".join([title, excerpt or ""])
        if keywords and not keyword_match(searchable, keywords):
            continue
        authors = [a.get("name", "").strip() for a in entry.get("authors", []) if a.get("name")]
        records.append(make_record(source=source, url=entry.get("link", source["url"]), title=title, ctx=ctx,
                                   published_at=published, authors=authors, raw_excerpt=excerpt,
                                   discovery_context=f"{source['adapter']}:{source['url']}"))
    return records, {"status": "success", "scanned": scanned, "accepted": len(records)}


class TheBatchPageParser(HTMLParser):
    """Extract stable metadata and links without depending on visual markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self.stories: list[dict[str, Any]] = []
        self._news_started = False
        self._heading_id: Optional[str] = None
        self._heading_parts: list[str] = []
        self._in_heading = False
        self._paragraph_parts: list[str] = []
        self._in_paragraph = False
        self._current_story: Optional[dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = {key: value for key, value in attrs if value is not None}
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if key and content and key not in self.metadata:
                self.metadata[key] = content
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._heading_id = attributes.get("id")
            self._heading_parts = []
            self._in_heading = True
        elif tag == "p" and self._current_story is not None:
            self._paragraph_parts = []
            self._in_paragraph = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._in_heading:
            heading = " ".join("".join(self._heading_parts).split())
            if self._heading_id == "news":
                self._news_started = True
                self._current_story = None
            elif self._news_started and self._heading_id and heading:
                self._current_story = {"id": self._heading_id, "title": heading, "paragraphs": []}
                self.stories.append(self._current_story)
            self._in_heading = False
            self._heading_parts = []
        elif tag == "p" and self._in_paragraph:
            paragraph = " ".join("".join(self._paragraph_parts).split())
            if paragraph and self._current_story is not None and len(self._current_story["paragraphs"]) < 3:
                self._current_story["paragraphs"].append(paragraph)
            self._in_paragraph = False
            self._paragraph_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_heading:
            self._heading_parts.append(data)
        if self._in_paragraph:
            self._paragraph_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(part.strip() for part in self.title_parts if part.strip())


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def collect_batch_index(source: dict[str, Any], ctx: Context, keywords: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover recent issues, then emit one source_record per individual story."""
    try:
        page = fetch_bytes(source["url"]).decode("utf-8", errors="replace")
        # The current Next.js payload exposes cards as escaped JSON. Matching only
        # these four scalar fields keeps this adapter independent of visual markup.
        pattern = re.compile(
            r'\\"title\\":\\"(?P<title>.*?)\\".*?'
            r'\\"excerpt\\":\\"(?P<excerpt>.*?)\\".*?'
            r'\\"href\\":\\"(?P<href>/the-batch/issue-[^\\"]+)\\".*?'
            r'\\"date\\":\\"(?P<date>[^\\"]+)\\"',
            re.S,
        )
        cards = []
        seen = set()
        for match in pattern.finditer(page):
            href = match.group("href")
            if href in seen:
                continue
            seen.add(href)
            cards.append(match.groupdict())
        if not cards:
            # Topic pages such as /tag/business expose story cards through a
            # schema.org ItemList instead of issue cards. Preserve the same
            # source_record contract while reading article-level metadata.
            business_items = []
            for raw_schema in re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                page,
                flags=re.S | re.I,
            ):
                try:
                    schema = json.loads(html.unescape(raw_schema))
                except (json.JSONDecodeError, TypeError):
                    continue
                main_entity = schema.get("mainEntity") if isinstance(schema, dict) else None
                if isinstance(main_entity, dict) and main_entity.get("@type") == "ItemList":
                    business_items.extend(main_entity.get("itemListElement") or [])
            if not business_items:
                raise ValueError("The Batch card payload was not found")
            records = []
            scanned = 0
            for item in business_items[:int(source.get("max_scan", 20))]:
                url = item.get("url") if isinstance(item, dict) else None
                if not url:
                    continue
                scanned += 1
                article_page = fetch_bytes(url).decode("utf-8", errors="replace")
                parser = TheBatchPageParser()
                parser.feed(article_page)
                published = parse_iso_datetime(parser.metadata.get("article:published_time"))
                if not in_window(published, ctx):
                    continue
                title = parser.metadata.get("og:title") or item.get("name") or parser.title
                excerpt = parser.metadata.get("og:description") or parser.metadata.get("description")
                if keywords and not keyword_match(f"{title} {excerpt or ''}", keywords):
                    continue
                records.append(make_record(
                    source=source,
                    url=url,
                    title=html.unescape(title),
                    ctx=ctx,
                    published_at=published,
                    authors=[parser.metadata.get("article:author") or "DeepLearning.AI"],
                    raw_excerpt=html.unescape(excerpt) if excerpt else None,
                    discovery_context=f"batch_business:{source['url']}",
                ))
            return records, {
                "status": "success",
                "scanned": scanned,
                "issues_in_window": 0,
                "story_sections": scanned,
                "accepted": len(records),
                "listing_mode": "business_item_list",
            }
        records = []
        issues_in_window = 0
        story_sections = 0
        for card in cards:
            issue_date = datetime.strptime(card["date"], "%B %d, %Y").date()
            # Compare calendar dates first. The listing page omits time and
            # timezone, which previously dropped a Friday issue at the edge of
            # a seven-day window before its exact timestamp could be read.
            if not (ctx.window_start.date() <= issue_date <= ctx.window_end.date()):
                continue
            issues_in_window += 1
            issue_url = urljoin("https://www.deeplearning.ai", card["href"])
            issue_page = fetch_bytes(issue_url).decode("utf-8", errors="replace")
            issue_parser = TheBatchPageParser()
            issue_parser.feed(issue_page)
            issue_published = parse_iso_datetime(issue_parser.metadata.get("article:published_time"))
            fallback_published = datetime.combine(issue_date, datetime.min.time(), tzinfo=timezone.utc)
            published = issue_published or fallback_published

            stories = issue_parser.stories
            story_sections += len(stories)

            # If the issue page changes shape, preserve a useful issue-level
            # record rather than silently reporting a healthy source with zero.
            if not stories:
                title = html.unescape(card["title"].replace(r'\"', '"'))
                excerpt = html.unescape(card["excerpt"].replace(r'\u0026', '&').replace(r'\"', '"'))
                if not keywords or keyword_match(f"{title} {excerpt}", keywords):
                    records.append(make_record(
                        source=source, url=issue_url, title=title, ctx=ctx, published_at=published,
                        authors=["DeepLearning.AI"], raw_excerpt=excerpt,
                        discovery_context=f"batch_issue_fallback:{issue_url}",
                    ))
                continue

            for story in stories:
                title = story["title"]
                excerpt = " ".join(story["paragraphs"][:2]) or None
                searchable = f"{title} {excerpt or ''}"
                if keywords and not keyword_match(searchable, keywords):
                    continue
                story_url = f"{issue_url}#{story['id']}"
                author = issue_parser.metadata.get("author") or "DeepLearning.AI"
                records.append(make_record(
                    source=source, url=story_url, title=html.unescape(title), ctx=ctx,
                    published_at=published, authors=[author], raw_excerpt=html.unescape(excerpt) if excerpt else None,
                    discovery_context=f"batch_issue:{issue_url}",
                ))
        return records, {
            "status": "success", "scanned": len(cards), "issues_in_window": issues_in_window,
            "story_sections": story_sections, "accepted": len(records),
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(
            source=source, url=source["url"], title="Collection failed: The Batch", ctx=ctx,
            access_status="failed", failure_reason=reason, rights_scope="metadata_only",
        )], {"status": "failed", "error": reason}


def arxiv_query(source: dict[str, Any]) -> str:
    categories = " OR ".join(f"cat:{category}" for category in source["categories"])
    terms = " OR ".join(f'all:"{term}"' for term in source["query_terms"])
    return f"({categories}) AND ({terms})"


def collect_arxiv(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = arxiv_query(source)
    records = []
    scanned = 0
    try:
        search = arxiv.Search(query=query, max_results=source.get("max_results", 60), sort_by=arxiv.SortCriterion.SubmittedDate)
        client = arxiv.Client(page_size=50, delay_seconds=3, num_retries=2)
        for result in client.results(search):
            scanned += 1
            published = result.published
            if published < ctx.window_start:
                break
            if not in_window(published, ctx):
                continue
            records.append(make_record(source=source, url=result.entry_id, title=result.title, ctx=ctx,
                                       published_at=published, authors=[a.name for a in result.authors],
                                       raw_excerpt=clean_html(result.summary), rights_scope="excerpt",
                                       discovery_context=f"arxiv:{query}"))
        return records, {"status": "success", "scanned": scanned, "accepted": len(records), "query": query}
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        record = make_record(source=source, url="https://arxiv.org/", title="Collection failed: arXiv visual query", ctx=ctx,
                             access_status="failed", failure_reason=reason, rights_scope="metadata_only",
                             discovery_context=f"arxiv:{query}")
        return [record], {"status": "failed", "error": reason, "query": query}


def normalize_newscrawler_payload(payload: dict[str, Any], source: dict[str, Any], ctx: Context) -> dict[str, Any]:
    article = payload.get("article") if isinstance(payload.get("article"), dict) else payload
    url = article.get("url") or article.get("news_url") or article.get("source_url") or source["url"]
    title = article.get("title") or article.get("raw_title") or "Untitled extracted article"
    body = article.get("content") or article.get("text") or article.get("raw_text") or article.get("texts")
    if not body and isinstance(article.get("contents"), list):
        body = [item.get("content", "") for item in article["contents"] if item.get("type") == "text"]
    if isinstance(body, list):
        body = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in body)
    meta = article.get("meta_info") if isinstance(article.get("meta_info"), dict) else {}
    author = article.get("authors") or article.get("author") or meta.get("author_name") or []
    if isinstance(author, str):
        author = [author]
    published = None
    published_raw = article.get("publish_time") or article.get("published_at") or article.get("date") or meta.get("publish_time")
    if published_raw:
        try:
            published = datetime.fromisoformat(str(published_raw).replace("Z", "+00:00"))
        except ValueError:
            try:
                published = parsedate_to_datetime(str(published_raw))
            except (TypeError, ValueError):
                pass
    return make_record(source=source, url=url, title=title, ctx=ctx, published_at=published,
                       authors=[str(item) for item in author], raw_text=body, raw_excerpt=clean_html(article.get("summary")),
                       rights_scope="full_text" if body else "metadata_only",
                       discovery_context="newscrawler:known_url")


def collect_newscrawler(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ctx.newscrawler_command:
        reason = "NewsCrawler command is not configured; adapter contract is ready but extractor is unavailable"
        return [make_record(source=source, url=source["url"], title="Collection unavailable: NewsCrawler", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    try:
        command = ctx.newscrawler_command.format(url=source["url"])
        completed = subprocess.run(command, shell=True, check=True, capture_output=True, text=True, timeout=90)
        payload = json.loads(completed.stdout)
        return [normalize_newscrawler_payload(payload, source, ctx)], {"status": "success", "scanned": 1, "accepted": 1}
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(source=source, url=source["url"], title="Collection failed: NewsCrawler", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}


def collect_news_extractor(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    command = source.get("command")
    if not command:
        reason = "news-extractor command is not configured"
        return [make_record(source=source, url=source["url"], title="Collection unavailable: news-extractor", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    try:
        with tempfile.TemporaryDirectory(prefix="spectra-news-") as output_dir:
            args = [part.format(url=source["url"], output=output_dir) for part in command]
            # Keep uv's cache outside the installed Skill directory. This makes
            # the isolated Skill callable from restricted/scheduled runtimes
            # without modifying the Skill or relying on a writable home cache.
            command_env = os.environ.copy()
            command_env.setdefault(
                "UV_CACHE_DIR",
                str(Path(tempfile.gettempdir()) / "spectra-news-extractor-uv-cache"),
            )
            completed = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
                env=command_env,
            )
            outputs = list(Path(output_dir).glob("*.json"))
            if len(outputs) != 1:
                raise ValueError(f"expected one JSON output, got {len(outputs)}")
            payload = json.loads(outputs[0].read_text(encoding="utf-8"))
            record = normalize_newscrawler_payload(payload, source, ctx)
            if not in_window(datetime.fromisoformat(record["published_at"].replace("Z", "+00:00")) if record["published_at"] else None, ctx):
                return [], {"status": "success", "scanned": 1, "accepted": 0, "note": "article outside collection window"}
            return [record], {"status": "success", "scanned": 1, "accepted": 1, "stdout": completed.stdout[-1000:]}
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(source=source, url=source["url"], title="Collection failed: news-extractor", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}


def rewrite_discovered_url(url: str, source: dict[str, Any]) -> str:
    rewritten = url
    for rule in source.get("link_replacements", []):
        rewritten = rewritten.replace(rule["from"], rule["to"])
    return rewritten


def collect_news_extractor_feed(source: dict[str, Any], ctx: Context, keywords: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover recent links from a feed, then extract selected articles with NewsCrawler."""
    try:
        parsed = feedparser.parse(fetch_bytes(source["discovery_url"]))
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(source=source, url=source["discovery_url"], title=f"Collection failed: {source['source_name']}", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    if parsed.get("bozo") and not parsed.entries:
        reason = str(parsed.get("bozo_exception", "discovery feed parse failed"))
        return [make_record(source=source, url=source["discovery_url"], title=f"Collection failed: {source['source_name']}", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}
    selected = []
    scanned = 0
    for entry in parsed.entries:
        scanned += 1
        published = parse_feed_datetime(entry)
        if not in_window(published, ctx):
            continue
        article_url = rewrite_discovered_url(entry.get("link", source["url"]), source)
        include_pattern = source.get("url_include_regex")
        if include_pattern and not re.search(include_pattern, article_url):
            continue
        title = clean_html(entry.get("title")) or "Untitled feed entry"
        excerpt = clean_html(entry.get("summary") or entry.get("description"))
        if keywords and not keyword_match(f"{title} {excerpt or ''}", keywords):
            continue
        selected.append((entry, article_url))
        if len(selected) >= source.get("max_extract", 3):
            break
    records = []
    errors = []
    for entry, article_url in selected:
        article_source = {**source, "url": article_url}
        batch, health = collect_news_extractor(article_source, ctx)
        records.extend(batch)
        if health["status"] == "failed":
            errors.append(health.get("error", "unknown extraction error"))
    successful = sum(item["access_status"] == "success" for item in records)
    if selected and not successful:
        return records, {"status": "failed", "scanned": scanned, "selected": len(selected), "accepted": 0, "errors": errors}
    return records, {
        "status": "success", "scanned": scanned, "selected": len(selected), "accepted": successful,
        "extraction_failures": len(errors),
    }


def collect_news_extractor_inbox(source: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract supported article URLs discovered by search, referral, or another connector."""
    inbox_path = Path(source["input_file"])
    try:
        payload = json.loads(inbox_path.read_text(encoding="utf-8"))
        entries = payload.get("records", [])
        if not isinstance(entries, list):
            raise ValueError("inbox records must be a list")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return [make_record(source=source, url=source["url"], title="Collection failed: news-extractor inbox", ctx=ctx,
                            access_status="failed", failure_reason=reason, rights_scope="metadata_only")], {"status": "failed", "error": reason}

    records = []
    errors = []
    attempted = 0
    for item in entries:
        if not item.get("enabled", True) or not item.get("url"):
            continue
        discovered_at = parse_iso_datetime(item.get("discovered_at"))
        if discovered_at and not in_window(discovered_at, ctx):
            continue
        attempted += 1
        article_source = {
            **source,
            "url": item["url"],
            "source_name": item.get("source_name") or source["source_name"],
            "publisher": item.get("publisher") or source.get("publisher"),
        }
        batch, health = collect_news_extractor(article_source, ctx)
        for record in batch:
            record["discovered_by"] = item.get("discovered_by", "search")
            record["discovery_context"] = item.get("discovery_context") or f"news_extractor_inbox:{inbox_path}"
        records.extend(batch)
        if health["status"] == "failed":
            errors.append(health.get("error", "unknown extraction error"))
        if attempted >= source.get("max_extract", 10):
            break
    successful = sum(item["access_status"] == "success" for item in records)
    if attempted and not successful:
        return records, {"status": "failed", "scanned": len(entries), "attempted": attempted, "accepted": 0, "errors": errors}
    return records, {
        "status": "success", "scanned": len(entries), "attempted": attempted,
        "accepted": successful, "extraction_failures": len(errors),
    }


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    rank = {"success": 3, "partial": 2, "blocked": 1, "failed": 0}
    for record in records:
        key = record["canonical_url"]
        if key not in chosen or rank[record["access_status"]] > rank[chosen[key]["access_status"]]:
            chosen[key] = record
    return sorted(chosen.values(), key=lambda r: (r["published_at"] or "", r["source_name"]), reverse=True)


def run(config: dict[str, Any], ctx: Context) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    checks = []
    groups = config.get("keyword_groups", {})
    for source in config["sources"]:
        if not source.get("enabled", True):
            continue
        adapter = source["adapter"]
        keywords = groups.get(source.get("keyword_group"), [])
        if adapter in {"rss", "github_atom"}:
            batch, health = collect_feed(source, ctx, keywords)
        elif adapter == "batch_index":
            batch, health = collect_batch_index(source, ctx, keywords)
        elif adapter == "arxiv":
            batch, health = collect_arxiv(source, ctx)
        elif adapter == "newscrawler":
            batch, health = collect_newscrawler(source, ctx)
        elif adapter == "news_extractor":
            batch, health = collect_news_extractor(source, ctx)
        elif adapter == "news_extractor_feed":
            batch, health = collect_news_extractor_feed(source, ctx, keywords)
        elif adapter == "news_extractor_inbox":
            batch, health = collect_news_extractor_inbox(source, ctx)
        elif adapter == "werss_api":
            batch, health = collect_werss(source, ctx)
        elif adapter == "official_ir_index":
            batch, health = collect_official_ir_index(source, ctx)
        elif adapter == "hkex_title_search":
            batch, health = collect_hkex_title_search(source, ctx)
        elif adapter == "sec_submissions":
            batch, health = collect_sec_submissions(source, ctx)
        elif adapter == "sitemap":
            batch, health = collect_sitemap(source, ctx)
        else:
            batch, health = [], {"status": "failed", "error": f"Unknown adapter: {adapter}"}
        if source.get("extract_pdf_text"):
            health["pdf_extraction"] = enrich_pdf_records(batch, source)
        records.extend(batch)
        checks.append({"registry_id": source["registry_id"], "adapter": adapter, **health})
    unique = dedupe(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "collection_run",
        "run_id": "run_" + ctx.collected_at.strftime("%Y%m%dT%H%M%SZ"),
        "window_start": iso(ctx.window_start),
        "window_end": iso(ctx.window_end),
        "collected_at": iso(ctx.collected_at),
        "source_checks": checks,
        "summary": {
            "configured_sources": len(checks),
            "successful_sources": sum(1 for c in checks if c["status"] == "success"),
            "failed_sources": sum(1 for c in checks if c["status"] == "failed"),
            "source_records": len(unique),
            "successful_records": sum(1 for r in unique if r["access_status"] == "success"),
            "failed_records": sum(1 for r in unique if r["access_status"] == "failed")
        },
        "source_records": unique
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="collector/source_registry.v0.2.json")
    parser.add_argument("--output", default="collector/runs/latest.json")
    parser.add_argument("--end", help="ISO timestamp; default now")
    parser.add_argument("--days", type=int, help="Override config window_days")
    parser.add_argument(
        "--source",
        action="append",
        help="Run only the selected registry_id; repeat to select multiple sources",
    )
    parser.add_argument("--newscrawler-command", help="Command template returning JSON on stdout; use {url} placeholder")
    args = parser.parse_args()
    load_local_env()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.source:
        selected_ids = set(args.source)
        config["sources"] = [item for item in config["sources"] if item["registry_id"] in selected_ids]
        missing = selected_ids - {item["registry_id"] for item in config["sources"]}
        if missing:
            parser.error(f"unknown source registry_id: {', '.join(sorted(missing))}")
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end else datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    days = args.days or config.get("window_days", 7)
    ctx = Context(collected_at=datetime.now(timezone.utc), window_start=end - timedelta(days=days), window_end=end, newscrawler_command=args.newscrawler_command)
    result = run(config, ctx)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
