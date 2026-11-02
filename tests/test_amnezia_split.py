import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "amnezia_split.py"
sys.path.insert(0, str(SCRIPT.parent))
import amnezia_split  # noqa: E402


def run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class AmneziaSplitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        path = self.dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_official_format(self):
        self.write("in.txt", "# header\n1.2.3.0/24\n10.0.0.0/8\n")
        result = run(["in.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data,
            [
                {"hostname": "1.2.3.0/24", "ip": ""},
                {"hostname": "10.0.0.0/8", "ip": ""},
            ],
        )

    def test_legacy_format_keeps_cidr_in_ip_field(self):
        self.write("in.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        result = run(["in.txt", "-o", "out.json", "--legacy", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual([e["ip"] for e in data], ["1.2.3.0/24", "10.0.0.0/8"])
        self.assertEqual(len({e["hostname"] for e in data}), 2)
        for entry in data:
            self.assertNotIn("/", entry["hostname"])

    def test_duplicates_removed_and_invalid_skipped(self):
        self.write(
            "in.txt",
            "1.2.3.0/24\n1.2.3.0/24\n2001:db8::/32\nnot-an-ip\n\n",
        )
        result = run(["in.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertIn("skipped 2 invalid line(s)", result.stderr)

    def test_collapse_merges_adjacent_networks(self):
        self.write("in.txt", "10.0.0.0/9\n10.128.0.0/9\n")
        result = run(["in.txt", "-o", "out.json", "--collapse", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(data, [{"hostname": "10.0.0.0/8", "ip": ""}])

    def test_single_ip_becomes_host_route(self):
        self.write("in.txt", "5.6.7.8\n")
        result = run(["in.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(data[0]["hostname"], "5.6.7.8/32")

    def test_allowedips_output(self):
        self.write("in.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        result = run(
            ["in.txt", "-o", "out.json", "--allowedips", "allowed.txt", "-q"],
            self.dir,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.dir / "allowed.txt").read_text(encoding="utf-8").strip(),
            "1.2.3.0/24, 10.0.0.0/8",
        )

    def test_empty_input_fails(self):
        self.write("in.txt", "# only comments\n")
        result = run(["in.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 2)

    def test_missing_input_fails(self):
        result = run(["nope.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 3)

    def test_quiet_prints_only_path(self):
        self.write("in.txt", "1.2.3.0/24\n")
        result = run(["in.txt", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.stdout.strip(), "out.json")

    def test_default_output_has_timestamp(self):
        self.write("in.txt", "1.2.3.0/24\n")
        result = run(["in.txt", "-q"], self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        name = result.stdout.strip()
        self.assertRegex(name, r"^ip-list-\d{8}-\d{6}\.json$")
        self.assertTrue((self.dir / name).is_file())

    def test_version(self):
        result = run(["--version"], self.dir)
        self.assertEqual(result.returncode, 0)
        self.assertIn("amnezia_split", result.stdout)

    # ------------------------------------------------------------------ #
    # automatic download
    # ------------------------------------------------------------------ #

    def source_url(self, name="remote.txt"):
        return (self.dir / name).as_uri()

    def fetch_args(self, *extra):
        return ["--min-networks", "1", "--cache-dir", "cache", "-o", "out.json", "-q", *extra]

    def test_fetch_from_url(self):
        self.write("remote.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        result = run(self.fetch_args("--source", self.source_url()), self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)

    def test_fetch_writes_metadata(self):
        self.write("remote.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        result = run(self.fetch_args("--source", self.source_url()), self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = json.loads((self.dir / "out.json.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["networks"], 2)
        self.assertFalse(meta["from_cache"])
        self.assertIn("sha256", meta)

    def test_fetch_uses_cache_when_source_disappears(self):
        remote = self.write("remote.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        args = self.fetch_args("--source", self.source_url())
        first = run(args, self.dir)
        self.assertEqual(first.returncode, 0, first.stderr)

        remote.unlink()
        second = run(args, self.dir)
        self.assertEqual(second.returncode, 0, second.stderr)
        meta = json.loads((self.dir / "out.json.meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["from_cache"])

    def test_fetch_offline_without_cache_fails(self):
        result = run(
            ["--offline", "--cache-dir", "empty-cache", "-o", "out.json", "-q"],
            self.dir,
        )
        self.assertEqual(result.returncode, 4)

    def test_fetch_falls_back_to_next_source(self):
        self.write("remote.txt", "1.2.3.0/24\n10.0.0.0/8\n")
        result = run(
            self.fetch_args(
                "--source",
                (self.dir / "missing.txt").as_uri(),
                "--source",
                self.source_url(),
            ),
            self.dir,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)

    def test_fetch_rejects_broken_source(self):
        self.write("remote.txt", "1.2.3.0/24\n")
        result = run(
            ["--source", self.source_url(), "--cache-dir", "cache", "-o", "out.json", "-q"],
            self.dir,
        )
        self.assertEqual(result.returncode, 4)
        self.assertFalse((self.dir / "out.json").exists())

    def test_offline_and_refresh_conflict(self):
        result = run(["--offline", "--refresh", "-o", "out.json", "-q"], self.dir)
        self.assertEqual(result.returncode, 3)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def test_normalize_download_converts_ripe_json(self):
        payload = json.dumps(
            {"data": {"resources": {"ipv4": ["1.2.3.0/24", "10.0.0.0/8"]}}}
        )
        self.assertEqual(
            amnezia_split.normalize_download(payload),
            "1.2.3.0/24\n10.0.0.0/8\n",
        )

    def test_normalize_download_keeps_plain_text(self):
        text = "1.2.3.0/24\n10.0.0.0/8\n"
        self.assertEqual(amnezia_split.normalize_download(text), text)

    def test_normalize_download_keeps_unrelated_json(self):
        text = '{"foo": "bar"}'
        self.assertEqual(amnezia_split.normalize_download(text), text)

    def test_coverage_gap(self):
        import ipaddress

        wanted = [ipaddress.ip_network("10.0.0.0/8")]
        covered = [ipaddress.ip_network("10.0.0.0/9")]
        gap = amnezia_split.coverage_gap(wanted, covered)
        self.assertEqual([str(n) for n in gap], ["10.128.0.0/9"])

    def test_coverage_gap_full_cover(self):
        import ipaddress

        wanted = [ipaddress.ip_network("10.0.0.0/8")]
        covered = [ipaddress.ip_network("10.0.0.0/8")]
        self.assertEqual(amnezia_split.coverage_gap(wanted, covered), [])


if __name__ == "__main__":
    unittest.main()
