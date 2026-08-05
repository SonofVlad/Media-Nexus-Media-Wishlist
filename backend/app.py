import csv
import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, jsonify, request

app = Flask(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
IMDB_DIR = Path(os.environ.get("IMDB_DIR", "/imdb"))
JSON_PATH = DATA_DIR / "requests.json"
CSV_PATH = DATA_DIR / "requests.csv"
IMDB_DATABASE = IMDB_DIR / "imdb_titles.sqlite"

IMDB_PATTERN = re.compile(
    r"^https?://(?:www\.)?imdb\.com/title/(tt\d{7,10})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
WRITE_LOCK = threading.Lock()

CSV_FIELDS = [
    "id", "requester", "mediaType", "contentName", "year",
    "imdbUrl", "imdbId", "acquired", "processed",
    "submittedAt", "updatedAt",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def parse_imdb_url(value: Any):
    imdb_url = clean_text(value)
    match = IMDB_PATTERN.fullmatch(imdb_url)
    if not match:
        return None, imdb_url
    return match.group(1).lower(), imdb_url


def lookup_imdb(imdb_id: str):
    if not IMDB_DATABASE.exists():
        raise RuntimeError(
            "The local IMDb database is not ready yet. "
            "The first download and index build may still be running."
        )

    connection = sqlite3.connect(IMDB_DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        row = connection.execute(
            """
            SELECT imdb_id, media_type, content_name, year
            FROM titles
            WHERE imdb_id = ?
            """,
            (imdb_id,),
        ).fetchone()
    finally:
        connection.close()

    return dict(row) if row else None


def load_requests():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not JSON_PATH.exists():
        return []

    with JSON_PATH.open("r", encoding="utf-8") as handle:
        records = json.load(handle)

    if not isinstance(records, list):
        raise RuntimeError("requests.json must contain a JSON array.")

    return records


def atomic_write_json(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix="requests-", suffix=".json", dir=str(DATA_DIR)
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, JSON_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_csv(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix="requests-", suffix=".csv", dir=str(DATA_DIR)
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {field: record.get(field, "") for field in CSV_FIELDS}
                )
        os.replace(temporary_name, CSV_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def save_requests(records):
    atomic_write_json(records)
    write_csv(records)


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "imdbDatabaseReady": IMDB_DATABASE.exists(),
    })


@app.post("/lookup")
def lookup():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON request body is required."}), 400

    imdb_id, imdb_url = parse_imdb_url(payload.get("imdbUrl"))
    if not imdb_id:
        return jsonify({
            "error": (
                "IMDb URL must look like "
                "https://www.imdb.com/title/tt1981677/."
            )
        }), 400

    try:
        result = lookup_imdb(imdb_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    if not result:
        return jsonify({
            "error": (
                "That IMDb title was not found in the local movie/TV database. "
                "It may be a short, episode, game, podcast, adult title, or a "
                "very new record not included in the latest download."
            )
        }), 404

    return jsonify({
        "imdbId": result["imdb_id"],
        "imdbUrl": imdb_url,
        "mediaType": result["media_type"],
        "contentName": result["content_name"],
        "year": result["year"],
    })


@app.get("/requests")
def list_requests():
    try:
        return jsonify(load_requests())
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/requests")
def create_request():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON request body is required."}), 400

    requester = clean_text(payload.get("requester"))
    imdb_id, imdb_url = parse_imdb_url(payload.get("imdbUrl"))

    if not requester:
        return jsonify({"error": "Requester is required."}), 400

    if not imdb_id:
        return jsonify({"error": "A valid IMDb title URL is required."}), 400

    try:
        metadata = lookup_imdb(imdb_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    if not metadata:
        return jsonify({
            "error": "That IMDb title was not found in the local database."
        }), 404

    with WRITE_LOCK:
        try:
            records = load_requests()
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 500

        duplicate = next(
            (
                record for record in records
                if str(record.get("imdbId", "")).lower() == imdb_id
                and not bool(record.get("processed"))
            ),
            None,
        )

        if duplicate:
            return jsonify({
                "error": "That IMDb title already has an active request.",
                "existing": duplicate,
            }), 409

        now = utc_now()
        record = {
            "id": f"REQ-{uuid4().hex[:8].upper()}",
            "requester": requester,
            "mediaType": metadata["media_type"],
            "contentName": metadata["content_name"],
            "year": metadata["year"],
            "imdbUrl": imdb_url,
            "imdbId": imdb_id,
            "acquired": False,
            "processed": False,
            "submittedAt": now,
            "updatedAt": now,
        }

        records.append(record)

        try:
            save_requests(records)
        except OSError as exc:
            return jsonify({"error": f"Unable to save request: {exc}"}), 500

    return jsonify(record), 201


@app.patch("/requests/<request_id>")
def update_request(request_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON request body is required."}), 400

    allowed = {"acquired", "processed", "requester"}
    if not payload or not set(payload).issubset(allowed):
        return jsonify({
            "error": "Only acquired, processed, and requester may be updated."
        }), 400

    if "acquired" in payload and not isinstance(payload["acquired"], bool):
        return jsonify({"error": "acquired must be true or false."}), 400

    if "processed" in payload and not isinstance(payload["processed"], bool):
        return jsonify({"error": "processed must be true or false."}), 400

    if "requester" in payload:
        payload["requester"] = clean_text(payload["requester"])
        if not payload["requester"]:
            return jsonify({"error": "Requester cannot be blank."}), 400
        if len(payload["requester"]) > 100:
            return jsonify({"error": "Requester must be 100 characters or fewer."}), 400

    with WRITE_LOCK:
        try:
            records = load_requests()
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 500

        record = next(
            (item for item in records if item.get("id") == request_id),
            None,
        )
        if record is None:
            return jsonify({"error": "Request not found."}), 404

        record.update(payload)
        record["updatedAt"] = utc_now()

        try:
            save_requests(records)
        except OSError as exc:
            return jsonify({"error": f"Unable to save request: {exc}"}), 500

    return jsonify(record)


@app.post("/requests/batch-requester")
def batch_update_requester():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON request body is required."}), 400

    request_ids = payload.get("requestIds")
    requester = clean_text(payload.get("requester"))

    if not isinstance(request_ids, list) or not request_ids:
        return jsonify({"error": "Select at least one request."}), 400

    request_ids = [str(item) for item in request_ids]
    if len(set(request_ids)) != len(request_ids):
        return jsonify({"error": "Duplicate request IDs are not allowed."}), 400

    if not requester:
        return jsonify({"error": "Requester cannot be blank."}), 400
    if len(requester) > 100:
        return jsonify({"error": "Requester must be 100 characters or fewer."}), 400

    with WRITE_LOCK:
        try:
            records = load_requests()
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 500

        records_by_id = {str(item.get("id")): item for item in records}
        missing = [item for item in request_ids if item not in records_by_id]
        if missing:
            return jsonify({"error": "One or more selected requests no longer exist."}), 404

        now = utc_now()
        updated = []
        for request_id in request_ids:
            record = records_by_id[request_id]
            record["requester"] = requester
            record["updatedAt"] = now
            updated.append(record)

        try:
            save_requests(records)
        except OSError as exc:
            return jsonify({"error": f"Unable to save requests: {exc}"}), 500

    return jsonify({"updated": updated})


@app.post("/requests/batch-delete")
def batch_delete_requests():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON request body is required."}), 400

    confirmations = payload.get("confirmations")
    if not isinstance(confirmations, list) or not confirmations:
        return jsonify({"error": "Select at least one request."}), 400

    confirmation_by_id = {}
    for item in confirmations:
        if not isinstance(item, dict):
            return jsonify({"error": "Invalid deletion confirmation."}), 400

        request_id = str(item.get("id", ""))
        requester_confirmation = clean_text(item.get("requesterConfirmation"))

        if not request_id or not requester_confirmation:
            return jsonify({
                "error": "Every selected request requires its requester name."
            }), 400

        if request_id in confirmation_by_id:
            return jsonify({"error": "Duplicate request IDs are not allowed."}), 400

        confirmation_by_id[request_id] = requester_confirmation

    with WRITE_LOCK:
        try:
            records = load_requests()
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 500

        records_by_id = {str(item.get("id")): item for item in records}
        missing = [item for item in confirmation_by_id if item not in records_by_id]
        if missing:
            return jsonify({"error": "One or more selected requests no longer exist."}), 404

        failures = []
        for request_id, entered_name in confirmation_by_id.items():
            expected_name = clean_text(records_by_id[request_id].get("requester"))
            if entered_name.casefold() != expected_name.casefold():
                failures.append({
                    "id": request_id,
                    "contentName": records_by_id[request_id].get("contentName"),
                })

        if failures:
            return jsonify({
                "error": "One or more requester names did not match.",
                "failures": failures,
            }), 403

        deleted_ids = set(confirmation_by_id)
        remaining = [
            record for record in records
            if str(record.get("id")) not in deleted_ids
        ]

        try:
            save_requests(remaining)
        except OSError as exc:
            return jsonify({"error": f"Unable to delete requests: {exc}"}), 500

    return jsonify({
        "deletedIds": list(deleted_ids),
        "deletedCount": len(deleted_ids),
    })


DATA_DIR.mkdir(parents=True, exist_ok=True)
if not JSON_PATH.exists():
    save_requests([])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "50012")))
