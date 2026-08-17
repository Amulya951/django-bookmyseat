"""Small reusable helpers for the movies app."""
import re


def youtube_embed_url(url):
    """Turn a YouTube watch/short URL into an embeddable URL.

    Returns the embed URL (e.g. https://www.youtube.com/embed/VIDEO_ID)
    or None if the input is empty or not a recognised YouTube link.
    """
    if not url:
        return None
    match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]+)', url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"
    return None
