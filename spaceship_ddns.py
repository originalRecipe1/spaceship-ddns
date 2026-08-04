"""Keep one or more Spaceship DNS A records on the current public IPv4."""

import argparse
import datetime
import ipaddress
import os
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

import requests

ENDPOINT = "https://spaceship.dev/api/v1/dns/records"
PUBLIC_ADDRESS_ENDPOINT = "https://api.ipify.org"
HTTP_TIMEOUT_SECONDS = 30
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 86400
DEFAULT_TTL_SECONDS = 300


class UpdateError(RuntimeError):
    """An update failed in a way that is safe to retry."""


@dataclass(frozen=True)
class Config:
    domain: str
    api_key: str
    api_secret: str
    names: tuple[str, ...]
    interval_seconds: int | None
    ttl_seconds: int


def required_value(
    cli_value: str | None,
    environment: Mapping[str, str],
    variable_name: str,
) -> str:
    value = cli_value if cli_value is not None else environment.get(variable_name)
    if value is None or not value.strip():
        raise ValueError(
            f"Please use the CLI argument or set the {variable_name} "
            "environment variable"
        )
    return value


def bounded_integer(
    raw_value: str | int,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def parse_args(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> Config:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-d", "--domain", help="Domain to update")
    parser.add_argument("-k", "--api-key", help="Spaceship API key")
    parser.add_argument("-s", "--api-secret", help="Spaceship API secret")
    parser.add_argument(
        "-N",
        "--name",
        help="Target DNS name(s), comma-separated. Use @ for the domain root.",
    )
    parser.add_argument(
        "--interval-seconds",
        help="Repeat after this many seconds; omit for a single update.",
    )
    parser.add_argument(
        "--ttl-seconds",
        help=f"A-record TTL (default: {DEFAULT_TTL_SECONDS}).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one update even when an interval is configured.",
    )
    args = parser.parse_args(argv)
    environment = os.environ if environment is None else environment

    domain = required_value(args.domain, environment, "SPACESHIP_DDNS_DOMAIN")
    api_key = required_value(args.api_key, environment, "SPACESHIP_DDNS_API_KEY")
    api_secret = required_value(
        args.api_secret,
        environment,
        "SPACESHIP_DDNS_API_SECRET",
    )
    raw_names = args.name
    if raw_names is None:
        raw_names = environment.get("SPACESHIP_DDNS_NAMES")
    if raw_names is None:
        raw_names = environment.get("SPACESHIP_DDNS_NAME")
    if raw_names is None:
        raise ValueError("Please use --name or set SPACESHIP_DDNS_NAMES")
    names = tuple(
        dict.fromkeys(item.strip() for item in raw_names.split(",") if item.strip())
    )
    if not names:
        raise ValueError("At least one DNS name must be configured")

    raw_interval = None if args.once else args.interval_seconds
    if raw_interval is None and not args.once:
        raw_interval = environment.get("SPACESHIP_DDNS_INTERVAL_SECONDS")
    interval_seconds = None
    if raw_interval is not None:
        interval_seconds = bounded_integer(
            raw_interval,
            "Update interval",
            MIN_INTERVAL_SECONDS,
            MAX_INTERVAL_SECONDS,
        )

    raw_ttl = args.ttl_seconds
    if raw_ttl is None:
        raw_ttl = environment.get(
            "SPACESHIP_DDNS_TTL_SECONDS",
            str(DEFAULT_TTL_SECONDS),
        )
    ttl_seconds = bounded_integer(
        raw_ttl,
        "DNS TTL",
        MIN_TTL_SECONDS,
        MAX_TTL_SECONDS,
    )

    return Config(
        domain=domain,
        api_key=api_key,
        api_secret=api_secret,
        names=names,
        interval_seconds=interval_seconds,
        ttl_seconds=ttl_seconds,
    )


def log_response(action: str, response: requests.Response) -> None:
    timestamp = datetime.datetime.now(tz=datetime.UTC).isoformat()
    print(
        f"{timestamp} action={action} status={response.status_code}",
        flush=True,
    )


def get_public_address() -> str:
    response = requests.get(
        PUBLIC_ADDRESS_ENDPOINT,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw_address = response.text.strip()
    try:
        address = ipaddress.ip_address(raw_address)
    except ValueError as exc:
        raise UpdateError("Public-address service returned an invalid IP") from exc
    if address.version != 4:
        raise UpdateError("Public-address service did not return IPv4")
    return str(address)


def get_dns_entries(config: Config) -> list[dict]:
    url = f"{ENDPOINT}/{config.domain}?take=500&skip=0"
    response = requests.get(
        url,
        headers={
            "X-API-Key": config.api_key,
            "X-API-Secret": config.api_secret,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    log_response("list", response)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpdateError("Spaceship returned invalid JSON") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise UpdateError("Spaceship response is missing the DNS record list")
    return items


def delete_dns_entry(config: Config, name: str, address: str) -> None:
    response = requests.delete(
        f"{ENDPOINT}/{config.domain}",
        json=[{"type": "A", "name": name, "address": address}],
        headers={
            "X-API-Key": config.api_key,
            "X-API-Secret": config.api_secret,
            "content-type": "application/json",
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    log_response(f"delete name={name}", response)
    response.raise_for_status()


def add_dns_entry(config: Config, name: str, address: str) -> None:
    response = requests.put(
        f"{ENDPOINT}/{config.domain}",
        json={
            "force": True,
            "items": [
                {
                    "type": "A",
                    "name": name,
                    "address": address,
                    "ttl": config.ttl_seconds,
                }
            ],
        },
        headers={
            "X-API-Key": config.api_key,
            "X-API-Secret": config.api_secret,
            "content-type": "application/json",
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    log_response(f"put name={name}", response)
    response.raise_for_status()


def ttl_matches(record: dict, expected_ttl: int) -> bool:
    if "ttl" not in record:
        return True
    try:
        return int(record["ttl"]) == expected_ttl
    except (TypeError, ValueError):
        return False


def update_records(config: Config) -> None:
    current_address = get_public_address()
    dns_entries = get_dns_entries(config)

    for name in config.names:
        records = [
            entry
            for entry in dns_entries
            if isinstance(entry, dict)
            and entry.get("name") == name
            and entry.get("type") == "A"
        ]
        if (
            len(records) == 1
            and records[0].get("address") == current_address
            and ttl_matches(records[0], config.ttl_seconds)
        ):
            print(
                f"name={name} address={current_address} result=unchanged",
                flush=True,
            )
            continue

        for record in records:
            address = record.get("address")
            if not isinstance(address, str) or not address:
                raise UpdateError(f"Spaceship returned an invalid A record for {name}")
            delete_dns_entry(config, name, address)
        add_dns_entry(config, name, current_address)
        print(
            f"name={name} address={current_address} result=updated",
            flush=True,
        )


def run(
    config: Config,
    *,
    sleep=time.sleep,
    monotonic=time.monotonic,
    max_runs: int | None = None,
) -> None:
    if config.interval_seconds is None:
        update_records(config)
        return

    completed_runs = 0
    while True:
        started = monotonic()
        try:
            update_records(config)
        except (requests.RequestException, UpdateError) as exc:
            print(f"DDNS update failed: {exc}", flush=True)
        completed_runs += 1
        if max_runs is not None and completed_runs >= max_runs:
            return
        elapsed = monotonic() - started
        delay = max(0.0, config.interval_seconds - elapsed)
        print(f"Next update in {delay:.0f} seconds", flush=True)
        sleep(delay)


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
