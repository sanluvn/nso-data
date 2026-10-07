"""Acquire monthly socioeconomic releases and artifacts from the NSO website.

This production entry point discovers releases, maintains stable release identity,
reconciles downloadable artifacts, preserves immutable artifact revisions, and
validates the resulting registry/manifests. It may access the network and update
``data/registry`` and ``data/raw/nso_web``.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
PIPELINE_ROOT = Path(__file__).resolve().parents[1]
from typing import Optional
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
import json
import os
import time
import hashlib
import uuid

from src.web_layout import WebLayout, LAYOUT_VERSION, period_info, safe_path

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from src.paths import (
    NSO_WEB_MONTHLY_ROOT,
    NSO_WEB_RELEASE_REGISTRY_PATH,
)

# ============================================================
# PROJECT CONFIGURATION
# ============================================================

SERIES_ROOT = NSO_WEB_MONTHLY_ROOT

RELEASE_REGISTRY_PATH = (
    NSO_WEB_RELEASE_REGISTRY_PATH
)

# ============================================================
# SOURCE CONFIGURATION
# ============================================================

SOURCE_NAME = (
    "National Statistics Office of Vietnam"
)

SERIES_CODE = (
    "monthly_socioeconomic"
)

SERIES_NAME = (
    "Báo cáo tình hình kinh tế - xã hội hàng tháng"
)

LISTING_URL = (
    "https://www.nso.gov.vn/"
    "bao-cao-tinh-hinh-kinh-te-xa-hoi-hang-thang/"
)


# ============================================================
# REGISTRY CONFIGURATION
# ============================================================

REGISTRY_SCHEMA_VERSION = "1.0"

REGISTRY_TYPE = (
    "nso_web_release_registry"
)


# ============================================================
# ARTIFACT DISCOVERY CONFIGURATION
# ============================================================

ARTIFACT_EXTENSION_TYPES = {
    ".xls": "excel",
    ".xlsx": "excel",
    ".doc": "word",
    ".docx": "word",
    ".pdf": "pdf",
    ".zip": "archive",
    ".rar": "archive",
    ".7z": "archive",
}

SUPPORTED_ARTIFACT_EXTENSIONS = set(
    ARTIFACT_EXTENSION_TYPES
)


# ============================================================
# HTTP CONFIGURATION
# ============================================================

REQUEST_TIMEOUT = 30
MAX_DOWNLOAD_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 2
HASH_CHUNK_SIZE = 1024 * 1024
MANIFEST_FILENAME = "manifest.json"

MAX_LISTING_PAGES = 100

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


# ============================================================
# DATA MODELS
# ============================================================

@dataclass(frozen=True)
class ReleaseRecord:
    """
    One release discovered from the NSO listing archive.
    """

    series_code: str

    title: str

    publication_date: Optional[str]

    reference_period: Optional[str]

    next_release_date: Optional[str]

    publication_url: str

    listing_page_url: str

    listing_page_number: int


@dataclass(frozen=True)
class ArtifactCandidate:
    """
    One artifact link discovered from a release detail page.

    Artifact bytes have NOT been downloaded yet.
    """

    artifact_url: str

    artifact_type: str

    source_filename: str

    anchor_text: Optional[str]


@dataclass(frozen=True)
class AcquiredArtifact:
    """
    One successfully acquired artifact with persisted provenance.
    """

    artifact_url: str

    artifact_type: str

    source_filename: str

    local_relative_path: str

    sha256: str

    byte_size: int

    content_type: Optional[str]


@dataclass(frozen=True)
class ReleaseArtifactDiscovery:
    """
    Result of inspecting one publication detail page.

    Discovery records links only; artifact reconciliation happens afterward.
    """

    release_id: str

    publication_url: str

    final_page_url: Optional[str]

    http_status: Optional[int]

    artifacts: tuple

    error: Optional[str]


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now_iso():
    """
    Return current UTC time as ISO-8601.
    """

    return (
        datetime.now(
            timezone.utc
        )
        .replace(
            microsecond=0
        )
        .isoformat()
    )


# ============================================================
# HTTP SESSION
# ============================================================

def build_session():
    """
    Create HTTP session used by acquisition pipeline.
    """

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent":
                USER_AGENT,
        }
    )

    return session


# ============================================================
# DIRECTORY INITIALIZATION
# ============================================================

def ensure_pipeline_directories():
    """
    Create directories owned by this acquisition layer.
    """

    directories = (SERIES_ROOT, RELEASE_REGISTRY_PATH.parent,)
    for directory in directories:

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


# ============================================================
# CONFIGURATION VALIDATION
# ============================================================

def validate_configuration():
    """
    Fail early if essential configuration is invalid.
    """

    if not PIPELINE_ROOT.exists():
        raise RuntimeError(
            "PIPELINE_ROOT does not exist: "
            f"{PIPELINE_ROOT}"
        )
    if not LISTING_URL.startswith(
        "https://"
    ):

        raise RuntimeError(
            "LISTING_URL must use HTTPS."
        )

    if not SERIES_CODE:

        raise RuntimeError(
            "SERIES_CODE is empty."
        )

    if not REGISTRY_SCHEMA_VERSION:

        raise RuntimeError(
            "REGISTRY_SCHEMA_VERSION is empty."
        )


# ============================================================
# BASIC TEXT / URL HELPERS
# ============================================================

def clean_text(value):
    """
    Normalize whitespace without changing source semantics.
    """

    if value is None:
        return None

    value = " ".join(
        value.split()
    )

    return value or None


def normalize_url(
    href,
    base_url,
):
    """
    Resolve relative href against actual response URL.

    No semantic URL correction is performed.
    """

    if not href:
        return None

    href = href.strip()

    if not href:
        return None

    return urljoin(
        base_url,
        href,
    )


def strip_prefix(
    text,
    prefix,
):
    """
    Remove known presentation label from metadata.
    """

    text = clean_text(
        text
    )

    if text is None:
        return None

    if text.startswith(
        prefix
    ):

        text = text[
            len(prefix):
        ].strip()

    return text or None


def parse_source_date(text):
    """
    Convert DD/MM/YYYY into YYYY-MM-DD.
    """

    text = clean_text(
        text
    )

    if text is None:
        return None

    parsed = datetime.strptime(
        text,
        "%d/%m/%Y",
    ).date()

    return parsed.isoformat()


# ============================================================
# RELEASE METADATA EXTRACTION
# ============================================================

def extract_metadata_text(
    section,
    css_class,
    prefix,
):
    """
    Extract one metadata field using semantic CSS class.
    """

    element = section.find(
        class_=css_class,
    )

    if element is None:
        return None

    return strip_prefix(
        element.get_text(
            " ",
            strip=True,
        ),
        prefix,
    )


def extract_title(section):
    """
    Extract displayed release title.
    """

    metadata_classes = {
        "archive-issue-date",
        "archive-reference-period",
        "archive-next-release",
    }

    candidates = []

    for element in section.find_all(
        True
    ):

        classes = set(
            element.get(
                "class",
                [],
            )
        )

        if classes & metadata_classes:
            continue

        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        if any(
            marker in text
            for marker in (
                "Ngày đăng:",
                "Kỳ tham chiếu:",
                "Lần công bố sắp tới:",
            )
        ):

            continue

        candidates.append(
            text
        )

    if not candidates:
        return None

    return max(
        candidates,
        key=len,
    )


def parse_section_metadata(section):
    """
    Parse source metadata from one release section.
    """

    publication_date_text = (
        extract_metadata_text(
            section,
            "archive-issue-date",
            "Ngày đăng:",
        )
    )

    reference_period = (
        extract_metadata_text(
            section,
            "archive-reference-period",
            "Kỳ tham chiếu:",
        )
    )

    next_release_date_text = (
        extract_metadata_text(
            section,
            "archive-next-release",
            "Lần công bố sắp tới:",
        )
    )

    return {
        "title":
            extract_title(
                section
            ),

        "publication_date": (
            parse_source_date(
                publication_date_text
            )
            if publication_date_text
            else None
        ),

        "reference_period":
            reference_period,

        "next_release_date": (
            parse_source_date(
                next_release_date_text
            )
            if next_release_date_text
            else None
        ),
    }


# ============================================================
# PUBLICATION LINK EXTRACTION
# ============================================================

def extract_publication_url(
    element,
    base_url,
):
    """
    Extract final href contained in one structural link element.
    """

    anchors = element.find_all(
        "a",
        href=True,
    )

    if not anchors:
        return None

    href = anchors[-1].get(
        "href"
    )

    return normalize_url(
        href,
        base_url,
    )


# ============================================================
# RELEASE SECTION IDENTIFICATION
# ============================================================

def is_release_section(element):
    """
    Identify release metadata section.
    """

    if not isinstance(
        element,
        Tag,
    ):

        return False

    classes = element.get(
        "class",
        [],
    )

    return (
        element.name == "section"
        and "item" in classes
    )


# ============================================================
# RELEASE SEGMENTATION
# ============================================================

def extract_release_records(
    archive,
    base_url,
    listing_page_number,
):
    """
    Convert one archive-container into ReleaseRecord objects.

    Validated grammar:

        publication-link element
                ↓
        section.item
    """

    children = [
        child
        for child in archive.children
        if isinstance(
            child,
            Tag,
        )
    ]

    records = []

    release_section_count = sum(
        is_release_section(
            child
        )
        for child in children
    )

    for index in range(
        len(children) - 1
    ):

        current_child = (
            children[index]
        )

        next_child = (
            children[index + 1]
        )

        if not is_release_section(
            next_child
        ):

            continue

        publication_url = (
            extract_publication_url(
                current_child,
                base_url,
            )
        )

        metadata = (
            parse_section_metadata(
                next_child
            )
        )

        if publication_url is None:

            raise RuntimeError(
                "Listing structural invariant failed: "
                "release section is not immediately "
                "preceded by a publication URL.\n"
                f"Title        : {metadata['title']}\n"
                f"Listing page : {base_url}"
            )

        if metadata[
            "title"
        ] is None:

            raise RuntimeError(
                "Release title missing.\n"
                f"Publication URL : {publication_url}\n"
                f"Listing page    : {base_url}"
            )

        record = ReleaseRecord(
            series_code=SERIES_CODE,

            title=metadata[
                "title"
            ],

            publication_date=metadata[
                "publication_date"
            ],

            reference_period=metadata[
                "reference_period"
            ],

            next_release_date=metadata[
                "next_release_date"
            ],

            publication_url=publication_url,

            listing_page_url=base_url,

            listing_page_number=(
                listing_page_number
            ),
        )

        records.append(
            record
        )

    if len(records) != release_section_count:

        raise RuntimeError(
            "Listing structural completeness failed.\n"
            f"Release sections : {release_section_count}\n"
            f"Parsed records   : {len(records)}\n"
            f"Listing page     : {base_url}"
        )

    return records


# ============================================================
# PAGINATION
# ============================================================

def extract_next_page_url(
    soup,
    base_url,
):
    """
    Follow actual pagination href published by NSO.
    """

    anchor = soup.select_one(
        "a.next.page-numbers[href]"
    )

    if anchor is None:
        return None

    return normalize_url(
        anchor.get(
            "href"
        ),
        base_url,
    )


# ============================================================
# SINGLE LISTING PAGE DISCOVERY
# ============================================================

def discover_listing_page(
    session,
    page_url,
    page_number,
):
    """
    Fetch and parse exactly one listing page.
    """

    response = session.get(
        page_url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    archive = soup.find(
        "div",
        class_="archive-container",
    )

    if archive is None:

        raise RuntimeError(
            "archive-container not found.\n"
            f"Page: {response.url}"
        )

    records = (
        extract_release_records(
            archive=archive,
            base_url=response.url,
            listing_page_number=page_number,
        )
    )

    next_url = (
        extract_next_page_url(
            soup,
            response.url,
        )
    )

    return (
        records,
        next_url,
        response.url,
    )


# ============================================================
# COMPLETE SERIES DISCOVERY
# ============================================================

def discover_all_releases(
    session,
):
    """
    Traverse complete listing archive.
    """

    records = []

    visited_pages = set()

    current_url = (
        LISTING_URL
    )

    page_number = 1

    while current_url is not None:

        if page_number > MAX_LISTING_PAGES:

            raise RuntimeError(
                "Listing pagination exceeded safety "
                f"limit of {MAX_LISTING_PAGES} pages."
            )

        if current_url in visited_pages:

            raise RuntimeError(
                "Listing pagination loop detected: "
                f"{current_url}"
            )

        visited_pages.add(
            current_url
        )

        (
            page_records,
            next_url,
            actual_page_url,
        ) = discover_listing_page(
            session=session,
            page_url=current_url,
            page_number=page_number,
        )

        print(
            f"Page {page_number:>2} | "
            f"releases={len(page_records):>2} | "
            f"next="
            f"{'YES' if next_url else 'NO'}"
        )

        records.extend(
            page_records
        )

        current_url = (
            next_url
        )

        page_number += 1

    return records


# ============================================================
# DISCOVERY VALIDATION
# ============================================================

def validate_discovered_releases(
    records,
):
    """
    Validate listing discovery before registry processing.
    """

    if not records:

        raise RuntimeError(
            "No releases discovered."
        )

    for index, record in enumerate(
        records,
        start=1,
    ):

        if not record.title:

            raise RuntimeError(
                f"Record {index} has no title."
            )

        if not record.publication_url:

            raise RuntimeError(
                f"Record {index} has no publication URL."
            )

        if not record.listing_page_url:

            raise RuntimeError(
                f"Record {index} has no listing-page URL."
            )

        if record.listing_page_number < 1:

            raise RuntimeError(
                f"Record {index} has invalid "
                "listing_page_number."
            )

    urls = [
        record.publication_url
        for record in records
    ]

    url_counts = {}

    for url in urls:

        url_counts[
            url
        ] = (
            url_counts.get(
                url,
                0,
            )
            + 1
        )

    duplicate_urls = [
        url
        for url, count
        in url_counts.items()
        if count > 1
    ]

    if duplicate_urls:

        raise RuntimeError(
            "Duplicate publication URLs discovered:\n"
            + "\n".join(
                duplicate_urls
            )
        )


# ============================================================
# REGISTRY CREATION / LOADING
# ============================================================

def create_empty_registry():
    """
    Create empty in-memory release registry.
    """

    now = utc_now_iso()

    return {
        "schema_version":
            REGISTRY_SCHEMA_VERSION,

        "registry_type":
            REGISTRY_TYPE,

        "source":
            SOURCE_NAME,

        "series": {
            "series_code":
                SERIES_CODE,

            "series_name":
                SERIES_NAME,

            "listing_url":
                LISTING_URL,
        },

        "created_at_utc":
            now,

        "updated_at_utc":
            now,

        "releases":
            [],
    }


def validate_registry_structure(
    registry,
):
    """
    Validate loaded registry before using it.
    """

    if not isinstance(
        registry,
        dict,
    ):

        raise RuntimeError(
            "Registry root must be a JSON object."
        )

    if registry.get(
        "schema_version"
    ) != REGISTRY_SCHEMA_VERSION:

        raise RuntimeError(
            "Unsupported registry schema version.\n"
            f"Expected: {REGISTRY_SCHEMA_VERSION}\n"
            f"Actual  : "
            f"{registry.get('schema_version')}"
        )

    if registry.get(
        "registry_type"
    ) != REGISTRY_TYPE:

        raise RuntimeError(
            "Unexpected registry type."
        )

    series = registry.get(
        "series"
    )

    if not isinstance(
        series,
        dict,
    ):

        raise RuntimeError(
            "Registry series metadata missing."
        )

    if series.get(
        "series_code"
    ) != SERIES_CODE:

        raise RuntimeError(
            "Registry series_code does not match "
            "current pipeline configuration."
        )

    releases = registry.get(
        "releases"
    )

    if not isinstance(
        releases,
        list,
    ):

        raise RuntimeError(
            "Registry releases must be a list."
        )

    release_ids = []

    for entry in releases:

        release_id = entry.get(
            "release_id"
        )

        if not release_id:

            raise RuntimeError(
                "Registry contains release "
                "without release_id."
            )

        release_ids.append(
            release_id
        )

    if len(release_ids) != len(
        set(release_ids)
    ):

        raise RuntimeError(
            "Registry contains duplicate "
            "release_id values."
        )


def load_release_registry():
    """
    Load persistent release registry.
    """

    if not RELEASE_REGISTRY_PATH.exists():

        return (
            create_empty_registry(),
            False,
        )

    try:

        with RELEASE_REGISTRY_PATH.open(
            "r",
            encoding="utf-8",
        ) as file:

            registry = json.load(
                file
            )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "Release registry is not valid JSON:\n"
            f"{RELEASE_REGISTRY_PATH}"
        ) from exc

    validate_registry_structure(
        registry
    )

    return (
        registry,
        True,
    )


# ============================================================
# REGISTRY IDENTITY
# ============================================================

def generate_release_id():
    """
    Generate internal stable release identifier.
    """

    return (
        "nso_release_"
        + uuid.uuid4().hex
    )


def build_registry_indexes(
    registry,
):
    """
    Build conservative identity indexes.

    Primary:
        publication_url

    Fallback:
        series_code + publication_date + title
    """

    by_url = {}

    by_natural_signature = {}

    for entry in registry[
        "releases"
    ]:

        current = entry.get(
            "current",
            {},
        )

        publication_url = (
            current.get(
                "publication_url"
            )
        )

        if publication_url:

            if publication_url in by_url:

                raise RuntimeError(
                    "Registry contains duplicate "
                    "current publication URLs:\n"
                    f"{publication_url}"
                )

            by_url[
                publication_url
            ] = entry

        signature = (
            current.get(
                "series_code"
            ),
            current.get(
                "publication_date"
            ),
            current.get(
                "title"
            ),
        )

        if all(
            value is not None
            for value in signature
        ):

            if signature in by_natural_signature:

                by_natural_signature[
                    signature
                ] = None

            else:

                by_natural_signature[
                    signature
                ] = entry

    return (
        by_url,
        by_natural_signature,
    )


def resolve_release_identity(
    record,
    by_url,
    by_natural_signature,
):
    """
    Resolve discovered release against registry.
    """

    entry = by_url.get(
        record.publication_url
    )

    if entry is not None:

        return (
            entry,
            "publication_url",
        )

    signature = (
        record.series_code,
        record.publication_date,
        record.title,
    )

    if all(
        value is not None
        for value in signature
    ):

        entry = (
            by_natural_signature.get(
                signature
            )
        )

        if entry is not None:

            return (
                entry,
                "natural_signature",
            )

    return (
        None,
        "new",
    )


# ============================================================
# REGISTRY SOURCE STATE
# ============================================================

def release_record_to_state(
    record,
):
    """
    Convert ReleaseRecord into registry source state.
    """

    return asdict(
        record
    )


def compare_source_states(
    old_state,
    new_state,
):
    """
    Return field-level source metadata differences.
    """

    changes = {}

    keys = set(
        old_state.keys()
    ) | set(
        new_state.keys()
    )

    for key in sorted(
        keys
    ):

        old_value = old_state.get(
            key
        )

        new_value = new_state.get(
            key
        )

        if old_value != new_value:

            changes[
                key
            ] = {
                "old":
                    old_value,

                "new":
                    new_value,
            }

    return changes


# ============================================================
# NEW REGISTRY ENTRY
# ============================================================

def create_registry_entry(
    record,
    observed_at,
):
    """
    Create registry entry for newly discovered release.
    """

    state = (
        release_record_to_state(
            record
        )
    )

    return {
        "release_id":
            generate_release_id(),

        "first_seen_at_utc":
            observed_at,

        "last_seen_at_utc":
            observed_at,

        "current":
            state,

        "history":
            [],
    }


# ============================================================
# REGISTRY SYNCHRONIZATION
# ============================================================

def synchronize_registry(
    registry,
    records,
):
    """
    Synchronize current discovery against persistent registry.
    """

    observed_at = (
        utc_now_iso()
    )

    (
        by_url,
        by_natural_signature,
    ) = build_registry_indexes(
        registry
    )

    stats = {
        "discovered":
            len(records),

        "new":
            0,

        "unchanged":
            0,

        "changed":
            0,

        "matched_by_url":
            0,

        "matched_by_natural_signature":
            0,
    }

    matched_release_ids = set()

    for record in records:

        (
            entry,
            match_method,
        ) = resolve_release_identity(
            record=record,
            by_url=by_url,
            by_natural_signature=(
                by_natural_signature
            ),
        )

        # ====================================================
        # NEW
        # ====================================================

        if entry is None:

            new_entry = (
                create_registry_entry(
                    record=record,
                    observed_at=observed_at,
                )
            )

            registry[
                "releases"
            ].append(
                new_entry
            )

            stats[
                "new"
            ] += 1

            current = new_entry[
                "current"
            ]

            by_url[
                current[
                    "publication_url"
                ]
            ] = new_entry

            signature = (
                current.get(
                    "series_code"
                ),
                current.get(
                    "publication_date"
                ),
                current.get(
                    "title"
                ),
            )

            if all(
                value is not None
                for value in signature
            ):

                existing = (
                    by_natural_signature.get(
                        signature
                    )
                )

                if (
                    existing is None
                    and signature
                    not in by_natural_signature
                ):

                    by_natural_signature[
                        signature
                    ] = new_entry

                else:

                    by_natural_signature[
                        signature
                    ] = None

            matched_release_ids.add(
                new_entry[
                    "release_id"
                ]
            )

            continue

        # ====================================================
        # EXISTING
        # ====================================================

        release_id = entry[
            "release_id"
        ]

        if release_id in matched_release_ids:

            raise RuntimeError(
                "Multiple discovered records resolved "
                "to same registry release.\n"
                f"release_id: {release_id}"
            )

        matched_release_ids.add(
            release_id
        )

        if match_method == (
            "publication_url"
        ):

            stats[
                "matched_by_url"
            ] += 1

        elif match_method == (
            "natural_signature"
        ):

            stats[
                "matched_by_natural_signature"
            ] += 1

        old_state = entry[
            "current"
        ]

        new_state = (
            release_record_to_state(
                record
            )
        )

        changes = (
            compare_source_states(
                old_state,
                new_state,
            )
        )

        # ----------------------------------------------------
        # UNCHANGED
        # ----------------------------------------------------

        if not changes:

            stats[
                "unchanged"
            ] += 1

            entry[
                "last_seen_at_utc"
            ] = observed_at

            continue

        # ----------------------------------------------------
        # CHANGED
        # ----------------------------------------------------

        stats[
            "changed"
        ] += 1

        history_event = {
            "observed_at_utc":
                observed_at,

            "event":
                "source_metadata_changed",

            "match_method":
                match_method,

            "changes":
                changes,

            "previous_state":
                old_state,
        }

        entry[
            "history"
        ].append(
            history_event
        )

        entry[
            "current"
        ] = new_state

        entry[
            "last_seen_at_utc"
        ] = observed_at

    registry[
        "updated_at_utc"
    ] = observed_at

    return stats


# ============================================================
# REGISTRY VALIDATION
# ============================================================

def validate_synchronized_registry(
    registry,
):
    """
    Validate registry integrity before persistent write.
    """

    validate_registry_structure(
        registry
    )

    release_ids = []

    current_urls = []

    for entry in registry[
        "releases"
    ]:

        release_id = entry.get(
            "release_id"
        )

        current = entry.get(
            "current"
        )

        if not isinstance(
            current,
            dict,
        ):

            raise RuntimeError(
                "Registry release has no valid "
                "current source state.\n"
                f"release_id: {release_id}"
            )

        publication_url = (
            current.get(
                "publication_url"
            )
        )

        if not publication_url:

            raise RuntimeError(
                "Registry release current state has "
                "no publication URL.\n"
                f"release_id: {release_id}"
            )

        history = entry.get(
            "history"
        )

        if not isinstance(
            history,
            list,
        ):

            raise RuntimeError(
                "Registry release history must be list.\n"
                f"release_id: {release_id}"
            )

        release_ids.append(
            release_id
        )

        current_urls.append(
            publication_url
        )

    if len(release_ids) != len(
        set(release_ids)
    ):

        raise RuntimeError(
            "Duplicate release IDs detected."
        )

    if len(current_urls) != len(
        set(current_urls)
    ):

        raise RuntimeError(
            "Duplicate current publication URLs detected."
        )


# ============================================================
# ATOMIC REGISTRY WRITE
# ============================================================

def write_registry_atomic(
    registry,
):
    """
    Persist registry using temporary file + atomic replacement.
    """

    temporary_path = (
        RELEASE_REGISTRY_PATH.with_suffix(
            RELEASE_REGISTRY_PATH.suffix
            + ".tmp"
        )
    )

    try:

        with temporary_path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                registry,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write(
                "\n"
            )

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temporary_path,
            RELEASE_REGISTRY_PATH,
        )

    finally:

        if temporary_path.exists():

            try:

                temporary_path.unlink()

            except OSError:

                pass


# ============================================================
# DETAIL-PAGE ARTIFACT DISCOVERY
# ============================================================

def get_url_extension(url):
    """
    Return lowercase file extension from URL path.

    Query strings and fragments do not affect classification.
    """

    path = urlparse(
        url
    ).path

    return Path(
        path
    ).suffix.lower()


def classify_artifact_url(url):
    """
    Classify artifact URL by file extension.
    """

    extension = (
        get_url_extension(
            url
        )
    )

    return (
        ARTIFACT_EXTENSION_TYPES.get(
            extension
        )
    )


def source_filename_from_url(url):
    """
    Extract source filename from actual URL path.

    No filename reconstruction is performed.
    """

    path = urlparse(
        url
    ).path

    filename = Path(
        path
    ).name

    return (
        filename
        if filename
        else None
    )


def discover_artifacts_from_html(
    soup,
    base_url,
):
    """
    Discover supported artifact links from one detail page.

    Artifact identity is based on actual source href.

    Duplicate occurrences of the same normalized URL on one page
    are collapsed into one ArtifactCandidate.
    """

    artifacts = []

    seen_urls = set()

    for anchor in soup.find_all(
        "a",
        href=True,
    ):

        artifact_url = (
            normalize_url(
                anchor.get(
                    "href"
                ),
                base_url,
            )
        )

        if not artifact_url:
            continue

        artifact_type = (
            classify_artifact_url(
                artifact_url
            )
        )

        if artifact_type is None:
            continue

        if artifact_url in seen_urls:
            continue

        seen_urls.add(
            artifact_url
        )

        source_filename = (
            source_filename_from_url(
                artifact_url
            )
        )

        if not source_filename:
            continue

        anchor_text = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        artifacts.append(
            ArtifactCandidate(
                artifact_url=artifact_url,
                artifact_type=artifact_type,
                source_filename=source_filename,
                anchor_text=anchor_text,
            )
        )

    return tuple(
        artifacts
    )


def discover_release_artifacts(
    session,
    release_entry,
):
    """
    Inspect one release detail page.

    Downloads HTML only.

    Artifact bytes are NOT downloaded.
    """

    release_id = (
        release_entry[
            "release_id"
        ]
    )

    publication_url = (
        release_entry[
            "current"
        ][
            "publication_url"
        ]
    )

    try:

        response = session.get(
            publication_url,
            timeout=REQUEST_TIMEOUT,
        )

        http_status = (
            response.status_code
        )

        final_page_url = (
            response.url
        )

        if not response.ok:

            return (
                ReleaseArtifactDiscovery(
                    release_id=release_id,
                    publication_url=publication_url,
                    final_page_url=final_page_url,
                    http_status=http_status,
                    artifacts=tuple(),
                    error=(
                        f"HTTP {http_status}"
                    ),
                )
            )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        artifacts = (
            discover_artifacts_from_html(
                soup=soup,
                base_url=final_page_url,
            )
        )

        return (
            ReleaseArtifactDiscovery(
                release_id=release_id,
                publication_url=publication_url,
                final_page_url=final_page_url,
                http_status=http_status,
                artifacts=artifacts,
                error=None,
            )
        )

    except requests.RequestException as exc:

        return (
            ReleaseArtifactDiscovery(
                release_id=release_id,
                publication_url=publication_url,
                final_page_url=None,
                http_status=None,
                artifacts=tuple(),
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )
        )


def discover_all_release_artifacts(
    session,
    registry,
):
    """
    Inspect every registered release detail page.

    No artifact bytes are downloaded.
    """

    results = []

    releases = (
        registry[
            "releases"
        ]
    )

    total = len(
        releases
    )

    print()

    print(
        "Inspecting publication detail pages..."
    )

    print()

    for index, entry in enumerate(
        releases,
        start=1,
    ):

        result = (
            discover_release_artifacts(
                session=session,
                release_entry=entry,
            )
        )

        results.append(
            result
        )

        # Bounded progress output.
        if (
            index == 1
            or index % 25 == 0
            or index == total
        ):

            print(
                f"Detail pages "
                f"{index:>3}/{total} | "
                f"HTTP="
                f"{result.http_status} | "
                f"artifacts="
                f"{len(result.artifacts)}"
            )

    return results


# ============================================================
# DETAIL-PAGE DISCOVERY VALIDATION
# ============================================================

def validate_artifact_discovery_results(
    registry,
    results,
):
    """
    Validate whole-archive detail discovery bookkeeping.

    Zero artifacts is not automatically considered an error.
    """

    registry_release_ids = {
        entry[
            "release_id"
        ]
        for entry in registry[
            "releases"
        ]
    }

    result_release_ids = [
        result.release_id
        for result in results
    ]

    if len(
        result_release_ids
    ) != len(
        registry_release_ids
    ):

        raise RuntimeError(
            "Detail-page discovery count does not match "
            "registry release count.\n"
            f"Registry releases : "
            f"{len(registry_release_ids)}\n"
            f"Discovery results : "
            f"{len(result_release_ids)}"
        )

    if len(
        result_release_ids
    ) != len(
        set(result_release_ids)
    ):

        raise RuntimeError(
            "Duplicate release_id detected in "
            "detail-page discovery results."
        )

    if set(
        result_release_ids
    ) != registry_release_ids:

        raise RuntimeError(
            "Detail-page discovery release IDs do not "
            "match registry release IDs."
        )


# ============================================================
# DISCOVERY SUMMARY
# ============================================================

def print_discovery_summary(
    records,
):
    """
    Print bounded listing-discovery diagnostics.
    """

    publication_dates_present = sum(
        record.publication_date
        is not None
        for record in records
    )

    reference_periods_present = sum(
        record.reference_period
        is not None
        for record in records
    )

    next_release_dates_present = sum(
        record.next_release_date
        is not None
        for record in records
    )

    publication_urls = [
        record.publication_url
        for record in records
    ]

    listing_pages = {
        record.listing_page_number
        for record in records
    }

    print()

    print(
        "=" * 88
    )

    print(
        "DISCOVERY SUMMARY"
    )

    print(
        "=" * 88
    )

    print(
        f"Listing pages           : "
        f"{len(listing_pages)}"
    )

    print(
        f"Release records         : "
        f"{len(records)}"
    )

    print(
        f"Unique publication URLs : "
        f"{len(set(publication_urls))}"
    )

    print()

    print(
        "FIELD COMPLETENESS"
    )

    print(
        "-" * 88
    )

    print(
        f"title                   : "
        f"{len(records)}/{len(records)}"
    )

    print(
        f"publication_date        : "
        f"{publication_dates_present}/"
        f"{len(records)}"
    )

    print(
        f"reference_period        : "
        f"{reference_periods_present}/"
        f"{len(records)}"
    )

    print(
        f"next_release_date       : "
        f"{next_release_dates_present}/"
        f"{len(records)}"
    )

    print(
        f"publication_url         : "
        f"{len(publication_urls)}/"
        f"{len(records)}"
    )


# ============================================================
# REGISTRY SUMMARY
# ============================================================

def print_registry_summary(
    registry,
    registry_existed,
    stats,
):
    """
    Print registry synchronization results.
    """

    print()

    print(
        "=" * 88
    )

    print(
        "RELEASE REGISTRY SUMMARY"
    )

    print(
        "=" * 88
    )

    print(
        f"Registry existed before run : "
        f"{registry_existed}"
    )

    print(
        f"Registry path               : "
        f"{RELEASE_REGISTRY_PATH}"
    )

    print(
        f"Registry schema             : "
        f"{REGISTRY_SCHEMA_VERSION}"
    )

    print()

    print(
        f"Discovered this run         : "
        f"{stats['discovered']}"
    )

    print(
        f"NEW                         : "
        f"{stats['new']}"
    )

    print(
        f"UNCHANGED                   : "
        f"{stats['unchanged']}"
    )

    print(
        f"CHANGED                     : "
        f"{stats['changed']}"
    )

    print()

    print(
        f"Matched by URL              : "
        f"{stats['matched_by_url']}"
    )

    print(
        f"Matched by natural signature: "
        f"{stats['matched_by_natural_signature']}"
    )

    print()

    print(
        f"Total registry releases     : "
        f"{len(registry['releases'])}"
    )

    history_events = sum(
        len(
            entry.get(
                "history",
                [],
            )
        )
        for entry in registry[
            "releases"
        ]
    )

    print(
        f"Metadata history events     : "
        f"{history_events}"
    )


# ============================================================
# DETAIL-PAGE DISCOVERY SUMMARY
# ============================================================

def print_artifact_discovery_summary(
    results,
):
    """
    Print whole-archive artifact inventory diagnostics.
    """

    total_releases = len(
        results
    )

    http_success = sum(
        result.http_status is not None
        and 200 <= result.http_status < 300
        for result in results
    )

    redirected = sum(
        result.final_page_url is not None
        and result.final_page_url
        != result.publication_url
        for result in results
    )

    error_results = [
        result
        for result in results
        if result.error is not None
    ]

    releases_with_artifacts = sum(
        len(
            result.artifacts
        ) > 0
        for result in results
    )

    releases_without_artifacts = (
        total_releases
        - releases_with_artifacts
    )

    all_artifacts = [
        artifact
        for result in results
        for artifact in result.artifacts
    ]

    artifact_type_counts = {
        "excel": 0,
        "word": 0,
        "pdf": 0,
        "archive": 0,
    }

    releases_by_type = {
        "excel": 0,
        "word": 0,
        "pdf": 0,
        "archive": 0,
    }

    for artifact in all_artifacts:

        artifact_type_counts[
            artifact.artifact_type
        ] += 1

    for result in results:

        types_present = {
            artifact.artifact_type
            for artifact in result.artifacts
        }

        for artifact_type in (
            releases_by_type
        ):

            if artifact_type in types_present:

                releases_by_type[
                    artifact_type
                ] += 1

    artifact_urls = [
        artifact.artifact_url
        for artifact in all_artifacts
    ]

    unique_artifact_urls = set(
        artifact_urls
    )

    duplicate_artifact_occurrences = (
        len(artifact_urls)
        - len(unique_artifact_urls)
    )

    print()

    print(
        "=" * 88
    )

    print(
        "DETAIL-PAGE ARTIFACT DISCOVERY SUMMARY"
    )

    print(
        "=" * 88
    )

    print(
        f"Releases inspected          : "
        f"{total_releases}"
    )

    print(
        f"HTTP 2xx                    : "
        f"{http_success}"
    )

    print(
        f"Redirected final URLs       : "
        f"{redirected}"
    )

    print(
        f"Request / HTTP errors       : "
        f"{len(error_results)}"
    )

    print()

    print(
        f"Releases with artifacts     : "
        f"{releases_with_artifacts}"
    )

    print(
        f"Releases without artifacts  : "
        f"{releases_without_artifacts}"
    )

    print(
        f"Artifact occurrences        : "
        f"{len(all_artifacts)}"
    )

    print(
        f"Unique artifact URLs        : "
        f"{len(unique_artifact_urls)}"
    )

    print(
        f"Cross-release duplicate URLs: "
        f"{duplicate_artifact_occurrences}"
    )

    print()

    print(
        "ARTIFACT TYPE DISTRIBUTION"
    )

    print(
        "-" * 88
    )

    for artifact_type in (
        "excel",
        "word",
        "pdf",
        "archive",
    ):

        print(
            f"{artifact_type:<8} | "
            f"artifacts="
            f"{artifact_type_counts[artifact_type]:>4} | "
            f"releases="
            f"{releases_by_type[artifact_type]:>4}"
        )

    # --------------------------------------------------------
    # Artifact count distribution
    # --------------------------------------------------------

    count_distribution = {}

    for result in results:

        artifact_count = len(
            result.artifacts
        )

        count_distribution[
            artifact_count
        ] = (
            count_distribution.get(
                artifact_count,
                0,
            )
            + 1
        )

    print()

    print(
        "ARTIFACT COUNT PER RELEASE"
    )

    print(
        "-" * 88
    )

    for artifact_count in sorted(
        count_distribution
    ):

        print(
            f"{artifact_count:>2} artifact(s) : "
            f"{count_distribution[artifact_count]:>3} "
            f"release(s)"
        )

    # --------------------------------------------------------
    # Zero-artifact samples
    # --------------------------------------------------------

    zero_artifact_results = [
        result
        for result in results
        if (
            result.error is None
            and len(
                result.artifacts
            ) == 0
        )
    ]

    if zero_artifact_results:

        print()

        print(
            "SAMPLE RELEASES WITH ZERO ARTIFACTS"
        )

        print(
            "-" * 88
        )

        for result in zero_artifact_results[
            :10
        ]:

            print(
                result.publication_url
            )

    # --------------------------------------------------------
    # Error samples
    # --------------------------------------------------------

    if error_results:

        print()

        print(
            "SAMPLE DETAIL-PAGE ERRORS"
        )

        print(
            "-" * 88
        )

        for result in error_results[
            :10
        ]:

            print(
                f"{result.error} | "
                f"{result.publication_url}"
            )



# ============================================================
# PRODUCTION ARTIFACT STATE / REVISION MANAGEMENT
# ============================================================

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_transport_url(source_url):
    """Use HTTPS transport for historical HTTP links on www.nso.gov.vn only."""
    parts = urlsplit(source_url)
    if (
        parts.scheme.lower() == "http"
        and parts.hostname
        and parts.hostname.lower() == "www.nso.gov.vn"
    ):
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))
    return source_url


_WEB_LAYOUT = None


def get_web_layout():
    global _WEB_LAYOUT
    if _WEB_LAYOUT is None:
        _WEB_LAYOUT = WebLayout(SERIES_ROOT)
    return _WEB_LAYOUT


def get_release_directory(release_id):
    return get_web_layout().directory(release_id)


def get_manifest_path(release_id):
    return get_release_directory(release_id) / MANIFEST_FILENAME


def load_release_manifest(release_id):
    path = get_manifest_path(release_id)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    if manifest.get("release_id") != release_id:
        raise RuntimeError(f"Manifest release_id mismatch: {path}")
    return manifest


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def generate_artifact_id():
    return "nso_artifact_" + uuid.uuid4().hex


def get_current_revision(artifact_entry):
    number = artifact_entry["current_revision"]
    for revision in artifact_entry["revisions"]:
        if revision["revision_number"] == number:
            return revision
    raise RuntimeError(f"Current revision missing: {artifact_entry['artifact_id']}")


def get_revision_target(release_id, artifact_id, revision_number, source_filename):
    return get_web_layout().target(release_id, artifact_id,
                                   classify_artifact_url(source_filename),
                                   revision_number, source_filename)


def get_initial_target(release_id, artifact_id, candidate):
    return get_web_layout().target(release_id, artifact_id, candidate.artifact_type,
                                   1, candidate.source_filename)


def remote_request_with_retry(session, source_url, temporary_path, headers=None):
    """Conditional/full GET with retry. Returns (status, metadata); 304 writes no bytes."""
    last_error = None
    transport_url = build_transport_url(source_url)
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        if temporary_path.exists():
            temporary_path.unlink()
        try:
            with session.get(
                transport_url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
                headers=headers or {},
            ) as response:
                if response.status_code == 304:
                    return 304, {
                        "transport_url": response.url,
                        "http_status": 304,
                        "content_type": response.headers.get("Content-Type"),
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "content_length": response.headers.get("Content-Length"),
                    }
                response.raise_for_status()
                metadata = {
                    "transport_url": response.url,
                    "http_status": response.status_code,
                    "content_type": response.headers.get("Content-Type"),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "content_length": response.headers.get("Content-Length"),
                }
                with temporary_path.open("xb") as file:
                    for chunk in response.iter_content(chunk_size=HASH_CHUNK_SIZE):
                        if chunk:
                            file.write(chunk)
                    file.flush()
                    os.fsync(file.fileno())
                return response.status_code, metadata
        except requests.RequestException as exc:
            last_error = exc
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
            if attempt == MAX_DOWNLOAD_ATTEMPTS:
                break
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        "Artifact request failed after retries.\n"
        f"Source URL: {source_url}\nLast error: {last_error}"
    ) from last_error


def build_conditional_headers(artifact_entry):
    observed = artifact_entry.get("last_observed_http") or {}
    headers = {}
    if observed.get("etag"):
        headers["If-None-Match"] = observed["etag"]
    if observed.get("last_modified"):
        headers["If-Modified-Since"] = observed["last_modified"]
    return headers


def validate_manifest(manifest):
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeError("Manifest artifacts must be a list.")
    if manifest.get("artifact_count") != len(artifacts):
        raise RuntimeError(f"Manifest artifact_count mismatch: {manifest['release_id']}")
    seen_urls = set()
    seen_ids = set()
    for artifact in artifacts:
        if artifact["source_url"] in seen_urls:
            raise RuntimeError("Duplicate artifact source_url within release.")
        if artifact["artifact_id"] in seen_ids:
            raise RuntimeError("Duplicate artifact_id within release.")
        seen_urls.add(artifact["source_url"])
        seen_ids.add(artifact["artifact_id"])
        revisions = artifact["revisions"]
        numbers = [item["revision_number"] for item in revisions]
        if numbers != list(range(1, len(revisions) + 1)):
            raise RuntimeError(f"Invalid revision sequence: {artifact['artifact_id']}")
        if artifact["current_revision"] != numbers[-1]:
            raise RuntimeError(f"Invalid current_revision: {artifact['artifact_id']}")
        for revision in revisions:
            path = safe_path(SERIES_ROOT, revision["local_relative_path"])
            if not path.exists():
                raise RuntimeError(f"Manifest references missing file: {path}")
            if path.stat().st_size != revision["byte_size"]:
                raise RuntimeError(f"Manifest byte-size mismatch: {path}")
            if sha256_file(path) != revision["sha256"]:
                raise RuntimeError(f"Manifest SHA-256 mismatch: {path}")


def build_revision_record(number, observed_at, sha256, byte_size, path, http_metadata):
    return {
        "revision_number": number,
        "observed_at_utc": observed_at,
        "sha256": sha256,
        "byte_size": byte_size,
        "local_relative_path": path.relative_to(SERIES_ROOT).as_posix(),
        "http": http_metadata,
    }


def acquire_new_artifact(session, release_id, candidate, observed_at, position):
    artifact_id = generate_artifact_id()
    temp_dir = get_release_directory(release_id) / ".remote_check"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"artifact_{position:04d}.part"
    status, metadata = remote_request_with_retry(session, candidate.artifact_url, temp)
    if status == 304 or not temp.exists() or temp.stat().st_size <= 0:
        raise RuntimeError(f"New artifact returned no bytes: {candidate.artifact_url}")
    sha256 = sha256_file(temp)
    target = get_initial_target(release_id, artifact_id, candidate)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"Initial artifact target already exists: {target}")
    os.replace(temp, target)
    revision = build_revision_record(1, observed_at, sha256, target.stat().st_size, target, metadata)
    return {
        "artifact_id": artifact_id,
        "artifact_type": candidate.artifact_type,
        "source_url": candidate.artifact_url,
        "source_filename": candidate.source_filename,
        "current_revision": 1,
        "last_observed_at_utc": observed_at,
        "last_observed_http": metadata,
        "last_source_status": "present",
        "last_source_status_at_utc": observed_at,
        "revisions": [revision],
    }


def reconcile_existing_artifact(session, release_id, artifact_entry, candidate, observed_at, position):
    temp_dir = get_release_directory(release_id) / ".remote_check"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"artifact_{position:04d}.part"
    headers = build_conditional_headers(artifact_entry)
    status, metadata = remote_request_with_retry(
        session, candidate.artifact_url, temp, headers=headers
    )
    if status == 304:
        artifact_entry["last_observed_at_utc"] = observed_at
        artifact_entry["last_observed_http"] = metadata
        return "UNCHANGED_304"
    if not temp.exists() or temp.stat().st_size <= 0:
        raise RuntimeError(f"Remote artifact returned no bytes: {candidate.artifact_url}")
    remote_hash = sha256_file(temp)
    current = get_current_revision(artifact_entry)
    if remote_hash == current["sha256"]:
        temp.unlink()
        artifact_entry["last_observed_at_utc"] = observed_at
        artifact_entry["last_observed_http"] = metadata
        return "UNCHANGED_HASH"
    new_number = artifact_entry["current_revision"] + 1
    target = get_revision_target(
        release_id, artifact_entry["artifact_id"], new_number, candidate.source_filename
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"Immutable revision target already exists: {target}")
    os.replace(temp, target)
    revision = build_revision_record(
        new_number, observed_at, remote_hash, target.stat().st_size, target, metadata
    )
    artifact_entry["revisions"].append(revision)
    artifact_entry["current_revision"] = new_number
    artifact_entry["artifact_type"] = candidate.artifact_type
    artifact_entry["source_filename"] = candidate.source_filename
    artifact_entry["last_observed_at_utc"] = observed_at
    artifact_entry["last_observed_http"] = metadata
    return "CHANGED"


def create_empty_release_manifest(registry_entry, observed_at):
    return {
        "schema_version": "1.0",
        "manifest_type": "nso_web_release_artifact_manifest",
        "release_id": registry_entry["release_id"],
        "created_at_utc": observed_at,
        "updated_at_utc": observed_at,
        "release_snapshot": registry_entry["current"],
        "artifact_count": 0,
        "artifacts": [],
    }


def reconcile_release_artifacts(session, registry_entry, discovery_result):
    get_web_layout().register(registry_entry)
    release_id = registry_entry["release_id"]
    observed_at = utc_now_iso()
    manifest = load_release_manifest(release_id)
    manifest_created = manifest is None
    if manifest_created:
        manifest = create_empty_release_manifest(registry_entry, observed_at)
    else:
        validate_manifest(manifest)
    by_url = {item["source_url"]: item for item in manifest["artifacts"]}
    current_urls = {item.artifact_url for item in discovery_result.artifacts}
    stats = {
        "manifest_created": int(manifest_created),
        "new": 0,
        "changed": 0,
        "unchanged_304": 0,
        "unchanged_hash": 0,
        "missing": 0,
    }
    for position, candidate in enumerate(sorted(discovery_result.artifacts, key=lambda a: a.artifact_url), start=1):
        existing = by_url.get(candidate.artifact_url)
        if existing is None:
            entry = acquire_new_artifact(
                session, release_id, candidate, observed_at, position
            )
            manifest["artifacts"].append(entry)
            by_url[candidate.artifact_url] = entry
            stats["new"] += 1
        else:
            result = reconcile_existing_artifact(
                session, release_id, existing, candidate, observed_at, position
            )
            if result == "CHANGED":
                stats["changed"] += 1
            elif result == "UNCHANGED_304":
                stats["unchanged_304"] += 1
            else:
                stats["unchanged_hash"] += 1
    for entry in manifest["artifacts"]:
        if entry["source_url"] in current_urls:
            entry["last_source_status"] = "present"
        else:
            entry["last_source_status"] = "missing_from_current_source"
            stats["missing"] += 1
        entry["last_source_status_at_utc"] = observed_at
    if release_id in get_web_layout().versioned:
        manifest["local_layout"] = get_web_layout().layouts[release_id]
    manifest["artifact_count"] = len(manifest["artifacts"])
    manifest["release_snapshot"] = registry_entry["current"]
    manifest["updated_at_utc"] = observed_at
    manifest["last_reconciled_at_utc"] = observed_at
    validate_manifest(manifest)
    write_json_atomic(get_manifest_path(release_id), manifest)
    temp_dir = get_release_directory(release_id) / ".remote_check"
    if temp_dir.exists() and not any(temp_dir.iterdir()):
        temp_dir.rmdir()
    return stats


def reconcile_all_release_artifacts(session, registry, discovery_results):
    registry_index = {entry["release_id"]: entry for entry in registry["releases"]}
    totals = {
        "manifest_created": 0,
        "new": 0,
        "changed": 0,
        "unchanged_304": 0,
        "unchanged_hash": 0,
        "missing": 0,
    }
    total = len(discovery_results)
    for index, result in enumerate(discovery_results, start=1):
        entry = registry_index[result.release_id]
        stats = reconcile_release_artifacts(session, entry, result)
        for key in totals:
            totals[key] += stats[key]
        if index == 1 or index % 25 == 0 or index == total:
            unchanged = totals["unchanged_304"] + totals["unchanged_hash"]
            print(
                f"Reconcile {index:>3}/{total} | unchanged={unchanged:>3} | "
                f"changed={totals['changed']:>3} | new={totals['new']:>3} | "
                f"missing={totals['missing']:>3}"
            )
    return totals


def validate_end_to_end_state(registry, discovery_results):
    registry_ids = {entry["release_id"] for entry in registry["releases"]}
    discovery_ids = {result.release_id for result in discovery_results}
    if registry_ids != discovery_ids:
        raise RuntimeError("Registry/detail discovery release populations differ.")
    artifact_ids = set()
    total_artifacts = 0
    total_revisions = 0
    manifest_count = 0
    for release_id in registry_ids:
        manifest = load_release_manifest(release_id)
        if manifest is None:
            raise RuntimeError(f"Manifest missing after reconciliation: {release_id}")
        validate_manifest(manifest)
        manifest_count += 1
        for artifact in manifest["artifacts"]:
            if artifact["artifact_id"] in artifact_ids:
                raise RuntimeError(f"Duplicate artifact_id globally: {artifact['artifact_id']}")
            artifact_ids.add(artifact["artifact_id"])
            total_artifacts += 1
            total_revisions += len(artifact["revisions"])
    return {
        "registry_releases": len(registry_ids),
        "detail_results": len(discovery_ids),
        "manifests": manifest_count,
        "artifacts": total_artifacts,
        "revisions": total_revisions,
        "unique_artifact_ids": len(artifact_ids),
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 88)
    print("NSO WEB ACQUISITION PIPELINE")
    print("=" * 88)

    validate_configuration()
    ensure_pipeline_directories()
    get_web_layout()  # Refuse an interrupted migration before network activity.
    session = build_session()

    print("\nDiscovering release archive...\n")
    records = discover_all_releases(session)
    validate_discovered_releases(records)
    print_discovery_summary(records)

    print("\nLoading and synchronizing release registry...")
    registry, registry_existed = load_release_registry()
    stats = synchronize_registry(registry=registry, records=records)
    validate_synchronized_registry(registry)
    write_registry_atomic(registry)
    print_registry_summary(registry, registry_existed, stats)
    get_web_layout().register_all(registry["releases"])

    artifact_results = discover_all_release_artifacts(session=session, registry=registry)
    validate_artifact_discovery_results(registry=registry, results=artifact_results)
    print_artifact_discovery_summary(artifact_results)

    if any(result.error is not None for result in artifact_results):
        raise RuntimeError("Artifact discovery contains detail-page errors; reconciliation aborted.")

    print("\nReconciling artifact manifests and immutable revisions...\n")
    artifact_stats = reconcile_all_release_artifacts(
        session=session,
        registry=registry,
        discovery_results=artifact_results,
    )

    print("\nValidating complete persistent state...")
    final = validate_end_to_end_state(registry, artifact_results)
    print("End-to-end persistent-state validation: PASS")

    unchanged = artifact_stats["unchanged_304"] + artifact_stats["unchanged_hash"]
    print("\n" + "=" * 88)
    print("PRODUCTION END-TO-END SUMMARY")
    print("=" * 88)
    print(f"Registry releases          : {final['registry_releases']}")
    print(f"Release manifests          : {final['manifests']}")
    print(f"Artifact records           : {final['artifacts']}")
    print(f"Revision records           : {final['revisions']}")
    print(f"Unique artifact IDs        : {final['unique_artifact_ids']}")
    print(f"Manifests created this run : {artifact_stats['manifest_created']}")
    print(f"UNCHANGED (304)            : {artifact_stats['unchanged_304']}")
    print(f"UNCHANGED (SHA-256)        : {artifact_stats['unchanged_hash']}")
    print(f"UNCHANGED total            : {unchanged}")
    print(f"CHANGED                    : {artifact_stats['changed']}")
    print(f"NEW artifact               : {artifact_stats['new']}")
    print(f"MISSING from source        : {artifact_stats['missing']}")

    print("\n" + "=" * 88)
    print("VALIDATION DECISION")
    print("=" * 88)
    print("PASS — production listing, registry, artifact discovery, manifest,")
    print("immutable revision, and idempotent reconciliation layers completed.")
    print("\nACQUISITION COMPLETE")
    print("=" * 88)


if __name__ == "__main__":
    main()
