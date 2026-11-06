"""WhisperTail daily episode runner (Replit Scheduled Deployment entrypoint).

Runs once a day. Picks the next "ready" episode from queue.json in the
whispertail-staging GitHub repo, renders it with main.py's proven functions,
uploads the finished video back to GitHub, and schedules it on TikTok +
YouTube Shorts through the Buffer API.

Design notes (do not change lightly):
- Reuses main.py ONLY through the same truncated-exec pattern as make_ep2.py:
  exec only the part before `if __name__ == "__main__":`, so the legacy
  bottom upload code in main.py never runs. Never run main.py itself.
- Voiceover is NOT generated here. Muse generates the Adam voiceover and
  pushes it (plus the manifest entry) to GitHub ahead of time. This script
  only downloads, renders, and schedules.
- Buffer posts are scheduled (customScheduled + dueAt), NOT sent immediately,
  so there is a half-day window for Muse to review the rendered video and
  cancel the post in Buffer if something is wrong.
- Secrets come from Replit Secrets env vars only: OPENAI_API_KEY, GH_PAT,
  BUFFER_API_KEY. Never print them, never hardcode them.
"""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import typing_extensions

# --- typing_extensions Sentinel shim (same as make_ep2.py) ---
if not hasattr(typing_extensions, "Sentinel"):
    class Sentinel:
        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return "Sentinel(%r)" % (self.name,)

    typing_extensions.Sentinel = Sentinel

from openai import OpenAI

# --- Config (edit these, not the code below) ---
GITHUB_OWNER = "Miranda10128"
GITHUB_REPO = "whispertail-staging"
GITHUB_BRANCH = "main"
MANIFEST_PATH = "queue.json"
GITHUB_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
)
GITHUB_API_BASE = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
)
BUFFER_API_URL = "https://api.buffer.com"
# Hours between render and publish: render early morning, publish later the
# same day, leaving a half-day window for review.
PUBLISH_DELAY_HOURS = 12
YOUTUBE_CATEGORY_ID = "24"  # Entertainment (same as main.py)


def load_main_functions():
    """Exec main.py (truncated before __main__) and return its namespace."""
    src = Path("main.py").read_text(encoding="utf-8")
    cut = src.rindex('if __name__ == "__main__":')
    ns = {"__name__": "whispertail_daily", "__file__": str(Path("main.py").resolve())}
    exec(compile(src[:cut], "main.py", "exec"), ns)
    return ns


def github_headers():
    token = os.getenv("GH_PAT")
    if not token:
        raise RuntimeError("GH_PAT is missing from Replit Secrets.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def github_get_file(path):
    """Return (sha, decoded_text) for a repo file, or (None, None) if missing."""
    resp = requests.get(
        f"{GITHUB_API_BASE}/{path}",
        headers=github_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=30,
    )
    if resp.status_code == 404:
        return None, None
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return data["sha"], content


def github_put_file(path, content_bytes, message, sha=None):
    """Create or update a repo file. Pass the current sha when updating."""
    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    resp = requests.put(
        f"{GITHUB_API_BASE}/{path}",
        headers=github_headers(),
        json=body,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


def save_manifest(manifest, sha, message):
    """Write queue.json back to GitHub. Returns the new sha."""
    new_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    result = github_put_file(MANIFEST_PATH, new_text.encode("utf-8"), message, sha=sha)
    return result["content"]["sha"]


def buffer_graphql(query, variables=None):
    token = os.getenv("BUFFER_API_KEY")
    if not token:
        raise RuntimeError("BUFFER_API_KEY is missing from Replit Secrets.")
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    resp = requests.post(
        BUFFER_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer API error: {data['errors']}")
    return data.get("data") or {}


def buffer_channels():
    """Return {service: channel} for connected, non-disconnected channels."""
    data = buffer_graphql("query { account { organizations { id name } } }")
    orgs = (data.get("account") or {}).get("organizations") or []
    if not orgs:
        raise RuntimeError("Buffer account has no organizations.")
    org_id = orgs[0]["id"]
    data = buffer_graphql(
        'query { channels(input:{organizationId:"%s"}) '
        "{ id name service displayName isDisconnected } }" % org_id
    )
    by_service = {}
    for ch in data.get("channels") or []:
        if ch.get("isDisconnected"):
            continue
        by_service[ch.get("service")] = ch
    return by_service


CREATE_POST_MUTATION = (
    "mutation CreatePost($input: CreatePostInput!) {"
    " createPost(input: $input) {"
    " ... on PostActionSuccess { post { id text status dueAt } }"
    " ... on MutationError { message } } }"
)


def buffer_create_post(channel_id, text, video_url, due_at, youtube_meta=None):
    post_input = {
        "channelId": channel_id,
        "text": text,
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at,
        "assets": [{"video": {"url": video_url}}],
    }
    if youtube_meta is not None:
        post_input["metadata"] = {"youtube": youtube_meta}
    data = buffer_graphql(CREATE_POST_MUTATION, {"input": post_input})
    result = (data or {}).get("createPost") or {}
    if "message" in result:
        raise RuntimeError(f"Buffer createPost failed: {result['message']}")
    post = result.get("post") or {}
    if not post.get("id"):
        raise RuntimeError(f"Buffer createPost returned no post id: {result}")
    return post


def main():
    # 1. Pick the next "ready" episode from the manifest.
    manifest_sha, manifest_text = github_get_file(MANIFEST_PATH)
    if manifest_text is None:
        raise RuntimeError(f"Manifest not found in GitHub: {MANIFEST_PATH}")
    manifest = json.loads(manifest_text)
    episodes = manifest.get("episodes") or []
    episode = next((ep for ep in episodes if ep.get("status") == "ready"), None)
    if episode is None:
        print("No 'ready' episode in queue.json. Nothing to do.")
        return 0

    episode_id = episode["id"]
    title = episode["title"]
    description = episode.get("description", "")
    hashtags = episode.get("hashtags", "")
    audio_rel = episode["audio"]
    print(f"Episode: {episode_id} | {title}")

    # 2. Download the voiceover from GitHub raw.
    audio_url = f"{GITHUB_RAW_BASE}/{audio_rel}"
    audio_path = Path("generated-artifacts") / "final_voiceover.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(audio_url, timeout=120)
    resp.raise_for_status()
    audio_path.write_bytes(resp.content)
    if audio_path.stat().st_size == 0:
        raise RuntimeError(f"Downloaded voiceover is empty: {audio_url}")
    print(f"Downloaded voiceover: {audio_path} ({audio_path.stat().st_size} bytes)")

    # 3. Render with main.py's proven functions (same pattern as make_ep2.py).
    ns = load_main_functions()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from Replit Secrets.")
    client = OpenAI(api_key=api_key, timeout=120)
    print("Step 1/2: Whisper captions...")
    subtitle_path = ns["transcribe_audio"](str(audio_path), client)
    print("Step 2/2: Render video...")
    video_path = Path(ns["create_final_video"](str(audio_path), title, subtitle_path))
    print(f"Rendered: {video_path} ({video_path.stat().st_size} bytes)")

    # 4. Upload the finished video back to GitHub (stable public link for Buffer).
    video_rel = f"episodes/{episode_id}/final_video.mp4"
    existing_sha, _ = github_get_file(video_rel)
    github_put_file(
        video_rel,
        video_path.read_bytes(),
        f"WhisperTail {episode_id}: rendered video",
        sha=existing_sha,
    )
    video_url = f"{GITHUB_RAW_BASE}/{video_rel}"
    print(f"Uploaded video to GitHub: {video_url}")
    episode["video_url"] = video_url
    manifest_sha = save_manifest(
        manifest, manifest_sha, f"WhisperTail {episode_id}: video rendered"
    )

    # 5. Schedule on TikTok + YouTube via Buffer (later today, not now).
    # Resume-safe: a post already recorded in the manifest is skipped, so a
    # rerun never creates a duplicate.
    due_at = (datetime.now(timezone.utc) + timedelta(hours=PUBLISH_DELAY_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    channels = buffer_channels()
    tiktok = channels.get("tiktok")
    youtube = channels.get("youtube")
    if not tiktok or not youtube:
        raise RuntimeError(
            f"Buffer is missing a channel (tiktok={bool(tiktok)}, youtube={bool(youtube)})."
        )
    caption = f"{title}\n\n{description}\n\n{hashtags}".strip()
    yt_text = f"{description}\n\n{hashtags}".strip()
    yt_meta = {
        "title": title,
        "categoryId": YOUTUBE_CATEGORY_ID,
        "privacy": "public",
        "madeForKids": False,
    }
    if episode.get("buffer_tiktok_post_id"):
        tiktok_post = {"id": episode["buffer_tiktok_post_id"]}
        print(f"TikTok already scheduled: post {tiktok_post['id']} (skipped)")
    else:
        tiktok_post = buffer_create_post(tiktok["id"], caption, video_url, due_at)
        print(f"TikTok scheduled: post {tiktok_post['id']} due {tiktok_post.get('dueAt')}")
        episode["buffer_tiktok_post_id"] = tiktok_post["id"]
        episode["due_at"] = due_at
        manifest_sha = save_manifest(
            manifest, manifest_sha, f"WhisperTail {episode_id}: TikTok scheduled"
        )
    if episode.get("buffer_youtube_post_id"):
        youtube_post = {"id": episode["buffer_youtube_post_id"]}
        print(f"YouTube already scheduled: post {youtube_post['id']} (skipped)")
    else:
        youtube_post = buffer_create_post(
            youtube["id"], yt_text, video_url, due_at, youtube_meta=yt_meta
        )
        print(f"YouTube scheduled: post {youtube_post['id']} due {youtube_post.get('dueAt')}")
        episode["buffer_youtube_post_id"] = youtube_post["id"]
        episode["due_at"] = due_at
        manifest_sha = save_manifest(
            manifest, manifest_sha, f"WhisperTail {episode_id}: YouTube scheduled"
        )

    # 6. Mark the episode scheduled in the manifest.
    episode["status"] = "scheduled"
    episode["video_url"] = video_url
    manifest_sha = save_manifest(
        manifest, manifest_sha, f"WhisperTail {episode_id}: scheduled"
    )
    print(f"DONE: {episode_id} scheduled for {due_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
