import csv
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.text import P


PAGE_URL = (
    "https://www.ireland.ie/en/india/newdelhi/services/visas/"
    "processing-times-and-decisions/"
)

OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "visa_decisions.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def create_client() -> httpx.Client:
    return httpx.Client(
        headers=HEADERS,
        timeout=60.0,
        follow_redirects=True,
    )


def extract_date_from_url(url: str) -> datetime:
    match = re.search(r"(\d{8})", url)

    if not match:
        return datetime.min

    try:
        return datetime.strptime(
            match.group(1),
            "%Y%m%d",
        )
    except ValueError:
        return datetime.min


def discover_latest_ods_url(
    client: httpx.Client,
) -> str:
    print(f"Opening official page: {PAGE_URL}")

    response = client.get(PAGE_URL)

    print(
        "Official page HTTP status:",
        response.status_code,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    ods_urls = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()

        if not href:
            continue

        absolute_url = urljoin(
            PAGE_URL,
            href,
        )

        clean_url = absolute_url.split("?")[0]

        if not clean_url.lower().endswith(".ods"):
            continue

        combined_text = (
            link.get_text(" ", strip=True)
            + " "
            + clean_url
        ).lower()

        if (
            "visa" in combined_text
            or "decision" in combined_text
            or "ndvo" in combined_text
        ):
            ods_urls.append(absolute_url)

    ods_urls = list(dict.fromkeys(ods_urls))

    if not ods_urls:
        raise RuntimeError(
            "No NDVO visa decision ODS links were found "
            "on the official page."
        )

    ods_urls.sort(
        key=extract_date_from_url,
        reverse=True,
    )

    latest_url = ods_urls[0]

    print(
        f"Found {len(ods_urls)} ODS file(s)."
    )
    print(
        f"Latest ODS selected: {latest_url}"
    )

    return latest_url


def download_ods(
    client: httpx.Client,
    ods_url: str,
    destination: Path,
) -> None:
    print(f"Downloading ODS: {ods_url}")

    response = client.get(
        ods_url,
        headers={
            **HEADERS,
            "Referer": PAGE_URL,
            "Accept": (
                "application/vnd.oasis.opendocument.spreadsheet,"
                "application/octet-stream,*/*;q=0.8"
            ),
        },
    )

    print(
        "ODS HTTP status:",
        response.status_code,
    )

    response.raise_for_status()

    destination.write_bytes(
        response.content
    )

    print(
        f"Downloaded {len(response.content)} bytes."
    )


def get_text_from_cell(
    cell: TableCell,
) -> str:
    parts = []

    for paragraph in cell.getElementsByType(P):
        text_parts = []

        for node in paragraph.childNodes:
            if hasattr(node, "data"):
                text_parts.append(
                    str(node.data)
                )

        text = "".join(text_parts).strip()

        if text:
            parts.append(text)

    return " ".join(parts).strip()


def read_ods_rows(
    ods_path: Path,
) -> list[list[str]]:
    document = load(str(ods_path))

    all_rows = []

    for table in document.spreadsheet.getElementsByType(
        Table
    ):
        for row in table.getElementsByType(
            TableRow
        ):
            values = []

            for cell in row.getElementsByType(
                TableCell
            ):
                repeat = int(
                    cell.getAttribute(
                        "numbercolumnsrepeated"
                    )
                    or 1
                )

                value = get_text_from_cell(cell)

                values.extend(
                    [value] * min(repeat, 100)
                )

            while values and not values[-1]:
                values.pop()

            if any(
                value.strip()
                for value in values
            ):
                all_rows.append(values)

    return all_rows


def normalize_header(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def find_column_indexes(
    rows: list[list[str]],
) -> tuple[int, int, int]:
    for row_index, row in enumerate(rows):
        normalized = [
            normalize_header(value)
            for value in row
        ]

        application_index = None
        decision_index = None

        for index, value in enumerate(normalized):
            if (
                "application" in value
                and (
                    "number" in value
                    or "reference" in value
                    or value == "application"
                )
            ):
                application_index = index

            if (
                "decision" in value
                or "status" in value
                or "outcome" in value
            ):
                decision_index = index

        if (
            application_index is not None
            and decision_index is not None
        ):
            return (
                row_index,
                application_index,
                decision_index,
            )

    raise RuntimeError(
        "Could not identify application-number and "
        "decision columns in the ODS file."
    )


def extract_decisions(
    rows: list[list[str]],
) -> list[tuple[str, str]]:
    (
        header_row_index,
        application_index,
        decision_index,
    ) = find_column_indexes(rows)

    decisions = []
    seen = set()

    for row in rows[header_row_index + 1:]:
        required_index = max(
            application_index,
            decision_index,
        )

        if len(row) <= required_index:
            continue

        application_number = (
            row[application_index].strip()
        )
        raw_status = (
            row[decision_index].strip()
        )

        if not application_number or not raw_status:
            continue

        status_lower = raw_status.lower()

        if "approved" in status_lower:
            status = "Approved"
        elif "refused" in status_lower:
            status = "Refused"
        else:
            continue

        key = (
            application_number.upper(),
            status,
        )

        if key in seen:
            continue

        seen.add(key)

        decisions.append(
            (
                application_number,
                status,
            )
        )

    if not decisions:
        raise RuntimeError(
            "ODS file was downloaded, but no Approved "
            "or Refused visa decisions were extracted."
        )

    return decisions


def save_csv(
    decisions: list[tuple[str, str]],
) -> None:
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow(
            [
                "application_number",
                "status",
            ]
        )

        writer.writerows(decisions)

    print(
        f"Saved {len(decisions)} decisions "
        f"to {OUTPUT_FILE}"
    )


def main() -> None:
    try:
        with create_client() as client:
            latest_ods_url = (
                discover_latest_ods_url(client)
            )

            with tempfile.TemporaryDirectory() as temp_dir:
                ods_path = (
                    Path(temp_dir)
                    / "latest_visa_decisions.ods"
                )

                download_ods(
                    client,
                    latest_ods_url,
                    ods_path,
                )

                rows = read_ods_rows(
                    ods_path
                )

                print(
                    f"Read {len(rows)} non-empty ODS rows."
                )

                decisions = extract_decisions(
                    rows
                )

        print(
            f"Extracted {len(decisions)} visa decisions."
        )

        save_csv(decisions)

        print("Visa data update completed successfully.")

    except Exception as exc:
        print(
            f"ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
