# Self-Hosted Media Requests

A small self-hosted web application for collecting and managing movie and TV requests. It includes a static browser interface, a Flask API, CSV/JSON persistence, and optional local IMDb title validation.

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

Open `http://localhost:8080`, or the port configured with `HOST_PORT`.

The repository intentionally contains no request history or IMDb database. The application creates request data under `data/` as it is used.

## Build the optional IMDb database

The application can run without the database, but IMDb lookups will report that the local database is not ready. Build a fresh database from IMDb's public datasets with:

```bash
docker compose --profile maintenance run --rm imdb-update
```

The generated database and update timestamp are stored under `imdb/` and excluded from Git.

## Privacy

Do not commit files generated under `data/` or `imdb/`. Request history can contain names, viewing preferences, request identifiers, and timestamps.
