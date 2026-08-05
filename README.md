# Media Nexus: Media Wishlist

A self-hosted movie and TV request manager with a browser interface, Flask API, CSV/JSON storage, and optional local IMDb validation.

## Run

```bash
cp .env.example .env
docker compose up -d
```

Open `http://localhost:8080` or the port set by `HOST_PORT`.

No request history or IMDb database is included. The application creates request data under `data/`.

## Optional IMDb database

```bash
docker compose --profile maintenance run --rm imdb-update
```

Without it, local IMDb validation reports that the database is not ready. Generated database files and timestamps are stored under `imdb/`.

## Privacy

`data/` and `imdb/` are excluded from Git. Do not commit them; request history may contain names, preferences, identifiers, and timestamps.
