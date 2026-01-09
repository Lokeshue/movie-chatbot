import os
import random
import datetime as dt
import requests
import streamlit as st

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"

# Indian languages on TMDB:
# hi (Hindi), ta (Tamil), te (Telugu), ml (Malayalam), kn (Kannada)
INDIAN_LANG_CODES = ["hi", "ta", "te", "ml", "kn"]


def tmdb_get(path, params=None):
    if not TMDB_API_KEY:
        raise RuntimeError("Missing TMDB_API_KEY environment variable.")
    params = params or {}
    params["api_key"] = TMDB_API_KEY
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_genre_map():
    data = tmdb_get("/genre/movie/list", {"language": "en-US"})
    return {g["name"].lower(): g["id"] for g in data.get("genres", [])}


def search_person_id(name: str):
    name = (name or "").strip()
    if not name:
        return None
    data = tmdb_get("/search/person", {"query": name, "include_adult": "false"})
    results = data.get("results", [])
    return results[0]["id"] if results else None


def discover_movies(
    *,
    start_date: str,
    end_date: str,
    page: int = 1,
    genre_id: int | None = None,
    with_original_language: str | None = None,
    region: str | None = None,
    sort_by: str = "popularity.desc",
):
    """
    TMDB Discover endpoint.
    We keep date range locked to last 20 years.
    We also add a small vote_count filter so results aren't too "new/empty".
    """
    params = {
        "include_adult": "false",
        "include_video": "false",
        "language": "en-US",
        "sort_by": sort_by,
        "page": page,
        "primary_release_date.gte": start_date,
        "primary_release_date.lte": end_date,
        "vote_count.gte": 50,  # helps reduce extremely new / low-info movies
    }
    if genre_id:
        params["with_genres"] = genre_id
    if with_original_language:
        params["with_original_language"] = with_original_language
    if region:
        params["region"] = region

    return tmdb_get("/discover/movie", params)


def movies_by_actor(person_id: int, start_date: str, end_date: str, pages: int = 2):
    movies = []
    for p in range(1, pages + 1):
        data = tmdb_get(
            "/discover/movie",
            {
                "with_cast": person_id,
                "include_adult": "false",
                "sort_by": "popularity.desc",
                "primary_release_date.gte": start_date,
                "primary_release_date.lte": end_date,
                "page": p,
                "vote_count.gte": 20,
            },
        )
        movies.extend(data.get("results", []))
    return movies


def unique_movies(movies):
    seen = set()
    out = []
    for m in movies:
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            out.append(m)
    return out


def year_from_date(d: str) -> int | None:
    if not d:
        return None
    try:
        return int(d[:4])
    except Exception:
        return None


def balanced_sample_by_year(movies, n: int) -> list:
    """
    Avoid getting only 2025/2024 by sampling across years.
    Simple approach:
    - group by year
    - shuffle each year
    - round-robin pick across years (most recent to older)
    """
    movies = unique_movies(movies)
    buckets = {}
    for m in movies:
        y = year_from_date(m.get("release_date", ""))
        if y is None:
            continue
        buckets.setdefault(y, []).append(m)

    if not buckets:
        random.shuffle(movies)
        return movies[:n]

    years = sorted(buckets.keys(), reverse=True)
    for y in years:
        random.shuffle(buckets[y])

    picked = []
    idx = 0
    while len(picked) < n:
        made_progress = False
        for y in years:
            if idx < len(buckets[y]):
                picked.append(buckets[y][idx])
                made_progress = True
                if len(picked) >= n:
                    break
        if not made_progress:
            break
        idx += 1

    return picked[:n]


def label_movie(m, indian_lang_set):
    lang = (m.get("original_language") or "").lower()
    return "🇮🇳 Indian" if lang in indian_lang_set else "🌍 Global"


def main():
    st.set_page_config(page_title="Movie Reco Chatbot", page_icon="🎬", layout="centered")
    st.title("🎬 Movie Recommendation Chatbot ")
    st.caption("Leave blank or type 'surprise me'. Recommendations are from the last 20 years.")

    if not TMDB_API_KEY:
        st.error("TMDB_API_KEY is not set. In terminal: export TMDB_API_KEY='YOUR_KEY'")
        st.stop()

    today = dt.date.today()
    start_20yrs = today.replace(year=today.year - 20).isoformat()
    end_today = today.isoformat()

    genre_map = get_genre_map()
    indian_lang_set = set(INDIAN_LANG_CODES)

    st.subheader("🗣️ Questions")
    fav_movie = st.text_input("1) Favorite movie (optional)", placeholder="Interstellar / Vikram / surprise me")
    fav_actor = st.text_input("2) Favorite hero/actor (optional)", placeholder="Vijay / Suriya / Shah Rukh Khan / Leonardo DiCaprio")
    fav_song = st.text_input("3) Favorite movie song (optional)", placeholder="Naatu Naatu / Arabic Kuthu / surprise me")
    genre = st.text_input("4) Favorite genre (optional)", placeholder="Action, Comedy, Romance, Thriller, Sci-Fi...")

    count = st.slider("How many recommendations?", 6, 20, 10)
    st.caption("We’ll do roughly half Indian + half Global.")

    if st.button("Get Recommendations 🍿"):
        def normalize(x):
            x = (x or "").strip()
            return "" if x.lower() in ["surprise me", "idk", "i don't know", "dont know", "random"] else x

        fav_movie = normalize(fav_movie)
        fav_actor = normalize(fav_actor)
        fav_song = normalize(fav_song)
        genre = normalize(genre)

        genre_id = genre_map.get(genre.lower()) if genre else None

        # 1) Pull Indian pool (mix multiple Indian languages)
        indian_pool = []
        for lang in INDIAN_LANG_CODES:
            for p in range(1, 3):  # 2 pages per language
                data = discover_movies(
                    start_date=start_20yrs,
                    end_date=end_today,
                    page=p,
                    genre_id=genre_id,
                    with_original_language=lang,
                )
                indian_pool.extend(data.get("results", []))

        # 2) Pull global pool (use region-based discovery for variety)
        # NOTE: We intentionally do NOT use without_original_language because it can be inconsistent.
        # Instead we fetch global lists from different regions and later filter out Indian languages.
        global_pool = []
        for region in ["US", "KR", "JP", "FR", "ES"]:
            for p in range(1, 3):
                data = discover_movies(
                    start_date=start_20yrs,
                    end_date=end_today,
                    page=p,
                    genre_id=genre_id,
                    region=region,
                )
                global_pool.extend(data.get("results", []))

        # Filter global pool to exclude Indian languages
        global_pool = [m for m in global_pool if (m.get("original_language") or "").lower() not in indian_lang_set]

        # 3) If actor provided, boost (add actor movies into both pools)
        if fav_actor:
            pid = search_person_id(fav_actor)
            if pid:
                actor_movies = movies_by_actor(pid, start_20yrs, end_today, pages=3)
                indian_pool.extend([m for m in actor_movies if (m.get("original_language") or "").lower() in indian_lang_set])
                global_pool.extend([m for m in actor_movies if (m.get("original_language") or "").lower() not in indian_lang_set])

        # 4) Balanced sampling to avoid only 2025/2024
        half = count // 2
        indian_pick = balanced_sample_by_year(indian_pool, half)
        global_pick = balanced_sample_by_year(global_pool, count - half)

        final = unique_movies(indian_pick + global_pick)

        st.subheader("✅ Recommendations")
        for m in final[:count]:
            title = m.get("title", "Untitled")
            date = m.get("release_date", "")
            rating = m.get("vote_average", "N/A")
            overview = m.get("overview", "")
            year = date[:4] if date else "—"
            tag = label_movie(m, indian_lang_set)

            st.markdown(f"### {title} ({year}) — {tag}")
            st.write(f"⭐ Rating: {rating}")
            if overview:
                st.write(overview)

        st.info("Tip: Try typing only a genre like 'Action' or just click without typing anything for a mixed surprise list.")


if __name__ == "__main__":
    main()
