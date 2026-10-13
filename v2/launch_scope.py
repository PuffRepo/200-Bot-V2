"""Approved S9 entry points; historical rendering remains supported."""
ACTIVE_TIERS = ('tier-all', 'tier-c', 'tier-b', 'tier-a')
ACTIVE_FORMATS = (1, 2, 3, 4)

def submission_tier(tiers, channel_id):
    from v2.lab_store import LabError
    matches = [name for name, entry in tiers.items()
               if name in ACTIVE_TIERS and entry['submission_channel_id'] == channel_id]
    if len(matches) != 1:
        raise LabError('Use a configured S9 tier submission channel.')
    return matches[0]
