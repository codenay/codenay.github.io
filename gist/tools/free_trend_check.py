#!/usr/bin/env python3
"""Collect free Ghana trend signals for editorial review, without changing Gist's feed."""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


GOOGLE_RSS_URL = "https://trends.google.com/trending/rss?geo=GH"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
GOOGLE_NS = "{https://trends.google.com/trending/rss}"
USER_AGENT = "GistTrendCheck/1.0 (+https://codenay.github.io/gist/)"
NOTICE = (
    "Discovery signals only. Google searches do not prove a social-platform trend. "
    "Verify each TikTok, X, and Instagram topic on that platform before editing feed.json. "
    "YouTube chart videos are candidates, not complete four-post Gist topics."
)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError("Unexpected source response: HTTP %s" % response.status)
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Source response is too large")
    return data


def iso_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def google_signals(xml_bytes):
    root = ET.fromstring(xml_bytes)
    if root.tag != "rss":
        raise ValueError("Google Trends did not return an RSS feed")
    items = root.findall("./channel/item")
    signals = []
    seen = set()
    for item in items[:50]:
        title = (item.findtext("title") or "").strip()
        if not title or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        signals.append({
            "title": title[:160],
            "searchVolume": (item.findtext(GOOGLE_NS + "approx_traffic") or "").strip(),
            "startedAt": iso_date(item.findtext("pubDate")),
            "sourceURL": GOOGLE_RSS_URL,
        })
    return signals


def youtube_signals(json_bytes):
    payload = json.loads(json_bytes)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("YouTube did not return a video chart")
    signals = []
    seen = set()
    for position, item in enumerate(payload["items"][:50], start=1):
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        snippet = item.get("snippet")
        if not isinstance(video_id, str) or not isinstance(snippet, dict):
            continue
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        thumbnails = snippet.get("thumbnails") or {}
        thumb = thumbnails.get("medium") or thumbnails.get("default") or {}
        signals.append({
            "chartPosition": position,
            "title": str(snippet.get("title") or "")[:160],
            "channel": str(snippet.get("channelTitle") or "")[:80],
            "publishedAt": snippet.get("publishedAt"),
            "videoURL": "https://www.youtube.com/watch?v=" + urllib.parse.quote(video_id, safe=""),
            "thumbnailURL": thumb.get("url") if isinstance(thumb, dict) else None,
        })
    return signals


def youtube_chart_url(api_key):
    query = urllib.parse.urlencode({
        "part": "snippet",
        "chart": "mostPopular",
        "regionCode": "GH",
        "maxResults": "50",
        "key": api_key,
    })
    return YOUTUBE_API_URL + "?" + query


def make_report(google_items, youtube_items, previous=None, now=None):
    sources = [
        {"name": "Google Trends Ghana", "kind": "search_candidates", "url": GOOGLE_RSS_URL,
         "items": google_items},
        {"name": "YouTube Ghana chart", "kind": "platform_candidates",
         "url": "https://www.youtube.com/feed/trending?gl=GH",
         "status": "available" if youtube_items is not None else "needs_free_api_key",
         "items": youtube_items or []},
    ]
    stable = {"schemaVersion": 1, "notice": NOTICE, "sources": sources}
    if previous and all(previous.get(key) == value for key, value in stable.items()):
        return None
    instant = now or datetime.now(timezone.utc)
    return dict(stable, generatedAt=instant.isoformat(timespec="seconds").replace("+00:00", "Z"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rss-file", type=Path, help="Use a saved RSS response for local verification")
    parser.add_argument("--youtube-file", type=Path, help="Use a saved YouTube response for local verification")
    args = parser.parse_args()

    try:
        rss = args.rss_file.read_bytes() if args.rss_file else fetch(GOOGLE_RSS_URL)
        google_items = google_signals(rss)
        if not google_items:
            raise ValueError("Google Trends returned no Ghana candidates; keeping the previous report")

        api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        youtube_items = None
        if args.youtube_file:
            youtube_items = youtube_signals(args.youtube_file.read_bytes())
        elif api_key:
            youtube_items = youtube_signals(fetch(youtube_chart_url(api_key)))

        previous = None
        if args.output.exists():
            previous = json.loads(args.output.read_text(encoding="utf-8"))
        report = make_report(google_items, youtube_items, previous)
        if report is None:
            print("Sources checked; candidate list unchanged. The published briefing was not touched.")
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Wrote %s Google and %s YouTube candidates to %s." % (
            len(google_items), len(youtube_items or []), args.output))
        return 0
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as error:
        print("Trend check failed: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
