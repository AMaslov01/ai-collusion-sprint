"""Prepare canonical tool CSVs in non-root Docker without changing upstream."""

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


DRIVE_FILE_ID = "1dNtxHFv7k0PeMHI0smZk8t-dBlWLA0Gz"
DRIVE_FOLDER = "1fHBg6Ro4bSbTBgxeQZ6piYNtmqy8a_kK"
DRIVE_OBSERVED_BYTES = 304807007
FLIGHT_COLUMNS = {
    "Flight Number", "Price", "DepTime", "ArrTime", "ActualElapsedTime",
    "FlightDate", "OriginCityName", "DestCityName", "Distance",
}


class DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.inside = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "download-form":
            self.inside = True
            self.action = attrs.get("action")
        if self.inside and tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False


def download_flights(destination: Path):
    url = "https://drive.usercontent.google.com/download?" + urlencode({
        "id": DRIVE_FILE_ID, "export": "download", "confirm": "t",
    })
    response = urlopen(url, timeout=900)
    if response.headers.get_content_type() == "text/html":
        form = DownloadForm()
        with response:
            form.feed(response.read().decode("utf-8"))
        parsed = urlparse(form.action or "")
        if parsed.scheme != "https" or parsed.netloc != "drive.usercontent.google.com" or parsed.path != "/download":
            raise RuntimeError("Unexpected Google Drive download form; user input required")
        if form.fields.get("id") != DRIVE_FILE_ID:
            raise RuntimeError("Google Drive file identity changed")
        response = urlopen(form.action + "?" + urlencode(form.fields), timeout=900)
    with response:
        if response.headers.get_content_type() == "text/html":
            raise RuntimeError("Google Drive returned HTML, not the flight CSV")
        part = destination.with_suffix(".csv.part")
        with part.open("xb") as output:
            shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    if part.stat().st_size != DRIVE_OBSERVED_BYTES:
        raise RuntimeError("Flight file size differs from the observed source metadata")
    with part.open(newline="", encoding="utf-8-sig") as source:
        columns = set(next(csv.reader(source)))
    if not FLIGHT_COLUMNS <= columns:
        raise RuntimeError("Downloaded flight CSV has unexpected columns")
    os.replace(part, destination)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--adapter-commit", required=True)
    args = parser.parse_args()
    if os.getuid() == 0:
        raise RuntimeError("Must run in non-root Docker")
    source_db = args.upstream / "env/env_systems/travel_planner_env/database"
    if args.destination.exists():
        raise RuntimeError("Destination already exists; inspect saved inputs before reuse")
    for source in source_db.rglob("*"):
        if source.is_symlink():
            raise RuntimeError("Unexpected symlink in canonical database")
    shutil.copytree(source_db, args.destination)
    flights = args.destination / "flights/clean_Flights_2022.csv"
    flights.parent.mkdir(exist_ok=True)
    if flights.exists():
        raise RuntimeError("Upstream unexpectedly contains the external flight CSV")
    download_flights(flights)
    files = []
    for path in sorted(args.destination.rglob("*")):
        if path.is_file():
            digest = sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            files.append({"path": str(path.relative_to(args.destination)), "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    identity = {
        "adapter_commit": args.adapter_commit,
        "upstream_commit": "6cd9de14b71915e39ac742a20dc33785e14b6aab",
        "flight_source": {
            "folder": f"https://drive.google.com/drive/folders/{DRIVE_FOLDER}",
            "file_id": DRIVE_FILE_ID, "source_observed_bytes": DRIVE_OBSERVED_BYTES,
            "checksum_basis": "SHA-256 computed after download; no published SHA-256 supplied",
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(), "files": files,
    }
    temporary = args.identity.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(identity, output, indent=2, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, args.identity)
    print(f"Prepared {len(files)} canonical database files", flush=True)


if __name__ == "__main__":
    main()
