# -*- coding: utf-8 -*-
"""Build and classify the Vietnam NSO PX-Web table catalog.

This module contains the production logic. 
Importing it has no network or file-system side effects; 
work is performed only when its functions are called.
"""

from pathlib import Path                    # represents file and folder paths as objects
import json
import time
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup               # HTML parsing
import pandas as pd
import requests

BASE = "https://pxweb.nso.gov.vn"
ROOT = f"{BASE}/pxweb/vi/"                  # define Vietnamese homepage of the stats DB
API_BASE = f"{BASE}/api/v1/vi"              # endpoint path for PX-Web API queries


def discover_databases(session=None, timeout=30):
    """Return the unique first-level PX-Web navigation/database entries."""
    session = session or requests.Session()             # reusable HTTP session
    response = session.get(ROOT, timeout=timeout)       # download homepage HTML
    response.raise_for_status()                         # stop if request failed
    soup = BeautifulSoup(response.text, "html.parser")  # parse HTML into searchable object

    rows = []
    for anchor in soup.find_all("a", href=True):        # loops through every hyperlink <a> tag that has an href attribute
        href = anchor["href"]                           # raw link URL
        text = anchor.get_text(" ", strip=True)         # visible link text
        path = urlparse(href).path.rstrip("/")          # strip domain + trailing slash
        parts = path.split("/")                         # eg. ['', 'pxweb', 'vi', 'DB1']

        if len(parts) == 4 and parts[1] == "pxweb" and parts[2] == "vi" and text:       # filters links to match PX-Web db URL structure
            rows.append({"database": text, "database_url": urljoin(BASE, href)})        # (/pxweb/vi/{database_id}) and ensures the link is not empty

    return (
        pd.DataFrame(rows)                              # list -> table
        .drop_duplicates(subset=["database_url"])       # remove repeat links
        .reset_index(drop=True)                         # renumber rows 0,1,2...
    )


def _find_tablelist_url(session, database, database_url, timeout=30):
    """Resolve a database landing page to its table-list URL."""
    response = session.get(database_url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        text = anchor.get_text(" ", strip=True)

        if text != database:                            # skips any link that does not match the db title passed into the func
            continue

        href_decoded = unquote(href)                    # turn %-encoded chars into normal text. Ex:
                                                        # href= "/pxweb/vi/D%C3%A2n%20s%E1%BB%91/?tablelist=true"
        if "tablelist=true" in href_decoded.lower():    # href_decoded= "/pxweb/vi/Dân số/?tablelist=true"
            return urljoin(BASE, href)                  # direct link to table list found

        if href_decoded.rstrip("/").count("/") >= 4:
            candidate = urljoin(BASE, href)
            clean_path = urlparse(candidate).path.rstrip("/")
            if clean_path.count("/") >= 4:
                return urljoin(BASE, clean_path + "/?tablelist=true")

    return None


def discover_tables(database_df, session=None, timeout=30, verbose=True):
    """Discover all .px tables for the supplied database inventory."""
    session = session or requests.Session()
    rows = []

    for _, db in database_df.iterrows():
        tablelist_url = _find_tablelist_url(
            session=session,
            database=db["database"],
            database_url=db["database_url"],
            timeout=timeout,
        )

        if tablelist_url is None:
            if verbose:
                print(f"{db['database']}: SECOND-LEVEL LINK NOT FOUND")
            continue

        response = session.get(tablelist_url, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        count = 0

        for anchor in soup.find_all("a", href=True):
            href = unquote(anchor["href"])
            title = anchor.get_text(" ", strip=True)

            if not title or ".px/" not in href.lower():
                continue

            table_url = urljoin(BASE, anchor["href"])
            filename = urlparse(table_url).path.rstrip("/").split("/")[-1]

            if not filename.lower().endswith(".px"):
                continue

            rows.append(
                {
                    "database": db["database"],
                    "table_id": filename,
                    "table_title": title,
                    "table_url": table_url,
                }
            )
            count += 1

        if verbose:
            print(f"{db['database']}: {count} tables")

    if not rows:
        return pd.DataFrame(columns=["database", "table_id", "table_title", "table_url"])

    return pd.DataFrame(rows).drop_duplicates(subset=["table_url"]).reset_index(drop=True)


def build_catalog(session=None, timeout=30, verbose=True):
    """Discover databases and return the unclassified master table catalog."""
    session = session or requests.Session()
    database_df = discover_databases(session=session, timeout=timeout)

    if verbose:
        print("Databases/navigation entries found:", len(database_df))

    catalog = discover_tables(
        database_df=database_df,
        session=session,
        timeout=timeout,
        verbose=verbose,
    )

    if verbose:
        print("\n" + "=" * 80)
        print("MASTER TABLE INVENTORY")
        print("=" * 80)
        print("Databases:", catalog["database"].nunique() if not catalog.empty else 0)
        print("Tables:", len(catalog))
        if not catalog.empty:
            print("\nTables by database:")
            print(catalog.groupby("database").size().sort_values(ascending=False).to_string())

    return catalog


def check_api_source(database, table_id, session=None, timeout=20):
    """Classify one table as JSON API or HTML form using its API metadata endpoint."""
    session = session or requests.Session()
    url = f"{API_BASE}/{quote(database, safe='')}/{table_id}"

    try:
        response = session.get(url, timeout=timeout)
        if response.status_code != 200:
            return "html_form", response.status_code, None

        data = json.loads(response.content.decode("utf-8-sig"))
        if isinstance(data, dict) and "variables" in data:
            return "json_api", response.status_code, data

        return "html_form", response.status_code, None
    except Exception as exc:
        return "html_form", None, str(exc)


def classify_sources(catalog, session=None, timeout=20, delay=0.05, verbose=True):
    """Add source mechanism and API status to every table in a catalog."""
    session = session or requests.Session()
    results = []

    for i, row in catalog.iterrows():
        source, status_code, _ = check_api_source(
            database=row["database"],
            table_id=row["table_id"],
            session=session,
            timeout=timeout,
        )

        results.append(
            {
                "database": row["database"],
                "table_id": row["table_id"],
                "table_title": row["table_title"],
                "table_url": row["table_url"],
                "source": source,
                "api_status": status_code,
            }
        )

        if verbose and (i + 1) % 25 == 0:
            print(f"Checked {i + 1}/{len(catalog)}")
        if delay:
            time.sleep(delay)

    return pd.DataFrame(results)


def save_catalog(catalog, output_path):
    """Save a classified catalog as UTF-8 BOM CSV and return its Path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def build_classified_catalog(output_path, timeout=30, delay=0.05, verbose=True):
    """Run discovery, apply production scope, classify sources, and save catalog."""
    with requests.Session() as session:
        # Discover the complete table inventory exposed by NSO.
        catalog = build_catalog(
            session=session,
            timeout=timeout,
            verbose=verbose,
        )

        # PL databases are currently outside the production scope.
        # Keep discovery generic, but do not classify, download, profile,
        # or load PL tables into the production pipeline.
        pl_mask = catalog["database"].astype(str).str.startswith(
            "PLV",
            na=False,
        )

        excluded_count = int(pl_mask.sum())

        production_catalog = (
            catalog.loc[~pl_mask]
            .reset_index(drop=True)
        )

        if verbose:
            print("\n" + "=" * 80)
            print("PRODUCTION SCOPE")
            print("=" * 80)
            print(f"Discovered tables : {len(catalog)}")
            print(f"Excluded PL tables: {excluded_count}")
            print(f"Production tables : {len(production_catalog)}")

        source_catalog = classify_sources(
            catalog=production_catalog,
            session=session,
            timeout=min(timeout, 20),
            delay=delay,
            verbose=verbose,
        )

    saved_path = save_catalog(
        source_catalog,
        output_path,
    )

    if verbose:
        print("\n" + "=" * 80)
        print("SOURCE CLASSIFICATION")
        print("=" * 80)
        print(source_catalog["source"].value_counts().to_string())
        print("\nSaved:", saved_path)
        print("Shape:", source_catalog.shape)

    return source_catalog