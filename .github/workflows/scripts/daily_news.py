#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import os
import re
import xml.etree.ElementTree as ET


UK = ZoneInfo("Europe/London")
OUTPUT = Path("docs/daily-briefing.md")
LOOKBACK = timedelta(hours=18)

CATEGORIES = [
    (
        "Technology",
        '(technology OR tech OR AI OR software OR cybersecurity OR gadgets) when:1d',
    ),
    (
        "Business",
        '(business OR companies OR economy OR finance OR markets) when:1d',
    ),
    (
        "Stories you might not have seen",
        '(science OR climate OR health OR culture OR space OR "local news") when:1d',
    ),
]


def fetch_stories(query: str, cutoff: datetime) -> list[dict]:
    params = urlencode(
        {
            "q": query,
            "hl": "en-GB",
            "gl": "GB",
            "ceid": "GB:en",
        }
    )
    url = f"https://news.google.com/rss/search?{params}"
    request = Request(url, headers={"User-Agent": "BusinessNewsDailyBriefing/1.0"})

    with urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())

    stories = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        published_text = item.findtext("pubDate", "").strip()
        source_element = item.find("source")
        source = source_element.text.strip() if source_element is not None and source_element.text else ""

        if not title or not link or not published_text:
            continue

        try:
            published = parsedate_to_datetime(published_text)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            published = published.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue

        if published < cutoff:
            continue

        stories.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published": published,
            }
        )

    return stories


def normalized_title(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold())


def main() -> None:
    now = datetime.now(UK)

    # GitHub Actions scheduled runs can be delayed. Only publish during the
    # 08:00 UK hour; allow manual runs from the Actions tab at any time.
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" and now.hour != 8:
        print(f"Skipping run: UK time is {now:%H:%M}; waiting for the 08:00 hour.")
        return

    cutoff = now.astimezone(timezone.utc) - LOOKBACK
    seen_titles: set[str] = set()
    sections = []

    for heading, query in CATEGORIES:
        try:
            results = fetch_stories(query, cutoff)
        except Exception as error:
            print(f"Could not fetch {heading} stories: {error}")
            results = []

        selected = []
        for story in results:
            key = normalized_title(story["title"])
            if key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(story)
            if len(selected) == 10:
                break

        sections.append(f"## {heading}\n")
        if not selected:
            sections.append("_No matching stories found in the last 18 hours._\n")
            continue

        for story in selected:
            published = story["published"].astimezone(UK).strftime("%H:%M UK")
            source = f" — {story['source']}" if story["source"] else ""
            sections.append(
                f"- [{story['title']}]({story['link']})"
                f"{source} · {published}\n"
            )

        if len(selected) < 10:
            sections.append(
                f"\n_Found {len(selected)} qualifying stories; "
                "the briefing does not add older stories to fill the list._\n"
            )

        sections.append("\n")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        f"# Daily business news briefing\n\n"
        f"Updated {now:%A, %-d %B %Y at %H:%M UK time}. "
        f"Stories are from the preceding 18 hours.\n\n"
        + "\n".join(sections),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
