# Spaceship DDNS

A small Python service that keeps one or more Spaceship DNS A records pointed
at the machine's current public IPv4 address.

The updater runs immediately at startup. It can then repeat at a bounded
interval, making it suitable for connections whose public address changes
without notice. Spaceship zones with more than 500 records are not supported.

## Configuration

Copy the example without committing the resulting file:

```sh
cp .env.example .env
chmod 0600 .env
```

Configure these values:

```dotenv
SPACESHIP_DDNS_DOMAIN=example.com
SPACESHIP_DDNS_API_KEY=replace-with-your-api-key
SPACESHIP_DDNS_API_SECRET=replace-with-your-api-secret
SPACESHIP_DDNS_NAMES=host-one,host-two
SPACESHIP_DDNS_INTERVAL_SECONDS=300
SPACESHIP_DDNS_TTL_SECONDS=300
```

Record names are relative to the domain. Names are deduplicated while
preserving their configured order. The interval must be between 60 and 3,600
seconds; the TTL must be between 60 and 86,400 seconds. Omitting the interval
runs exactly once.

The same settings can be supplied through command-line arguments; see:

```sh
python3 spaceship_ddns.py --help
```

Passing credentials on the command line may expose them to local process
inspection, so environment configuration is preferred.

## Run with Docker Compose

```sh
docker compose up -d --build
docker compose logs --tail=20 spaceship_ddns
```

The container is read-only, capability-free, and non-root. The Dockerfile
copies only the script and dependency manifest, while `.dockerignore` excludes
runtime environment files. The API credential is therefore supplied only at
container creation and is not embedded in the image.

Run a one-shot reconciliation with the configured environment:

```sh
docker compose run --rm spaceship_ddns --once
```

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
docker build -t spaceship-ddns:test .
```

## References

- [Spaceship DNS records API](https://docs.spaceship.dev/#tag/DNS-records/operation/saveRecords)
