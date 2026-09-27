"""Meaningful lab workflow boundaries: identity collisions, review, and cooldown."""

from types import SimpleNamespace

import pytest

from v2.identity import IdentityStore, NAME_COOLDOWN_SECONDS, WorkflowError, mkc_profile_id


def profile(mkc_id: int) -> str:
    return f"https://mkcentral.com/en-us/registry/players/profile?id={mkc_id}"


def verified(store: IdentityStore, player_id: int, mkc_id: int, name: str) -> None:
    request_id = store.submit_verification(player_id, profile(mkc_id), now=100)
    store.review_verification(request_id, 99, approve=True, player_name=name, now=110)


def test_only_direct_mkcentral_profile_links_are_accepted():
    assert mkc_profile_id(profile(930)) == 930
    for url in (
        "https://mkcentral.com.evil.example/en-us/registry/players/profile?id=930",
        "http://mkcentral.com/en-us/registry/players/profile?id=930",
        "https://mkcentral.com/en-us/registry/players/profile?id=930&id=931",
        "https://mkcentral.com/en-us/registry/players/profile?id=0",
        "https://mkcentral.com/en-us/registry/players?id=930",
    ):
        with pytest.raises(WorkflowError):
            mkc_profile_id(url)


def test_verification_requires_decision_and_prevents_duplicate_identity(tmp_path):
    store = IdentityStore(tmp_path / "private" / "lab.sqlite3")
    pending_id = store.submit_verification(1001, profile(930), now=100)
    assert store.submit_verification(1001, profile(930), now=101) == pending_id
    assert store.verification_claim(pending_id) == (1001, 930)
    with pytest.raises(WorkflowError):
        store.submit_verification(1002, profile(930), now=102)
    assert store.review_verification(pending_id, 2001, approve=True, player_name="Racer", now=120).player_id == 1001
    with pytest.raises(WorkflowError):
        store.review_verification(pending_id, 2001, approve=True, player_name="Racer", now=121)
    with pytest.raises(WorkflowError):
        store.submit_verification(1002, profile(930), now=122)
    with pytest.raises(WorkflowError):
        store.submit_verification(1001, profile(931), now=123)


def test_denial_releases_mkc_claim_and_name_approval_sets_cooldown(tmp_path):
    store = IdentityStore(tmp_path / "lab.sqlite3")
    request_id = store.submit_verification(1001, profile(930), now=100)
    store.review_verification(request_id, 2001, approve=False, now=110)
    verified(store, 1002, 930, "Racer")

    change_id = store.submit_name(1002, "New Name", now=1000)
    assert store.submit_name(1002, "New Name", now=1001) == change_id
    with pytest.raises(WorkflowError):
        store.submit_name(1002, "Another", now=1001)
    store.review_name(change_id, 2001, approve=True, now=1010)
    with pytest.raises(WorkflowError, match="cooldown"):
        store.submit_name(1002, "Another", now=1010 + NAME_COOLDOWN_SECONDS - 1)
    assert store.submit_name(1002, "Another", now=1010 + NAME_COOLDOWN_SECONDS) > change_id


def test_review_collision_rolls_back_and_name_stays_pending(tmp_path):
    store = IdentityStore(tmp_path / "lab.sqlite3")
    verified(store, 1001, 930, "One")
    verified(store, 1002, 931, "Two")
    request_id = store.submit_name(1001, "TakenSoon", now=1000)
    # Another player claimed this name after the request was submitted.
    with store._connect() as connection:
        connection.execute("UPDATE players SET player_name = ?, name_key = ? WHERE player_id = ?", (
            "TakenSoon", "takensoon", 1002,
        ))
    with pytest.raises(WorkflowError, match="now taken"):
        store.review_name(request_id, 2001, approve=True, now=1010)
    _, names = store.pending()
    assert names == [(request_id, 1001, "TakenSoon")]


def test_staff_guard_requires_manage_guild_in_lab():
    # Exercise the Discord boundary without making a connection.
    discord = pytest.importorskip("discord")
    from scripts.v2_lab import lab_staff
    from scripts.discord_smoke import DEBUG_CHANNEL_ID, LAB_GUILD_ID

    ctx = SimpleNamespace(
        guild_id=LAB_GUILD_ID, channel_id=DEBUG_CHANNEL_ID,
        author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False)),
    )
    assert not lab_staff(ctx)
    ctx.author.guild_permissions.manage_guild = True
    assert lab_staff(ctx)
    ctx.channel_id = DEBUG_CHANNEL_ID + 1
    assert not lab_staff(ctx)
