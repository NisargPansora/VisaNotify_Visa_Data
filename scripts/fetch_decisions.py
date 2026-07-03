import csv
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.irishimmigration.ie/visa-decisions/"

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
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def download_page() -> str:
    print(f"Downloading: {SOURCE_URL}")

    with httpx.Client(
        headers=HEADERS,
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        response = client.get(SOURCE_URL)
        response.raise_for_status()

    print(f"HTTP status: {response.status_code}")
    return response.text


def extract_decisions(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    decisions: list[tuple[str, str]] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        if not rows:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = [
            cell.get_text(" ", strip=True).lower()
            for cell in header_cells
        ]

        has_application = any(
            "application" in header for header in headers
        )
        has_decision = any(
            "decision" in header for header in headers
        )

        if not (has_application and has_decision):
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])

            if len(cells) < 2:
                continue

            application_number = cells[0].get_text(
                " ", strip=True
            )
            decision = cells[1].get_text(
                " ", strip=True
            )

            normalized_decision = decision.lower()

            if normalized_decision not in {
                "approved",
                "refused",
            }:
                continue

            if not application_number:
                continue

            decisions.append(
                (
                    application_number,
                    normalized_decision.capitalize(),
                )
            )

        if decisions:
            break

    return decisions


def save_csv(decisions: list[tuple[str, str]]) -> None:
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
        html = download_page()
        decisions = extract_decisions(html)

        if not decisions:
            raise RuntimeError(
                "No visa decisions found. "
                "The source page structure may have changed."
            )

        print(
            f"Found {len(decisions)} visa decisions."
        )

        save_csv(decisions)

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
