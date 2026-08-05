import argparse
import csv
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

IMDB_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
IMDB_DIR = Path(os.environ.get("IMDB_DIR", "/imdb"))
DATABASE_PATH = IMDB_DIR / "imdb_titles.sqlite"
METADATA_PATH = IMDB_DIR / "last_update.txt"

# Keep only content that maps cleanly to the two choices used by the site.
TYPE_MAP = {
    "movie": "Movie",
    "tvMovie": "Movie",
    "tvSeries": "TV Series",
    "tvMiniSeries": "TV Series",
}


def database_is_stale(max_age_days: int) -> bool:
    if not DATABASE_PATH.exists() or not METADATA_PATH.exists():
        return True

    try:
        updated_at = float(METADATA_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True

    age_seconds = time.time() - updated_at
    return age_seconds >= max_age_days * 86400


def download_dataset(destination: Path) -> None:
    request = urllib.request.Request(
        IMDB_URL,
        headers={"User-Agent": "SelfHosted-Media-Requests/1.0"},
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        total = response.headers.get("Content-Length")
        downloaded = 0
        next_report = 25 * 1024 * 1024

        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break

                output.write(chunk)
                downloaded += len(chunk)

                if downloaded >= next_report:
                    if total:
                        percent = downloaded / int(total) * 100
                        print(
                            f"Downloaded {downloaded / 1024 / 1024:.0f} MB "
                            f"({percent:.0f}%)",
                            flush=True,
                        )
                    else:
                        print(
                            f"Downloaded {downloaded / 1024 / 1024:.0f} MB",
                            flush=True,
                        )
                    next_report += 25 * 1024 * 1024


def build_database(gzip_path: Path, temporary_database: Path) -> int:
    connection = sqlite3.connect(temporary_database)

    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")

        connection.execute(
            """
            CREATE TABLE titles (
                imdb_id TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                content_name TEXT NOT NULL,
                year INTEGER NOT NULL
            )
            """
        )

        insert_sql = """
            INSERT OR REPLACE INTO titles
                (imdb_id, media_type, content_name, year)
            VALUES (?, ?, ?, ?)
        """

        batch = []
        inserted = 0

        with gzip.open(gzip_path, "rt", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")

            for row in reader:
                mapped_type = TYPE_MAP.get(row.get("titleType", ""))
                if not mapped_type:
                    continue

                # Exclude adult titles.
                if row.get("isAdult") == "1":
                    continue

                year_text = row.get("startYear", r"\N")
                title = row.get("primaryTitle", "").strip()
                imdb_id = row.get("tconst", "").strip()

                if not imdb_id or not title or year_text == r"\N":
                    continue

                try:
                    year = int(year_text)
                except ValueError:
                    continue

                batch.append((imdb_id, mapped_type, title, year))

                if len(batch) >= 10000:
                    connection.executemany(insert_sql, batch)
                    inserted += len(batch)
                    batch.clear()

                    if inserted % 100000 == 0:
                        print(f"Indexed {inserted:,} titles", flush=True)

        if batch:
            connection.executemany(insert_sql, batch)
            inserted += len(batch)

        connection.execute(
            "CREATE INDEX idx_titles_name ON titles(content_name)"
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.commit()

        return inserted
    finally:
        connection.close()


def update_database() -> None:
    IMDB_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=IMDB_DIR) as temporary_directory:
        temporary_directory = Path(temporary_directory)
        gzip_path = temporary_directory / "title.basics.tsv.gz"
        temporary_database = temporary_directory / "imdb_titles.sqlite"

        print("Downloading IMDb title basics dataset...", flush=True)
        download_dataset(gzip_path)

        print("Building trimmed SQLite database...", flush=True)
        count = build_database(gzip_path, temporary_database)

        os.replace(temporary_database, DATABASE_PATH)
        METADATA_PATH.write_text(str(time.time()), encoding="utf-8")

        print(
            f"IMDb database ready with {count:,} movie and TV records.",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-stale",
        type=int,
        metavar="DAYS",
        help="Update only when the local database is missing or older than DAYS.",
    )
    args = parser.parse_args()

    if args.if_stale is not None and not database_is_stale(args.if_stale):
        print("IMDb database is current; no update needed.", flush=True)
        return 0

    try:
        update_database()
        return 0
    except Exception as exc:
        print(f"IMDb database update failed: {exc}", file=sys.stderr, flush=True)
        # Allow the container to keep using an existing database.
        return 1 if not DATABASE_PATH.exists() else 0


if __name__ == "__main__":
    raise SystemExit(main())
