"""WhisperTail ep11 one-off render: The Night Stalker (image-sequence).

Same format as ep05 (hook first, image sequence + slow zoom), plus a
title text overlay on the first frame so the homepage thumbnail shows
the title. The hook audio still starts immediately; the title is
visual only.

Renders episode 11 with an image sequence + slow zoom instead of the usual
single forest-loop background. This is a ONE-OFF test of the new format; it
does NOT modify main.py or daily_ep.py.

Flow (run in Replit Shell, project root):
  1. Read queue.json from the whispertail-staging GitHub repo. If ep11 is
     already scheduled (both Buffer post IDs present), exit without doing
     anything (idempotency guard, same as daily_ep.py).
  2. Download the Adam voiceover + the 7 images from GitHub raw.
     NOTE: the images must already be in the repo at
     episodes/ep11-night-stalker/images/<filename> before running this script.
     They are NOT in the Replit project directory.
  3. Whisper-transcribe the voiceover (main.py transcribe_audio) to SRT.
  4. Compute the 8 visual-beat boundaries from the SRT cue timestamps
     (anchor-phrase matching, with interpolation fallback).
  5. Render 1080x1920/30fps H.264 with ffmpeg: each image gets a slow
     zoompan; all images are rendered fill; the last beat is a text question card;
     beat 1 also carries a title text overlay (first 6s, large) for the thumbnail.
     ASS subtitles (main.py create_ass_subtitles) are burned in.
  6. Upload final_video.mp4 to GitHub, schedule TikTok + YouTube via the
     Buffer API (dueAt = now + 12h), and mark ep11 "scheduled" in queue.json.

Secrets come from env only: OPENAI_API_KEY, GH_PAT, BUFFER_API_KEY.
Never print them, never hardcode them.
"""

import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import typing_extensions

# --- typing_extensions Sentinel shim (same as daily_ep.py / make_ep2.py) ---
if not hasattr(typing_extensions, "Sentinel"):
    class Sentinel:
        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return "Sentinel(%r)" % (self.name,)

    typing_extensions.Sentinel = Sentinel

from openai import OpenAI

# --- Config ---
EPISODE_ID = "ep11-night-stalker"
TITLE = "The Wow! Signal: The Night Stalker — The Summer Los Angeles Locked Its Doors"
DESCRIPTION = (
    "In the summer of 1985, Los Angeles was afraid to sleep. "
    "A man the newspapers called the Night Stalker broke into homes "
    "at random — until one fingerprint, and the people of East LA, ended it."
)
HASHTAGS = "#mystery #unsolved #truecrime #nightstalker #richardramirez"

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

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30

# Images in BEAT order (beat 8 is the question card, no image).
# The files must be in the repo at episodes/ep11-night-stalker/images/<filename>.
IMAGE_FILENAMES = [
    "01-la-night-street.jpg",      # beat 1: hook (LA street, every window lit)
    "02-newspaper-headline.jpg",   # beat 2: 1985 papers name the Night Stalker
    "03-dark-window.jpg",          # beat 3: half-open window at night
    "04-fingerprint-lab.jpg",      # beat 4: fingerprint lifted, lab match
    "05-orange-toyota.jpg",        # beat 5: the stolen car
    "06-east-la-crowd.jpg",        # beat 6: East LA crowd catches him
    "07-la-dawn.jpg",              # beat 7: dawn skyline, city sleeps again
]
# Render mode per beat: "fill" (scale to fill + crop), "pillarbox" (scale to
# fit + black bars, for landscape images whose subject spans the width),
# "question" (text card, no image). All 7 ep11 images render as "fill".
BEAT_MODES = [
    "fill",
    "fill",
    "fill",
    "fill",
    "fill",
    "fill",
    "fill",
    "question",
]

# Title overlay on beat 1 (first frame): the video thumbnail is the first
# frame, so burn the title here so the homepage thumbnail shows it.
# The hook audio still starts immediately; the title is visual only.
# "Night Stalker" fits at 96pt (same fix as ep09/ep10: short title only).
TITLE_OVERLAY_TEXT = "Night Stalker"
TITLE_OVERLAY_FONTSIZE = 96
TITLE_OVERLAY_Y = 400
TITLE_OVERLAY_DURATION = 6  # seconds (first 6s of beat 1; ep06 used 3s and YouTube's auto-thumbnail missed it)


def load_main_functions():
    """Exec main.py (truncated before __main__) and return its namespace."""
    src = Path("main.py").read_text(encoding="utf-8")
    cut = src.rindex('if __name__ == "__main__":')
    ns = {"__name__": "whispertail_ep11", "__file__": str(Path("main.py").resolve())}
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


def download_file(url, dest_path):
    """Download a URL to dest_path. Raises a clear error on 404."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
    except requests.HTTPError as error:
        status = getattr(getattr(error, "response", None), "status_code", "?")
        if status == 404:
            raise RuntimeError(
                f"Download 404 (not found in repo): {url}\n"
                "For ep11 images, push the 7 files to the staging repo at "
                f"episodes/{EPISODE_ID}/images/<filename> first."
            ) from error
        raise
    if not resp.content:
        raise RuntimeError(f"Downloaded file is empty: {url}")
    dest_path.write_bytes(resp.content)
    print(f"Downloaded: {dest_path} ({dest_path.stat().st_size} bytes)")
    return dest_path


def get_audio_duration(audio_path):
    """Return the duration in seconds of an audio file (ffprobe)."""
    ffprobe_binary = shutil.which("ffprobe")
    if not ffprobe_binary:
        raise RuntimeError("ffprobe must be available on PATH.")
    result = subprocess.run(
        [
            ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {audio_path}: {result.stderr.strip()}"
        )
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"Could not parse duration for {audio_path}.") from error
    if duration <= 0:
        raise RuntimeError(f"Audio has no usable duration: {audio_path}")
    return duration


def escape_filter_path(path):
    """Escape a file path for use inside an ffmpeg filter argument."""
    return (
        str(Path(path).resolve())
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )

# ---------------------------------------------------------------------------
# Beat boundaries from Whisper SRT
# ---------------------------------------------------------------------------

SRT_TIMESTAMP_LINE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}$"
)

# Anchor phrases (normalized) marking the start / end of each beat's
# narration. Chosen to avoid numbers Whisper may transcribe differently
# ("1971" vs "nineteen seventy-one", "1980" vs "nineteen eighty") and to
# be unique in the script. Beat 8 has no end anchor: it runs to the end
# of the audio.
BEAT_ANCHORS = [
    ("imagine an entire city", "felt safe"),            # 1 hook
    ("los angeles lived in fear", "night stalker"),      # 2 summer 1985
    ("he slipped into homes", "each other"),             # 3 random victims
    ("for more than a year", "could see"),               # 4 no leads
    ("then one fingerprint", "richard ramirez"),         # 5 fingerprint
    ("his photograph ran", "caught him themselves"),     # 6 caught
    ("he was convicted", "never executed"),              # 7 convicted
    ("stopped waiting", None),                           # 8 ending question
]


def normalize_text(text):
    """Lowercase, strip punctuation, collapse whitespace (for matching)."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def srt_time_to_seconds(timestamp):
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", timestamp.strip())
    if not match:
        raise RuntimeError(f"Invalid SRT timestamp: {timestamp}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt_cues(srt_path):
    """Parse an SRT file into [(start_sec, end_sec, normalized_text)]."""
    text = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    cues = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        if (
            len(lines) < 3
            or not lines[0].isdigit()
            or not SRT_TIMESTAMP_LINE.fullmatch(lines[1])
        ):
            continue
        start, end = (part.strip() for part in lines[1].split("-->"))
        caption = " ".join(lines[2:]).strip()
        if not caption:
            continue
        cues.append(
            (
                srt_time_to_seconds(start),
                srt_time_to_seconds(end),
                normalize_text(caption),
            )
        )
    if not cues:
        raise RuntimeError(f"No subtitle cues found in {srt_path}.")
    return cues


def estimate_phrase_time(cue_start, cue_end, cue_text, anchor_phrase, want_end=True):
    """Estimate when an anchor phrase starts/ends inside one SRT cue.

    Whisper SRT gives only cue-level timestamps, so words inside a cue are
    assumed evenly spaced. Finds the anchor word sequence in the cue's
    words and returns the estimated time of its end (want_end=True) or its
    start (want_end=False). Returns None if the phrase is not in the cue.
    This matters because a beat boundary can fall mid-cue (e.g. beat 4/5
    split one sentence), where cue end/start times would collapse two
    beats into one.
    """
    cue_words = normalize_text(cue_text).split()
    anchor_words = normalize_text(anchor_phrase).split()
    if not anchor_words or not cue_words:
        return None
    n = len(anchor_words)
    for i in range(len(cue_words) - n + 1):
        if cue_words[i:i + n] == anchor_words:
            if want_end:
                frac = (i + n) / len(cue_words)
            else:
                frac = i / len(cue_words)
            return cue_start + frac * (cue_end - cue_start)
    return None


def compute_beat_boundaries(srt_path, audio_duration):
    """Return 8 (start, end) second-ranges, one per visual beat.

    Each boundary between beat k and beat k+1 is the estimated time when
    beat k's END anchor phrase finishes inside its SRT cue (word-position
    estimate, so a boundary can fall mid-cue). If that anchor is not
    found, fall back to the estimated start of beat k+1's START anchor
    phrase; if neither is found, interpolate linearly between the nearest
    known boundaries. Beat 1 starts at 0, beat 8 ends at the audio duration.
    """
    cues = parse_srt_cues(srt_path)
    n = len(BEAT_ANCHORS)
    audio_duration = float(audio_duration)

    def find_phrase_time(phrase, want_end):
        if phrase is None:
            return None
        for (cue_start, cue_end, cue_text) in cues:
            t = estimate_phrase_time(cue_start, cue_end, cue_text, phrase, want_end)
            if t is not None:
                return t
        return None

    boundaries = [0.0] * (n + 1)
    boundaries[0] = 0.0
    boundaries[n] = audio_duration
    found = {}
    for k in range(1, n):
        # Boundary k sits between beat k-1 (0-indexed) and beat k.
        t = find_phrase_time(BEAT_ANCHORS[k - 1][1], want_end=True)
        if t is None:
            t = find_phrase_time(BEAT_ANCHORS[k][0], want_end=False)
        if t is not None:
            boundaries[k] = t
            found[k] = boundaries[k]

    # Interpolate any boundary that could not be matched.
    for k in range(1, n):
        if k in found:
            continue
        left_k = max([j for j in range(0, k) if j == 0 or j in found], default=0)
        right_options = [j for j in range(k + 1, n + 1) if j == n or j in found]
        right_k = min(right_options) if right_options else n
        left_t = boundaries[left_k]
        right_t = boundaries[right_k]
        frac = (k - left_k) / (right_k - left_k)
        boundaries[k] = left_t + frac * (right_t - left_t)
        print(
            f"  WARNING: beat boundary {k} could not be matched from the "
            f"subtitles; interpolated to {boundaries[k]:.2f}s."
        )

    # Enforce strictly increasing boundaries with a 0.5s minimum per beat,
    # keeping room for the beats that follow.
    for k in range(1, n):
        min_t = boundaries[k - 1] + 0.5
        max_t = audio_duration - 0.5 * (n - k)
        if boundaries[k] < min_t:
            boundaries[k] = min_t
        if boundaries[k] > max_t:
            boundaries[k] = max_t
    boundaries[n] = audio_duration

    if len(found) < n - 1:
        print(
            "  WARNING: only "
            f"{len(found)}/{n - 1} beat boundaries were matched from the "
            "subtitles. Check the printed beat times before publishing."
        )
    return [(boundaries[i], boundaries[i + 1]) for i in range(n)]


# ---------------------------------------------------------------------------
# Question card (beat 8)
# ---------------------------------------------------------------------------

QUESTION_SETUP = (
    "A city that could not sleep finally did. "
    "So tell me —"
)
QUESTION_TEXT = "would you have recognized him?"


def write_question_card_texts(workdir):
    """Write the two drawtext files for the ending card.

    Returns (setup_path, question_path, setup_lines, question_lines).
    """
    setup_lines = textwrap.wrap(
        QUESTION_SETUP, width=34, break_long_words=True, break_on_hyphens=False
    )
    question_lines = textwrap.wrap(
        QUESTION_TEXT, width=24, break_long_words=True, break_on_hyphens=False
    )
    setup_path = Path(workdir) / "ep11_question_setup.txt"
    question_path = Path(workdir) / "ep11_question.txt"
    setup_path.write_text("\n".join(setup_lines) + "\n", encoding="utf-8")
    question_path.write_text("\n".join(question_lines) + "\n", encoding="utf-8")
    return setup_path, question_path, len(setup_lines), len(question_lines)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def filter_ass_before(ass_path, cutoff_seconds, workdir):
    """Return an ASS file with Dialogue cues starting at/after the cutoff
    removed. Used to keep subtitles off the ending question card (beat 8):
    the card itself displays those exact words, and the yellow subtitle
    would overlap the card text. Returns the original path if nothing was
    removed.
    """
    ass_path = Path(ass_path)
    text = ass_path.read_text(encoding="utf-8")
    ass_time = re.compile(r"^Dialogue: 0,(\d+):(\d{2}):(\d{2})\.(\d{2}),")
    kept = []
    removed = 0
    for line in text.splitlines():
        match = ass_time.match(line)
        if match:
            hours, minutes, seconds, centiseconds = map(int, match.groups())
            start = hours * 3600 + minutes * 60 + seconds + centiseconds / 100.0
            # 0.1s tolerance: a cue starting exactly on the boundary can
            # compare a hair under it in floating point (contiguous cues).
            if start >= cutoff_seconds - 0.1:
                removed += 1
                continue
        kept.append(line)
    if removed == 0:
        return ass_path
    out_path = Path(workdir) / (ass_path.stem + "_noq8.ass")
    out_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"  Removed {removed} subtitle cue(s) overlapping the question card.")
    return out_path


def render_ep11_video(
    beat_images,
    audio_path,
    ass_path,
    output_path,
    beat_boundaries,
    audio_duration,
    workdir,
):
    """Render the 8-beat image-sequence video with ffmpeg.

    beat_images: list of 8 entries; the first 7 are image Paths, the last
      is None (question card). beat_boundaries: list of 8 (start, end)
      second-ranges. The video is cut to the audio duration via -shortest.
    Beat 1 also carries a title text overlay (first 6s, large) so the
      video thumbnail shows the title.
    """
    workdir = Path(workdir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(beat_images) != 8 or len(beat_boundaries) != 8:
        raise RuntimeError("ep11 needs exactly 8 beats.")
    if len(BEAT_MODES) != 8:
        raise RuntimeError("BEAT_MODES must list exactly 8 modes.")

    ffmpeg_binary = shutil.which("ffmpeg")
    if not ffmpeg_binary:
        raise RuntimeError("ffmpeg must be available on PATH.")
    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise RuntimeError(f"Voiceover audio was not found: {audio_path}")
    ass_path = Path(ass_path)
    if not ass_path.is_file() or ass_path.stat().st_size == 0:
        raise RuntimeError(f"Subtitle file was not found: {ass_path}")

    # Question-card text layout: center the two text blocks vertically.
    setup_path, question_path, setup_line_count, question_line_count = (
        write_question_card_texts(workdir)
    )
    setup_fontsize = 44
    question_fontsize = 76
    setup_line_h = setup_fontsize + 10
    question_line_h = question_fontsize + 12
    gap = 60
    total_h = (
        setup_line_count * setup_line_h + gap + question_line_count * question_line_h
    )
    y_setup = int((VIDEO_HEIGHT - total_h) / 2)
    y_question = y_setup + setup_line_count * setup_line_h + gap

    beat8_start, beat8_end = beat_boundaries[7]
    beat8_dur = max(beat8_end - beat8_start, 0.5)

    # Keep subtitles off the question card (beat 8): the card already
    # displays those words, and the yellow subtitle would overlap it.
    ass_path = filter_ass_before(ass_path, beat8_start, workdir)

    input_args = []
    filter_chains = []
    for i in range(8):
        start, end = beat_boundaries[i]
        dur = max(end - start, 0.5)
        frames = int(round(dur * VIDEO_FPS))
        if i == 7:
            # Last beat: round up + a small buffer so the video is never
            # shorter than the audio; -shortest trims the excess.
            frames = int(math.ceil(dur * VIDEO_FPS)) + 15
        frames = max(frames, 15)
        zoom_inc = 0.08 / max(frames - 1, 1)
        zoompan = (
            f"zoompan=z='min(zoom+{zoom_inc:.6f},1.08)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS}"
        )
        mode = BEAT_MODES[i]
        if mode == "fill_top":
            # Crop to the top two-thirds first (sky/lightning; avoids the
            # city buildings along the bottom edge), then fill + zoom.
            chain = (
                f"[{i}:v]crop=iw:ih*2/3:0:0,"
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "force_original_aspect_ratio=increase,"
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                f"{zoompan},setsar=1[v{i}]"
            )
        elif mode == "pillarbox":
            # Low-res archival image: scale to fit inside the frame and
            # center on black. Never upscale to full-bleed.
            chain = (
                f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "(ow-iw)/2:(oh-ih)/2:color=black,"
                f"{zoompan},setsar=1[v{i}]"
            )
        elif mode == "fill":
            chain = (
                f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "force_original_aspect_ratio=increase,"
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                f"{zoompan},setsar=1[v{i}]"
            )
        elif mode == "question":
            chain = (
                f"[{i}:v]"
                f"drawtext=textfile='{escape_filter_path(setup_path)}':"
                "font='DejaVu Sans':"
                f"fontsize={setup_fontsize}:fontcolor=white:"
                "borderw=2:bordercolor=black:"
                f"x=(w-text_w)/2:y={y_setup}:line_spacing=10:expansion=none,"
                f"drawtext=textfile='{escape_filter_path(question_path)}':"
                "font='DejaVu Sans':"
                f"fontsize={question_fontsize}:fontcolor=0xD0A030:"
                "borderw=3:bordercolor=black:box=1:boxcolor=black@0.5:"
                "boxborderw=20:"
                f"x=(w-text_w)/2:y={y_question}:line_spacing=12:expansion=none,"
                f"setsar=1[v{i}]"
            )
        else:
            raise RuntimeError(f"Unknown beat mode: {mode}")
        if i == 0:
            # Title overlay on the first frame (thumbnail): white title,
            # upper-middle, first 6s, large + bold border so YouTube's
            # auto-thumbnail catches a frame with the title. The hook
            # audio is untouched. (ep06 used 3s/72pt and YouTube missed it.)
            title_drawtext = (
                f"drawtext=text='{TITLE_OVERLAY_TEXT}':font='DejaVu Sans':"
                f"fontsize={TITLE_OVERLAY_FONTSIZE}:fontcolor=white:"
                "borderw=3:bordercolor=black:"
                "shadowcolor=black@0.5:shadowx=2:shadowy=2:"
                "x=(w-text_w)/2:"
                f"y={TITLE_OVERLAY_Y}:"
                f"enable='between(t,0,{TITLE_OVERLAY_DURATION})':expansion=none"
            )
            chain = chain.replace(
                f",setsar=1[v{i}]", f",{title_drawtext},setsar=1[v{i}]"
            )
        filter_chains.append(chain)

    # Inputs: 7 images (0-6), lavfi black for the question card (7), audio (8).
    for i in range(7):
        img = beat_images[i]
        if img is None:
            raise RuntimeError(f"Beat {i + 1} needs an image file.")
        img = Path(img)
        if not img.is_file() or img.stat().st_size == 0:
            raise RuntimeError(f"Beat image was not found: {img}")
        input_args += ["-i", str(img)]
    input_args += [
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}:d={beat8_dur:.3f}",
    ]
    audio_index = 8
    input_args += ["-i", str(audio_path)]

    concat_inputs = "".join(f"[v{i}]" for i in range(8))
    subtitle_filter = f"subtitles=filename='{escape_filter_path(ass_path)}'"
    filter_complex = ";".join(filter_chains)
    filter_complex += (
        f";{concat_inputs}concat=n=8:v=1:a=0,"
        f"fps={VIDEO_FPS},{subtitle_filter},format=yuv420p[video]"
    )

    temporary_output = output_path.with_name(
        f"{output_path.stem}.tmp{output_path.suffix}"
    )
    command = [
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        "[video]",
        "-map",
        f"{audio_index}:a:0",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(temporary_output),
    ]

    print(f"Rendering {output_path.name} (8 beats, {audio_duration:.1f}s audio)...")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(300, int(audio_duration * 10)),
            check=False,
        )
        if result.returncode != 0:
            details = result.stderr.strip()[-4000:] or "Unknown FFmpeg error."
            raise RuntimeError(f"FFmpeg could not create the video: {details}")

        output_duration = get_audio_duration(temporary_output)
        temporary_output.replace(output_path)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        temporary_output.unlink(missing_ok=True)
        raise

    print(
        f"Rendered: {output_path} ({output_path.stat().st_size} bytes, "
        f"{output_duration:.1f}s, {VIDEO_WIDTH}x{VIDEO_HEIGHT})"
    )
    return output_path

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Read the manifest; skip everything if ep11 is already scheduled.
    manifest_sha, manifest_text = github_get_file(MANIFEST_PATH)
    if manifest_text is None:
        raise RuntimeError(f"Manifest not found in GitHub: {MANIFEST_PATH}")
    manifest = json.loads(manifest_text)
    episodes = manifest.get("episodes") or []
    episode = next(
        (ep for ep in episodes if ep.get("id") == EPISODE_ID), None
    )
    if (
        episode
        and episode.get("buffer_tiktok_post_id")
        and episode.get("buffer_youtube_post_id")
    ):
        print(
            f"{EPISODE_ID} is already scheduled "
            f"(TikTok {episode['buffer_tiktok_post_id']}, "
            f"YouTube {episode['buffer_youtube_post_id']}). Nothing to do."
        )
        return 0

    if episode is None:
        episode = {
            "id": EPISODE_ID,
            "title": TITLE,
            "description": DESCRIPTION,
            "hashtags": HASHTAGS,
            "audio": f"episodes/{EPISODE_ID}/voiceover.mp3",
            "status": "ready",
        }
        episodes.append(episode)
        manifest["episodes"] = episodes
        manifest_sha = save_manifest(
            manifest, manifest_sha, f"WhisperTail {EPISODE_ID}: added to queue"
        )
    print(f"Episode: {EPISODE_ID} | {TITLE}")

    # 2. Download the voiceover + the 7 images from GitHub raw.
    workdir = Path("generated-artifacts") / "ep11"
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = download_file(
        f"{GITHUB_RAW_BASE}/episodes/{EPISODE_ID}/voiceover.mp3",
        workdir / "voiceover.mp3",
    )
    beat_images = []
    for filename in IMAGE_FILENAMES:
        beat_images.append(
            download_file(
                f"{GITHUB_RAW_BASE}/episodes/{EPISODE_ID}/images/{filename}",
                workdir / filename,
            )
        )
    beat_images.append(None)  # beat 8 is the question card, no image
    audio_duration = get_audio_duration(audio_path)
    print(f"Voiceover duration: {audio_duration:.2f}s")

    # 3. Whisper-transcribe (main.py) and compute the beat boundaries.
    ns = load_main_functions()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from Replit Secrets.")
    client = OpenAI(api_key=api_key, timeout=120)
    print("Step 1/2: Whisper captions...")
    subtitle_path = ns["transcribe_audio"](
        str(audio_path), client, output_path=workdir / "voiceover.srt"
    )
    beat_boundaries = compute_beat_boundaries(subtitle_path, audio_duration)
    for i, (start, end) in enumerate(beat_boundaries):
        print(f"  Beat {i + 1}: {start:.2f}s - {end:.2f}s ({end - start:.2f}s)")

    # 4. Render the image-sequence video.
    print("Step 2/2: Render video...")
    ass_path = ns["create_ass_subtitles"](
        subtitle_path, workdir / "voiceover.ass"
    )
    video_path = render_ep11_video(
        beat_images,
        audio_path,
        ass_path,
        workdir / "final_video.mp4",
        beat_boundaries,
        audio_duration,
        workdir,
    )
    print(f"Rendered: {video_path} ({video_path.stat().st_size} bytes)")

    # 5. Upload the finished video back to GitHub (stable public link).
    video_rel = f"episodes/{EPISODE_ID}/final_video.mp4"
    existing_sha, _ = github_get_file(video_rel)
    github_put_file(
        video_rel,
        video_path.read_bytes(),
        f"WhisperTail {EPISODE_ID}: rendered video (image-sequence)",
        sha=existing_sha,
    )
    video_url = f"{GITHUB_RAW_BASE}/{video_rel}"
    print(f"Uploaded video to GitHub: {video_url}")
    episode["video_url"] = video_url
    manifest_sha = save_manifest(
        manifest, manifest_sha, f"WhisperTail {EPISODE_ID}: video rendered"
    )

    # 6. Schedule on TikTok + YouTube via Buffer (later today, not now).
    # Resume-safe: a post already recorded in the manifest is skipped, so a
    # rerun never creates a duplicate.
    due_at = (
        datetime.now(timezone.utc) + timedelta(hours=PUBLISH_DELAY_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    channels = buffer_channels()
    tiktok = channels.get("tiktok")
    youtube = channels.get("youtube")
    if not tiktok or not youtube:
        raise RuntimeError(
            f"Buffer is missing a channel (tiktok={bool(tiktok)}, "
            f"youtube={bool(youtube)})."
        )
    caption = f"{TITLE}\n\n{DESCRIPTION}\n\n{HASHTAGS}".strip()
    yt_text = f"{DESCRIPTION}\n\n{HASHTAGS}".strip()
    yt_meta = {
        "title": TITLE,
        "categoryId": YOUTUBE_CATEGORY_ID,
        "privacy": "public",
        "madeForKids": False,
    }
    if episode.get("buffer_tiktok_post_id"):
        print(
            f"TikTok already scheduled: post {episode['buffer_tiktok_post_id']} "
            "(skipped)"
        )
    else:
        tiktok_post = buffer_create_post(tiktok["id"], caption, video_url, due_at)
        print(
            f"TikTok scheduled: post {tiktok_post['id']} "
            f"due {tiktok_post.get('dueAt')}"
        )
        episode["buffer_tiktok_post_id"] = tiktok_post["id"]
        episode["due_at"] = due_at
        manifest_sha = save_manifest(
            manifest, manifest_sha, f"WhisperTail {EPISODE_ID}: TikTok scheduled"
        )
    if episode.get("buffer_youtube_post_id"):
        print(
            f"YouTube already scheduled: post {episode['buffer_youtube_post_id']} "
            "(skipped)"
        )
    else:
        youtube_post = buffer_create_post(
            youtube["id"], yt_text, video_url, due_at, youtube_meta=yt_meta
        )
        print(
            f"YouTube scheduled: post {youtube_post['id']} "
            f"due {youtube_post.get('dueAt')}"
        )
        episode["buffer_youtube_post_id"] = youtube_post["id"]
        episode["due_at"] = due_at
        manifest_sha = save_manifest(
            manifest, manifest_sha, f"WhisperTail {EPISODE_ID}: YouTube scheduled"
        )

    # 7. Mark the episode scheduled in the manifest.
    episode["status"] = "scheduled"
    episode["video_url"] = video_url
    manifest_sha = save_manifest(
        manifest, manifest_sha, f"WhisperTail {EPISODE_ID}: scheduled"
    )
    print(f"DONE: {EPISODE_ID} scheduled for {due_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
