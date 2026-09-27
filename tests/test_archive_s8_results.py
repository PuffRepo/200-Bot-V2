"""Check that the S8 archive keeps enough evidence to audit incomplete posts."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts.archive_s8_results import snapshot_message, write_report


class FakeAttachment:
    id = 456
    filename = "mmr.jpg"
    size = 3

    async def save(self, path):
        Path(path).write_bytes(b"MMR")


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


if __name__ == "__main__":
    unittest.main()
