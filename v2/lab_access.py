"""Pure permission boundary used by lab Reporter commands."""
def can_report(guild_id, channel_id, manage_guild, role_ids, reporter_role_id, lab_guild_id, debug_channel_id):
    if guild_id != lab_guild_id or channel_id != debug_channel_id:
        return False
    return bool(manage_guild or (reporter_role_id is not None and reporter_role_id in role_ids))
