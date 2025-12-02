# YouTube AI Scraping Agent

CLI tool that takes a YouTube song URL, detects the artist, finds their first studio album using a local LLM, and tries to fetch lyrics for all tracks via Apify + genius.com.

---

## Architecture

1. **YouTube → Artist**
   Uses Apify actor `streamers~youtube-scraper` to get the channel/artist name from a YouTube video URL.

2. **Artist → First Album & Tracks (Local LLM)**
   Uses a local open-source LLM via **Ollama** (default model: `llama3.2:1b`) to:

   * Identify the artist’s first official studio album.
   * Return the full track list in JSON.

3. **Tracks → Lyrics (Apify + genius.com)**

   * Calls a custom Apify actor (e.g. `youruser~genius-free-lyrics`) which scrapes **genius.com** for each track.
   * No external lyrics APIs are used.
   * If genius.com returns `403` or similar, the actor returns empty lyrics and an `error` field instead of failing.

The final JSON contains the artist, album info, and per-track Genius URL + raw lyrics text (if available).

---

## Requirements

* Python **3.10+**
* [Ollama](https://ollama.com/) running locally
* An Apify account + token
* Your custom `genius-free-lyrics` Apify actor (Python) deployed

Python deps (from `requirements.txt`): `requests`

---

## Setup

1. **Clone & install**

```bash
git clone <repo-url>
cd youtube-agent
pip install -r requirements.txt
```

2. **Run Ollama and pull a model**

```bash
ollama pull llama3.2:1b
# Ollama listens on http://localhost:11434 by default
```

3. **Create `.env`**

```env
# Required
APIFY_TOKEN=apify_api_xxx

# Optional
OLLAMA_URL=http://localhost:11434

# Actor IDs (you can override these)
YOUTUBE_ACTOR_ID=streamers~youtube-scraper
GENIUS_ACTOR_ID=youruser~genius-free-lyrics
```

Do **not** commit your real `.env`. Add `.env` to `.gitignore`.
You can also set these as environment variables instead of using a `.env` file.

---

## Usage

From the project root:

```bash
python -m src.main "https://www.youtube.com/watch?v=X27IfAgzhTY&list=RDX27IfAgzhTY&start_radio=1"
```

Example JSON output (simplified):

```json
{
  "input_youtube_url": "...",
  "artist_from_youtube": "Scorpions",
  "album": {
    "artist": "Scorpions",
    "album_title": "Lovedrive",
    "release_year": 1979
  },
  "tracks": [
    {
      "song_title": "Lovedrive",
      "genius_url": "https://genius.com/...",
      "lyrics_raw": "...",
      "error": null
    }
  ]
}
```

Fields:

* `artist_from_youtube` – artist inferred from the YouTube video
* `album` – album metadata from the LLM
* `tracks[]`:

  * `song_title` – track name
  * `genius_url` – Genius page or search URL chosen by the Apify actor
  * `lyrics_raw` – raw lyrics text (may be empty)
  * `error` – error message if scraping failed (e.g., `status=403`)

---

## Notes & Limitations

* genius.com may block requests (HTTP 403). In that case:

  * `lyrics_raw` is empty,
  * `error` explains the failure,
  * The agent still returns structured JSON (no crash).
* The local LLM may be imperfect on discography details. For production, combine with a trusted music database.
