"""Check that the S8 archive keeps enough evidence to audit incomplete posts."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts.archive_s8_results import (image_source_summary, save_embed_image,
                                        snapshot_message, write_report)


class FakeAttachment:
    id = 456
    filename = "mmr.jpg"
    size = 3

    async def save(self, path):
        Path(path).write_bytes(b"MMR")


class FakeImageResponse:
    status = 200
    headers = {"Content-Type": "image/jpeg"}

    def __init__(self):
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def iter_chunked(self, _):
        yield b"embedded MMR"


class FakeImageSession:
    def get(self, url, allow_redirects):
        assert url == "https://cdn.discordapp.com/attachments/1/mmr.jpg"
        assert allow_redirects is False
        return FakeImageResponse()


class ArchiveTest(unittest.TestCase):
    def test_saved_image_provenance_and_unpaired_result(self):
        field = SimpleNamespace(name="Table ID", value="3390")
        mmr = SimpleNamespace(
            id=123, guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2, name="tier-all-results"),
            jump_url="https://discord.com/channels/1/2/123",
            created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
            author=SimpleNamespace(id=4), content="",
            embeds=[SimpleNamespace(title="Tier ALL MMR", description=None, fields=[field])],
            attachments=[FakeAttachment()],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = asyncio.run(snapshot_message(mmr, root))
            report = write_report([record], root)
            self.assertEqual(record["table_ids"], [3390])
            attachment = record["attachments"][0]
            self.assertEqual(attachment["sha256"], hashlib.sha256(b"MMR").hexdigest())
            self.assertEqual((root / attachment["path"]).read_bytes(), b"MMR")
            self.assertEqual(report["table_ids_needing_review"]["3390"]["result_posts"], 0)
            self.assertEqual(json.loads((root / "audit.json").read_text())["highest_table_id"], 3390)

    def test_missing_image_is_flagged_even_with_matching_posts(self):
        field = SimpleNamespace(name="Table ID", value="3000")
        base = dict(id=123, guild=SimpleNamespace(id=1),
                    channel=SimpleNamespace(id=2, name="results"),
                    jump_url="https://discord.com/channels/1/2/123",
                    created_at=datetime.now(timezone.utc), author=SimpleNamespace(id=4),
                    content="", attachments=[])
        with tempfile.TemporaryDirectory() as temp:
            records = [asyncio.run(snapshot_message(
                SimpleNamespace(**base, embeds=[SimpleNamespace(
                    title=title, description=None, fields=[field])]), Path(temp)))
                for title in ("Tier ALL Results", "Tier ALL MMR")]
            report = write_report(records, Path(temp))
            self.assertEqual(len(report["posts_missing_images"]), 2)

    def test_channel_coverage_finds_tables_absent_from_primary(self):
        def record(channel_id, table_id, kind):
            return {"channel_id": channel_id, "channel_name": f"results-{channel_id}",
                    "table_ids": [table_id], "kind": kind,
                    "jump_url": f"https://discord.com/channels/1/{channel_id}/{table_id}",
                    "attachments": [{"status": "saved"}], "message_id": table_id,
                    "created_at": "2026-09-27T00:00:00+00:00"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = [record(2, 3390, "result"), record(3, 3390, "mmr"),
                       record(3, 3391, "result"), record(3, 3391, "mmr")]
            report = write_report(records, root, [(2, "tier-all-results"),
                                                   (3, "tier-a-results"),
                                                   (4, "tier-sq-results")], 2)
            self.assertEqual(report["table_ids_missing_from_primary_channel"], {"3391": [3]})
            self.assertEqual(report["channels"]["2"]["table_ids_found"], 1)
            self.assertEqual(report["channels"]["3"]["table_ids_found"], 2)
            self.assertEqual(report["channels"]["4"]["message_count"], 0)
            self.assertEqual(report["table_ids_needing_review"], {})
            self.assertEqual(json.loads((root / "audit.json").read_text()), report)

    def test_embed_image_is_saved_when_message_has_no_attachment(self):
        message = SimpleNamespace(
            id=123, guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2, name="tier-all-results"),
            jump_url="https://discord.com/channels/1/2/123",
            created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
            author=SimpleNamespace(id=4), content="", attachments=[],
            embeds=[SimpleNamespace(
                title="Tier ALL MMR", description=None,
                fields=[SimpleNamespace(name="Table ID", value="3390")],
                image=SimpleNamespace(url="https://cdn.discordapp.com/attachments/1/mmr.jpg"))],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = asyncio.run(snapshot_message(message, root, FakeImageSession()))
            report = write_report([record], root)
            image = record["embed_images"][0]
            self.assertEqual(image["status"], "saved")
            self.assertEqual(image["sha256"], hashlib.sha256(b"embedded MMR").hexdigest())
            self.assertEqual((root / image["path"]).read_bytes(), b"embedded MMR")
            self.assertEqual(report["posts_missing_images"], [])
            self.assertIn(image["path"], (root / "index.csv").read_text())

    def test_external_embed_url_is_not_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            image = asyncio.run(save_embed_image(
                "https://other.example.com/private.jpg",
                SimpleNamespace(channel=SimpleNamespace(id=2), id=123),
                0, Path(temp), FakeImageSession()))
            self.assertEqual(image["status"], "unsupported_url")
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_probe_reports_hosts_without_exposing_urls(self):
        message = SimpleNamespace(id=3390, attachments=[], embeds=[SimpleNamespace(
            image=SimpleNamespace(url="https://cdn.discordapp.com/attachments/id/mmr.jpg?secret=123"))])
        self.assertEqual(image_source_summary(message), {
            "message_id": 3390, "attachment_count": 0,
            "embed_image_hosts": ["cdn.discordapp.com"],
        })


if __name__ == "__main__":
    unittest.main()
