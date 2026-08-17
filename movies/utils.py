"""Small reusable helpers for the movies app."""
import re
from urllib.parse import urlparse, parse_qs

# Only these hosts are accepted as trailer sources. Anything else is treated
# as an unavailable trailer rather than embedded.
ALLOWED_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'www.youtu.be',
}

# YouTube video IDs are exactly 11 URL-safe characters.
VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def youtube_embed_url(url):
    """Turn a YouTube link into a safe embeddable URL.

    Returns https://www.youtube.com/embed/VIDEO_ID, or None if the input is
    empty, not a YouTube host, or does not contain a well-formed video ID.

    The returned string is always built from a validated ID rather than
    echoed from user input, so a hostile trailer_url cannot break out of the
    iframe src attribute.
    """
    if not url:
        return None

    try:
        parts = urlparse(url.strip())
    except ValueError:
        return None

    if parts.scheme not in ('http', 'https'):
        return None
    if parts.hostname is None or parts.hostname.lower() not in ALLOWED_HOSTS:
        return None

    host = parts.hostname.lower()
    path = parts.path or ''
    video_id = None

    if host in ('youtu.be', 'www.youtu.be'):
        video_id = path.lstrip('/').split('/')[0]
    elif path == '/watch':
        video_id = parse_qs(parts.query).get('v', [None])[0]
    elif path.startswith('/embed/') or path.startswith('/shorts/'):
        segments = path.split('/')
        video_id = segments[2] if len(segments) > 2 else None

    if video_id and VIDEO_ID_RE.match(video_id):
        return f"https://www.youtube.com/embed/{video_id}"
    return None
