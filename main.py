import os
import sys
import json
import textwrap
from typing import Any, Dict, List
from dotenv import load_dotenv
load_dotenv()

import requests

# -------------------------------------------------
# CONFIGURATION
# -------------------------------------------------

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
if not APIFY_TOKEN:
    raise RuntimeError("APIFY_TOKEN environment variable must be set.")

APIFY_BASE_URL = "https://api.apify.com/v2/acts"

YOUTUBE_ACTOR_ID = os.getenv("YOUTUBE_ACTOR_ID", "")
GENIUS_ACTOR_ID = os.getenv("GENIUS_ACTOR_ID", "")

LOCAL_LLM_MODEL = "llama3.2:1b"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


# -------------------------------------------------
# APIFY / LLM HELPERS
# -------------------------------------------------


def call_apify_actor(actor_id: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = f"{APIFY_BASE_URL}/{actor_id}/run-sync-get-dataset-items"
    params = {"token": APIFY_TOKEN}
    resp = requests.post(url, params=params, json=payload, timeout=600)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"[APIFY ERROR] {resp.status_code} {resp.text[:500]}", file=sys.stderr)
        raise e
    return resp.json()


def call_ollama_json(prompt: str, model: str = LOCAL_LLM_MODEL) -> Dict[str, Any]:
    url = f"{OLLAMA_URL}/api/generate"
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    resp = requests.post(url, json=body, timeout=600)
    resp.raise_for_status()
    data = resp.json()
    text = data.get("response", "").strip()

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Ollama JSON: {e}\nRaw: {text[:500]}")


# -------------------------------------------------
# STEP 1: YOUTUBE → ARTIST NAME
# -------------------------------------------------


def get_artist_from_youtube(youtube_url: str) -> str:
    payload = {
        "startUrls": [{"url": youtube_url}],
        "maxResults": 1,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }

    items = call_apify_actor(YOUTUBE_ACTOR_ID, payload)
    if not items:
        raise RuntimeError("YouTube Scraper returned no items.")

    first = items[0]

    artist = (
        first.get("channelTitle")
        or first.get("channelName")
        or first.get("uploader")
        or first.get("author")
        or ""
    ).strip()

    if not artist:
        raise RuntimeError(f"Could not extract artist name from YouTube output: {first}")

    return artist


# -------------------------------------------------
# STEP 2: LLM → FIRST ALBUM & TRACK LIST
# -------------------------------------------------


def get_first_album_from_llm(artist_name: str) -> Dict[str, Any]:
    prompt = textwrap.dedent(
        f"""
        You are a music expert.

        For the artist "{artist_name}", identify their FIRST official studio album
        (ignore EPs, live albums, compilations, reissues). Then list all songs in
        that album in correct order.

        You MUST use real, known song titles for this artist. Do NOT invent
        placeholders like "Song 1" or tracks from unrelated artists or albums.

        Return ONLY JSON with this exact structure (no explanations, no markdown):

        {{
          "artist": "Scorpions",
          "album_title": "Lovedrive",
          "release_year": 1979,
          "tracks": [
            "Loving You Sunday Morning",
            "Another Piece of Meat",
            "Always Somewhere"
          ]
        }}

        - artist: normalized artist name
        - album_title: album title
        - release_year: integer year
        - tracks: array of song titles (strings), in album order
        """
    ).strip()

    data = call_ollama_json(prompt)
    if "tracks" not in data or not isinstance(data["tracks"], list):
        raise RuntimeError(f"LLM output is not in the expected format: {data}")

    return data


# -------------------------------------------------
# STEP 3: GENIUS LYRICS VIA APIFY ACTOR
# -------------------------------------------------


def fetch_lyrics_from_genius(song_title: str, artist_name: str) -> Dict[str, Any]:
    search_query = f"{song_title} {artist_name} lyrics"

    payload = {
        "searchQuery": search_query,
        "maxSongs": 1,
        "start_urls": [{"url": "https://genius.com"}],
    }

    try:
        items = call_apify_actor(GENIUS_ACTOR_ID, payload)
    except Exception as e:
        return {
            "song_title": song_title,
            "genius_url": None,
            "lyrics_raw": "",
            "error": f"Apify call failed: {e}",
        }

    if not items:
        return {
            "song_title": song_title,
            "genius_url": None,
            "lyrics_raw": "",
            "error": "No items returned from genius-free-lyrics actor",
        }

    item = items[0]
    return {
        "song_title": song_title,
        "genius_url": item.get("url"),
        "lyrics_raw": item.get("lyricsText") or "",
        "error": item.get("error"),
    }


# -------------------------------------------------
# STEP 4: BUILD FINAL AGENT OUTPUT
# -------------------------------------------------


def build_agent_output(youtube_url: str) -> Dict[str, Any]:
    artist_name = get_artist_from_youtube(youtube_url)
    print(f"[INFO] Artist from YouTube: {artist_name}", file=sys.stderr)

    album_info = get_first_album_from_llm(artist_name)
    print(
        f"[INFO] First album from LLM: {album_info.get('album_title')} "
        f"({album_info.get('release_year')})",
        file=sys.stderr,
    )

    tracks = album_info.get("tracks", [])
    lyrics_results: List[Dict[str, Any]] = []

    for t in tracks:
        t_str = str(t).strip()
        if not t_str:
            continue
        print(f"[INFO] Fetching lyrics for: {t_str}", file=sys.stderr)
        lyrics_info = fetch_lyrics_from_genius(t_str, artist_name)
        lyrics_results.append(lyrics_info)

    return {
        "input_youtube_url": youtube_url,
        "artist_from_youtube": artist_name,
        "album": {
            "artist": album_info.get("artist"),
            "album_title": album_info.get("album_title"),
            "release_year": album_info.get("release_year"),
        },
        "tracks": lyrics_results,
    }


# -------------------------------------------------
# CLI ENTRYPOINT
# -------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <youtube_url>", file=sys.stderr)
        sys.exit(1)

    youtube_url = sys.argv[1]
    output = build_agent_output(youtube_url)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
