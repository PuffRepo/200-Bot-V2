"""Read only the public country of the exact MKCentral registry player."""
import json
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from v2.countries import country_code

MAX_PROFILE_BYTES = 256 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def read_profile(profile_id):
    request = Request(f'https://mkcentral.com/api/registry/players/{profile_id}',
                      headers={'User-Agent': '200-Lounge-V2/1.0', 'Accept': 'application/json'})
    with build_opener(NoRedirect()).open(request, timeout=8) as response:
        if response.status != 200:
            return None
        data = response.read(MAX_PROFILE_BYTES + 1)
    if len(data) > MAX_PROFILE_BYTES:
        return None
    return json.loads(data)


def profile_country(profile_id, fetch=read_profile):
    """Empty means unavailable; callers must still allow ordinary staff verification."""
    if type(profile_id) is not int or profile_id < 1:
        return ''
    try:
        profile = fetch(profile_id)
        if (not isinstance(profile, dict) or type(profile.get('id')) is not int
                or profile['id'] != profile_id or profile.get('is_hidden') is not False):
            return ''
        return country_code(profile.get('country_code'))
    except (URLError, OSError, ValueError, TypeError):
        return ''
