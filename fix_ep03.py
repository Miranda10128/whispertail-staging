"""One-off recovery for ep03-dyatlov-pass (2026-11-05).

The first daily_ep.py test run rendered the video, uploaded it to GitHub,
and scheduled the TikTok post, then failed on the YouTube post (Buffer
rejected the metadata field name "privacyStatus"). The manifest was never
updated, so a plain rerun of daily_ep.py would create a DUPLICATE TikTok
post.

This script finishes ONLY the missing pieces for ep03:
  1. Creates the YouTube Buffer post (fixed metadata field "privacy").
  2. Records both post IDs in queue.json and marks the episode "scheduled".

Do NOT rerun daily_ep.py for ep03. Run this once:
    python fix_ep03.py
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import daily_ep

EPISODE_ID = "ep03-dyatlov-pass"
# From the first test run's Shell output (TikTok already scheduled).
TIKTOK_POST_ID = "6aed72ce7b7e47e8f41081c7"
TIKTOK_DUE_AT = "2026-11-06T18:25:17.000Z"


def main():
    manifest_sha, manifest_text = daily_ep.github_get_file(daily_ep.MANIFEST_PATH)
    if manifest_text is None:
        raise RuntimeError("queue.json not found in GitHub.")
    manifest = json.loads(manifest_text)
    episode = next(
        (ep for ep in manifest.get("episodes") or [] if ep.get("id") == EPISODE_ID),
        None,
    )
    if episode is None:
        raise RuntimeError(f"Episode {EPISODE_ID} not found in queue.json.")
    if episode.get("status") == "scheduled":
        print(f"{EPISODE_ID} is already scheduled. Nothing to do.")
        return 0

    video_url = episode.get("video_url") or (
        f"{daily_ep.GITHUB_RAW_BASE}/episodes/{EPISODE_ID}/final_video.mp4"
    )
    title = episode["title"]
    description = episode.get("description", "")
    hashtags = episode.get("hashtags", "")
    due_at = (
        datetime.now(timezone.utc) + timedelta(hours=daily_ep.PUBLISH_DELAY_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    channels = daily_ep.buffer_channels()
    youtube = channels.get("youtube")
    if not youtube:
        raise RuntimeError("Buffer YouTube channel not found.")
    yt_text = f"{description}\n\n{hashtags}".strip()
    yt_meta = {
        "title": title,
        "categoryId": daily_ep.YOUTUBE_CATEGORY_ID,
        "privacy": "public",
        "madeForKids": False,
    }
    youtube_post = daily_ep.buffer_create_post(
        youtube["id"], yt_text, video_url, due_at, youtube_meta=yt_meta
    )
    print(f"YouTube scheduled: post {youtube_post['id']} due {youtube_post.get('dueAt')}")

    episode["status"] = "scheduled"
    episode["video_url"] = video_url
    episode["due_at"] = due_at
    episode["buffer_tiktok_post_id"] = TIKTOK_POST_ID
    episode["buffer_tiktok_due_at"] = TIKTOK_DUE_AT
    episode["buffer_youtube_post_id"] = youtube_post["id"]
    new_manifest = json.dumps(manifest, ensure_ascii=False, indent=2)
    daily_ep.github_put_file(
        daily_ep.MANIFEST_PATH,
        new_manifest.encode("utf-8"),
        f"WhisperTail {EPISODE_ID}: scheduled (recovery)",
        sha=manifest_sha,
    )
    print(f"DONE: {EPISODE_ID} fully scheduled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
