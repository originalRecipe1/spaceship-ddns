import unittest
from unittest.mock import Mock, patch

import requests

import spaceship_ddns


BASE_ENVIRONMENT = {
    "SPACESHIP_DDNS_DOMAIN": "example.com",
    "SPACESHIP_DDNS_API_KEY": "test-key",
    "SPACESHIP_DDNS_API_SECRET": "test-secret",
    "SPACESHIP_DDNS_NAMES": "host-one,host-two",
    "SPACESHIP_DDNS_INTERVAL_SECONDS": "300",
    "SPACESHIP_DDNS_TTL_SECONDS": "300",
}


def config(interval_seconds=300, ttl_seconds=300):
    return spaceship_ddns.Config(
        domain="example.com",
        api_key="test-key",
        api_secret="test-secret",
        names=("host-one", "host-two"),
        interval_seconds=interval_seconds,
        ttl_seconds=ttl_seconds,
    )


class ParseArgsTests(unittest.TestCase):
    def test_parses_and_deduplicates_multiple_names(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["SPACESHIP_DDNS_NAMES"] = "host-one, host-two,host-one"

        parsed = spaceship_ddns.parse_args([], environment)

        self.assertEqual(parsed.names, ("host-one", "host-two"))
        self.assertEqual(parsed.interval_seconds, 300)
        self.assertEqual(parsed.ttl_seconds, 300)

    def test_once_overrides_configured_interval(self):
        parsed = spaceship_ddns.parse_args(["--once"], BASE_ENVIRONMENT)

        self.assertIsNone(parsed.interval_seconds)

    def test_supports_legacy_single_name_environment(self):
        environment = dict(BASE_ENVIRONMENT)
        environment.pop("SPACESHIP_DDNS_NAMES")
        environment["SPACESHIP_DDNS_NAME"] = "legacy"

        parsed = spaceship_ddns.parse_args([], environment)

        self.assertEqual(parsed.names, ("legacy",))

    def test_rejects_interval_below_safety_bound(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["SPACESHIP_DDNS_INTERVAL_SECONDS"] = "59"

        with self.assertRaisesRegex(ValueError, "between 60 and 3600"):
            spaceship_ddns.parse_args([], environment)

    def test_rejects_empty_name_list(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["SPACESHIP_DDNS_NAMES"] = " , "

        with self.assertRaisesRegex(ValueError, "At least one"):
            spaceship_ddns.parse_args([], environment)


class UpdateRecordsTests(unittest.TestCase):
    def test_unchanged_records_do_not_write(self):
        records = [
            {"name": "host-one", "type": "A", "address": "192.0.2.10", "ttl": 300},
            {"name": "host-two", "type": "A", "address": "192.0.2.10", "ttl": 300},
        ]
        with (
            patch("spaceship_ddns.get_public_address", return_value="192.0.2.10"),
            patch("spaceship_ddns.get_dns_entries", return_value=records),
            patch("spaceship_ddns.delete_dns_entry") as delete,
            patch("spaceship_ddns.add_dns_entry") as add,
        ):
            spaceship_ddns.update_records(config())

        delete.assert_not_called()
        add.assert_not_called()

    def test_replaces_stale_record_and_adds_missing_record(self):
        records = [
            {"name": "host-one", "type": "A", "address": "192.0.2.9", "ttl": 300},
        ]
        current_config = config()
        with (
            patch("spaceship_ddns.get_public_address", return_value="192.0.2.10"),
            patch("spaceship_ddns.get_dns_entries", return_value=records),
            patch("spaceship_ddns.delete_dns_entry") as delete,
            patch("spaceship_ddns.add_dns_entry") as add,
        ):
            spaceship_ddns.update_records(current_config)

        delete.assert_called_once_with(
            current_config,
            "host-one",
            "192.0.2.9",
        )
        self.assertEqual(
            add.call_args_list,
            [
                unittest.mock.call(current_config, "host-one", "192.0.2.10"),
                unittest.mock.call(current_config, "host-two", "192.0.2.10"),
            ],
        )

    def test_reconciles_ttl_without_changing_address(self):
        records = [
            {"name": "host-one", "type": "A", "address": "192.0.2.10", "ttl": 1800},
            {"name": "host-two", "type": "A", "address": "192.0.2.10", "ttl": 300},
        ]
        current_config = config()
        with (
            patch("spaceship_ddns.get_public_address", return_value="192.0.2.10"),
            patch("spaceship_ddns.get_dns_entries", return_value=records),
            patch("spaceship_ddns.delete_dns_entry") as delete,
            patch("spaceship_ddns.add_dns_entry") as add,
        ):
            spaceship_ddns.update_records(current_config)

        delete.assert_called_once_with(
            current_config,
            "host-one",
            "192.0.2.10",
        )
        add.assert_called_once_with(
            current_config,
            "host-one",
            "192.0.2.10",
        )

    def test_rejects_invalid_record_address_before_delete(self):
        records = [{"name": "host-one", "type": "A", "address": None}]
        with (
            patch("spaceship_ddns.get_public_address", return_value="192.0.2.10"),
            patch("spaceship_ddns.get_dns_entries", return_value=records),
            patch("spaceship_ddns.delete_dns_entry") as delete,
        ):
            with self.assertRaisesRegex(spaceship_ddns.UpdateError, "invalid A record"):
                spaceship_ddns.update_records(config())

        delete.assert_not_called()


class SchedulingTests(unittest.TestCase):
    def test_continuous_mode_retries_after_network_failure(self):
        sleep = Mock()
        monotonic = Mock(side_effect=[10.0, 11.0, 310.0])
        with patch(
            "spaceship_ddns.update_records",
            side_effect=[requests.Timeout("timed out"), None],
        ) as update:
            spaceship_ddns.run(
                config(),
                sleep=sleep,
                monotonic=monotonic,
                max_runs=2,
            )

        self.assertEqual(update.call_count, 2)
        sleep.assert_called_once_with(299.0)

    def test_one_shot_mode_propagates_failure(self):
        with patch(
            "spaceship_ddns.update_records",
            side_effect=requests.Timeout("timed out"),
        ):
            with self.assertRaises(requests.Timeout):
                spaceship_ddns.run(config(interval_seconds=None))


class PublicAddressTests(unittest.TestCase):
    def test_requires_public_ipv4_and_uses_timeout(self):
        response = Mock()
        response.text = "2001:db8::1"
        with patch("spaceship_ddns.requests.get", return_value=response) as get:
            with self.assertRaisesRegex(
                spaceship_ddns.UpdateError, "did not return IPv4"
            ):
                spaceship_ddns.get_public_address()

        response.raise_for_status.assert_called_once_with()
        get.assert_called_once_with(
            spaceship_ddns.PUBLIC_ADDRESS_ENDPOINT,
            timeout=spaceship_ddns.HTTP_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
