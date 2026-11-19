# businessnews
Daily business news from the UK and the world
#!/usr/bin/env python3
"""Build and email a daily UK and world business/technology news briefing."""

from __future__ import annotations

import difflib
import email.utils
import html
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
USER_AGENT = "BusinessNewsDailyBriefing/1.0 (RSS reader)"
FEED_QUERIES = {
    "UK business & tech": [
        "(UK OR Britain OR British) (business OR economy OR finance) when:1d",
        "(UK OR Britain OR British) (technology OR tech OR startups) when:1d",
        "(UK OR Britain OR British) (markets OR companies OR industry) when:1d",
    ],
    "World business & tech": [
        "(global OR world) (business OR economy OR finance OR markets) when:1d",
        "(global OR world) (technology OR tech OR AI OR startups) when:1d",
        "(international OR global) (business OR technology) when:1d",
    ],
    "More to explore": [
        '(startups OR "venture capital" OR "small business") when:1d',
        '(cybersecurity OR "supply chain" OR innovation) (business OR technology) when:1d',
        '(business OR technology) (companies OR regulation OR research) when:1d -UK',
    ],
}


@dataclass
class Story:
    title: str
    link: str
    source: str
    published: datetime | None
    summary: str
    appearances: int = 1
    score: float = 0.0


class TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_html(value: str) -> str:
    parser = TextOnly()
    try:
        parser.feed(value)
        text = " ".join(parser.parts)
    except Exception:
        text = value
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def google_news_url(query: str) -> str:
    params = urllib.parse.urlencode(
        {"q": query, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"}
    )
    return f"https://news.google.com/rss/search?{params}"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_feed(query: str) -> list[Story]:
    request = urllib.request.Request(
        google_news_url(query), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(3_000_000)
        root = ET.fromstring(payload)
    except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
        print(f"Warning: could not fetch feed {query!r}: {exc}", file=sys.stderr)
        return []

    stories: list[Story] = []
    for item in root.findall("./channel/item"):
        title = clean_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_node = item.find("source")
        source = clean_html(source_node.text or "") if source_node is not None else "News source"
        summary = clean_html(item.findtext("description") or "")
        stories.append(
            Story(
                title=title,
                link=link,
                source=source or "News source",
                published=parse_date(item.findtext("pubDate")),
                summary=summary,
            )
        )
    return stories


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def is_same_story(first: Story, second: Story) -> bool:
    if first.link == second.link:
        return True
    a, b = title_key(first.title), title_key(second.title)
    return bool(a and b and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84)


def collect_section(queries: list[str]) -> list[Story]:
    unique: list[Story] = []
    for query in queries:
        for candidate in fetch_feed(query):
            match = next((story for story in unique if is_same_story(story, candidate)), None)
            if match:
                match.appearances += 1
                if len(candidate.summary) > len(match.summary):
                    match.summary = candidate.summary
            else:
                unique.append(candidate)
        # Avoid hammering the RSS endpoint while fetching the small set of searches.
        time.sleep(0.25)

    now = datetime.now(timezone.utc)
    for story in unique:
        age_hours = (
            max(0.0, (now - story.published).total_seconds() / 3600)
            if story.published
            else 24.0
        )
        recency = max(0.0, 4.0 - age_hours / 6.0)
        story.score = story.appearances * 3.0 + recency
    return sorted(unique, key=lambda story: (story.score, story.published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)


def select_sections() -> dict[str, list[Story]]:
    sections: dict[str, list[Story]] = {}
    for name in ("UK business & tech", "World business & tech"):
        sections[name] = collect_section(FEED_QUERIES[name])[:10]

    already_selected = [story for stories in sections.values() for story in stories]
    extra = [
        story
        for story in collect_section(FEED_QUERIES["More to explore"])
        if not any(is_same_story(story, selected) for selected in already_selected)
    ]
    sections["More to explore"] = extra[:10]
    return sections


def render_text(sections: dict[str, list[Story]]) -> str:
    lines = ["Your daily business & technology briefing", ""]
    for name, stories in sections.items():
        lines.extend([name, "=" * len(name)])
        if not stories:
            lines.extend(["No stories were available from the feeds today.", ""])
            continue
        for index, story in enumerate(stories, start=1):
            when = story.published.astimezone(LONDON).strftime("%H:%M UK time") if story.published else "time unavailable"
            lines.append(f"{index}. {story.title}")
            lines.append(f"   {story.source} · {when}")
            lines.append(f"   {story.link}")
            if story.summary:
                lines.append(f"   {story.summary[:350]}")
            lines.append("")
    lines.append("Stories are selected from Google News RSS search results; rankings are based on recency and repeated appearance across searches.")
    return "\n".join(lines)


def render_html(sections: dict[str, list[Story]]) -> str:
    blocks = [
        '<div style="font-family:Arial,sans-serif;max-width:720px;margin:auto;color:#202124">',
        '<h1>Your daily business &amp; technology briefing</h1>',
    ]
    for name, stories in sections.items():
        blocks.append(f"<h2>{html.escape(name)}</h2>")
        if not stories:
            blocks.append("<p>No stories were available from the feeds today.</p>")
            continue
        blocks.append("<ol>")
        for story in stories:
            published = story.published.astimezone(LONDON).strftime("%H:%M UK time") if story.published else "time unavailable"
            blocks.append(
                "<li style=\"margin-bottom:16px\">"
                f'<a href="{html.escape(story.link, quote=True)}"><strong>{html.escape(story.title)}</strong></a><br>'
                f'<small>{html.escape(story.source)} · {html.escape(published)}</small>'
                + (f"<p>{html.escape(story.summary[:350])}</p>" if story.summary else "")
                + "</li>"
            )
        blocks.append("</ol>")
    blocks.append(
        '<p style="color:#666;font-size:12px">Stories are selected from Google News RSS search results; rankings are based on recency and repeated appearance across searches. “More to explore” is not personalized.</p></div>'
    )
    return "\n".join(blocks)


def send_email(subject: str, plain: str, rich: str) -> None:
    required = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["EMAIL_FROM"]
    message["To"] = os.environ["EMAIL_TO"]
    message.set_content(plain)
    message.add_alternative(rich, subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(message)


def main() -> int:
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        local_now = datetime.now(LONDON)
        if local_now.hour != 8 or local_now.minute < 30:
            print(f"Skipping scheduled run at {local_now:%Y-%m-%d %H:%M %Z}; waiting for 08:30 UK time.")
            return 0

    sections = select_sections()
    plain = render_text(sections)
    print(plain)
    send_email(f"Daily business & tech briefing — {datetime.now(LONDON):%d %b %Y}", plain, render_html(sections))
    print("Briefing emailed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
