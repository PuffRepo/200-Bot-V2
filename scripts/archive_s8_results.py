"""Archive result posts and images locally using an authorized server bot.

This reads Discord history; it does not post messages or change roles/database rows.
Run ``python -m scripts.archive_s8_results --help`` for the required arguments.
"""

import argparse
import asyncio
import csv
import getpass
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
IMAGE_MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png",
                       "image/webp": ".webp", "image/gif": ".gif",
                       "image/avif": ".avif"}
DISCORD_IMAGE_HOSTS = frozenset({"cdn.discordapp.com", "cdn.discordapp.net",
                                 "media.discordapp.net"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
TABLE_ID = re.compile(r"^\s*(\d+)\s*$")


def parse_day(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a UTC date in YYYY-MM-DD format") from error


def classify(message) -> tuple[list[int], str, list[dict]]:
    ids = set()
    titles = []
    embeds = []
    for embed in message.embeds:
        titles.append((embed.title or "").casefold())
        fields = [{"name": field.name, "value": field.value} for field in embed.fields]
        for field in fields:
            if field["name"].strip().casefold() == "table id":
                match = TABLE_ID.fullmatch(field["value"])
                if match:
                    ids.add(int(match.group(1)))
        embeds.append({"title": embed.title, "description": embed.description, "fields": fields})
    kind = "mmr" if any("mmr" in title for title in titles) else (
        "result" if any("result" in title for title in titles) else "other"
    )
    return sorted(ids), kind, embeds


def image_source_summary(message) -> dict:
    return {"message_id": message.id, "attachment_count": len(message.attachments),
            "embed_image_hosts": [urlsplit(url).hostname
                                  for embed in message.embeds
                                  if isinstance((url := getattr(getattr(embed, "image", None),
                                                               "url", None)), str) and url]}


async def save_embed_image(url: str, message, index: int, directory: Path, session) -> dict:
    parts = urlsplit(url)
    item = {"embed_index": index, "url_host": parts.hostname, "status": "unsupported_url"}
    if parts.scheme != "https" or parts.hostname not in DISCORD_IMAGE_HOSTS:
        return item
    partial = None
    try:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                item["status"] = f"download_failed:HTTP{response.status}"
                return item
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            suffix = IMAGE_MIME_SUFFIXES.get(mime)
            if suffix is None:
                item["status"] = f"unsupported_content_type:{mime or 'missing'}"
                return item
            relative = (Path("images") / str(message.channel.id) /
                        f"{message.id}_embed{index}{suffix}")
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            hasher = hashlib.sha256()
            size = 0
            with partial.open("wb") as output:
                async for block in response.content.iter_chunked(1024 * 1024):
                    size += len(block)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("Image exceeds 20 MiB limit")
                    output.write(block)
                    hasher.update(block)
            os.replace(partial, target)
            item.update({"path": relative.as_posix(), "sha256": hasher.hexdigest(),
                         "size": size, "status": "saved"})
    except Exception as error:
        item["status"] = f"download_failed:{type(error).__name__}"
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return item


async def snapshot_message(message, directory: Path, session=None) -> dict:
    table_ids, kind, embeds = classify(message)
    record = {
        "guild_id": message.guild.id,
        "channel_id": message.channel.id,
        "channel_name": message.channel.name,
        "message_id": message.id,
        "jump_url": message.jump_url,
        "created_at": message.created_at.isoformat(),
        "author_id": message.author.id,
        "content": message.content,
        "table_ids": table_ids,
        "kind": kind,
        "embeds": embeds,
        "attachments": [],
        "embed_images": [],
    }
    for attachment in message.attachments:
        suffix = Path(attachment.filename).suffix.lower()
        item = {"id": attachment.id, "filename": attachment.filename,
                "size": attachment.size, "status": "skipped_non_image"}
        if suffix in IMAGE_SUFFIXES:
            relative = Path("images") / str(message.channel.id) / f"{message.id}_{attachment.id}{suffix}"
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            try:
                await attachment.save(partial)
                hasher = hashlib.sha256()
                with partial.open("rb") as image:
                    for block in iter(lambda: image.read(1024 * 1024), b""):
                        hasher.update(block)
                os.replace(partial, target)
                item.update({"path": relative.as_posix(), "sha256": hasher.hexdigest(),
                             "status": "saved"})
            except Exception as error:
                partial.unlink(missing_ok=True)
                item["status"] = f"download_failed:{type(error).__name__}"
        record["attachments"].append(item)
    if table_ids and kind in {"mmr", "result"} and not any(
        item["status"] == "saved" for item in record["attachments"]
    ):
        for index, embed in enumerate(message.embeds):
            url = getattr(getattr(embed, "image", None), "url", None)
            if isinstance(url, str) and url:
                if session is None:
                    raise ValueError("An image download session is required for embedded images")
                record["embed_images"].append(
                    await save_embed_image(url, message, index, directory, session)
                )
    return record


def write_report(records: list[dict], directory: Path,
                 requested_channels: list[tuple[int, str]] | None = None,
                 primary_channel_id: int | None = None) -> dict:
    groups = defaultdict(Counter)
    failures = []
    missing_images = []
    by_channel = {
        channel_id: {"name": name, "message_count": 0, "table_ids": set(),
                     "posts_without_table_id": 0}
        for channel_id, name in (requested_channels or [])
    }
    for record in records:
        images = record["attachments"] + record.get("embed_images", [])
        channel = by_channel.setdefault(record["channel_id"], {
            "name": record["channel_name"], "message_count": 0, "table_ids": set(),
            "posts_without_table_id": 0,
        })
        channel["message_count"] += 1
        channel["table_ids"].update(record["table_ids"])
        channel["posts_without_table_id"] += not record["table_ids"]
        for table_id in record["table_ids"]:
            groups[table_id][record["kind"]] += 1
        if record["table_ids"] and record["kind"] in {"mmr", "result"} and not any(
            image["status"] == "saved" for image in images
        ):
            missing_images.append(record["jump_url"])
        for image in images:
            if image["status"].startswith("download_failed"):
                failures.append(record["jump_url"])
    problems = {
        str(table_id): {"result_posts": kinds["result"], "mmr_posts": kinds["mmr"],
                        "other_posts": kinds["other"]}
        for table_id, kinds in sorted(groups.items())
        if kinds["result"] != 1 or kinds["mmr"] != 1 or kinds["other"]
    }
    primary_ids = by_channel[primary_channel_id]["table_ids"] if primary_channel_id else set()
    missing_from_primary = {
        str(table_id): sorted(channel_id for channel_id, channel in by_channel.items()
                              if channel_id != primary_channel_id and table_id in channel["table_ids"])
        for table_id in sorted(set(groups) - primary_ids)
    } if primary_channel_id else {}
    report = {
        "message_count": len(records),
        "table_ids_found": len(groups),
        "channels": {
            str(channel_id): {"name": channel["name"],
                              "message_count": channel["message_count"],
                              "table_ids_found": len(channel["table_ids"]),
                              "lowest_table_id": min(channel["table_ids"], default=None),
                              "highest_table_id": max(channel["table_ids"], default=None),
                              "posts_without_table_id": channel["posts_without_table_id"]}
            for channel_id, channel in sorted(by_channel.items())
        },
        "primary_channel_id": primary_channel_id,
        "table_ids_missing_from_primary_channel": missing_from_primary,
        "lowest_table_id": min(groups, default=None),
        "highest_table_id": max(groups, default=None),
        "posts_without_table_id": sum(not record["table_ids"] for record in records),
        "unmatched_ids_between_first_and_last": [
            table_id for table_id in range(min(groups, default=0), max(groups, default=-1) + 1)
            if table_id not in groups
        ],
        "table_ids_needing_review": problems,
        "posts_missing_images": missing_images,
        "failed_image_downloads": failures,
        "note": "Mirrored posts may appear in more than one results channel. "
                "Review duplicate IDs and S8 season boundaries; this inventory cannot prove "
                "the season is complete or identify reverted tables.",
    }
    (directory / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (directory / "index.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("table_ids", "kind", "channel_id", "message_id", "created_at",
                         "jump_url", "image_paths", "image_statuses"))
        for record in records:
            images = record["attachments"] + record.get("embed_images", [])
            writer.writerow((";".join(map(str, record["table_ids"])), record["kind"],
                             record["channel_id"], record["message_id"], record["created_at"],
                             record["jump_url"],
                             ";".join(item.get("path", "") for item in images),
                             ";".join(item["status"] for item in images)))
    return report


async def export(args, token: str) -> dict:
    import aiohttp
    import discord

    intents = discord.Intents.none()
    intents.guilds = True
    intents.message_content = True
    client = discord.Client(intents=intents)
    connection = asyncio.create_task(client.start(token, reconnect=False))
    ready = asyncio.create_task(client.wait_until_ready())
    try:
        done, _ = await asyncio.wait((connection, ready), timeout=60,
                                     return_when=asyncio.FIRST_COMPLETED)
        if connection in done:
            connection.result()
            raise RuntimeError("Bot disconnected before it was ready")
        if ready not in done:
            raise RuntimeError("Timed out waiting for bot readiness")
        application = await client.application_info()
        if application.id != args.application_id:
            raise RuntimeError(f"Wrong bot application ID: {application.id}")
        guild = client.get_guild(args.guild_id)
        if guild is None:
            raise RuntimeError("Bot is not installed in the selected server")
        bot_member = guild.me or await guild.fetch_member(client.user.id)
        channels = []
        for channel_id in args.channel_id:
            channel = await client.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                raise RuntimeError(f"{channel_id} is not a text channel in the selected server")
            permissions = channel.permissions_for(bot_member)
            if not permissions.view_channel or not permissions.read_message_history:
                raise RuntimeError(f"Bot cannot view and read history in channel {channel_id}")
            channels.append(channel)

        if args.check_access:
            return {"channels": [(channel.id, channel.name) for channel in channels]}
        if args.probe_message_id:
            return {"probe": [image_source_summary(await channels[0].fetch_message(message_id))
                              for message_id in args.probe_message_id]}

        args.output.mkdir(parents=True, exist_ok=False)
        records = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            with (args.output / "messages.jsonl").open("w", encoding="utf-8") as stream:
                for channel in channels:
                    async for message in channel.history(limit=None, after=args.since,
                                                         before=args.until, oldest_first=True):
                        record = await snapshot_message(message, args.output, session)
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                        stream.flush()
                        records.append(record)
        return write_report(records, args.output,
                            [(channel.id, channel.name) for channel in channels],
                            args.primary_channel_id)
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready
        if not connection.done():
            connection.cancel()
            with suppress(asyncio.CancelledError):
                await connection
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--guild-id", type=int, required=True)
    parser.add_argument("--channel-id", type=int, action="append", required=True,
                        help="Results channel ID; repeat to include other results channels")
    parser.add_argument("--primary-channel-id", type=int,
                        help="Results channel to compare against the other selected channels")
    parser.add_argument("--since", type=parse_day, required=True,
                        help="UTC start day, e.g. 2025-09-28")
    parser.add_argument("--until", type=parse_day, help="Exclusive UTC end day")
    parser.add_argument("--output", type=Path, required=True, help="New private archive directory")
    parser.add_argument("--check-access", action="store_true",
                        help="Check all requested channels without reading history or writing files")
    parser.add_argument("--probe-message-id", type=int, action="append",
                        help="Inspect image sources of a single known message; repeat for another")
    args = parser.parse_args(argv)
    if args.until and args.until <= args.since:
        parser.error("--until must be after --since")
    if args.output.exists() and not (args.check_access or args.probe_message_id):
        parser.error("Output directory already exists; use a new location to preserve archives")
    if len(args.channel_id) != len(set(args.channel_id)):
        parser.error("Each --channel-id may be supplied only once")
    if args.primary_channel_id is not None and args.primary_channel_id not in args.channel_id:
        parser.error("--primary-channel-id must also appear as a --channel-id")
    if args.probe_message_id and (len(args.channel_id) != 1 or args.check_access):
        parser.error("--probe-message-id requires exactly one channel and no --check-access")
    try:
        token = getpass.getpass("Authorized server bot token (hidden): ")
        if not token:
            raise ValueError("A bot token is required")
        report = asyncio.run(export(args, token))
    except Exception as error:
        print(f"Archive failed: {error}", file=sys.stderr)
        return 2
    if args.check_access:
        print("Access confirmed for the requested server and channels:")
        for channel_id, name in report["channels"]:
            print(f"  #{name} ({channel_id})")
        print("No message history was read and no files were created.")
        return 0
    if args.probe_message_id:
        for item in report["probe"]:
            print(f"Message {item['message_id']}: {item['attachment_count']} attachments; "
                  f"embed image hosts: {item['embed_image_hosts']}")
        print("Only the specified messages were read. No images were downloaded or files created.")
        return 0
    print(f"Archived {report['message_count']} messages and indexed "
          f"{report['table_ids_found']} table IDs in {args.output}.")
    primary_summary = (f"{len(report['table_ids_missing_from_primary_channel'])} IDs "
                       "missing from the primary channel, " if args.primary_channel_id
                       else "no primary-channel comparison, ")
    print(f"Review {len(report['channels'])} channel summaries, "
          f"{primary_summary}"
          f"{len(report['table_ids_needing_review'])} table IDs, "
          f"{len(report['unmatched_ids_between_first_and_last'])} gaps, "
          f"{len(report['posts_missing_images'])} posts without images, and "
          f"{len(report['failed_image_downloads'])} failed downloads in audit.json.")
    return 1 if (not report["table_ids_found"] or report["table_ids_needing_review"]
                 or report["table_ids_missing_from_primary_channel"]
                 or report["unmatched_ids_between_first_and_last"]
                 or report["posts_missing_images"] or report["failed_image_downloads"]
                 or any(channel["message_count"] == 0
                        for channel in report["channels"].values())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
