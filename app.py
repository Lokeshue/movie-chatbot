import os
import random
import datetime as dt
import requests
import streamlit as st

# -----------------------------
# Config
# -----------------------------
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"

INDIAN_LANG_CODES = ["hi", "ta", "te", "ml", "kn"]  # Hindi, Tamil, Telugu, Malayalam, Kannada
GLOBAL_REGIONS = ["US", "KR", "JP", "FR", "ES"]     # for variety

SKIP_WORDS = {"skip", "idk", "i don't know", "dont know", "random", "surprise me", ""}

# -----------------------------
# TMDB helpers
# -----------------------------
def tmdb_get(path, params=None):
    if not TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY is missing. Set it in Streamlit Secrets or export locally.")
    params = params or {}
    params["api_key"] = TMDB_API_KEY
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=20)

    # Streamlit Cloud redacts errors; show safe status
    if r.status_code != 200:
        try:
            payload = r.json()
            msg = payload.get("status_message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"TMDB API error {r.status_code}: {msg}")

    return r.json()


@st.cache_data(show_spinner=False)
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


def discover_movies(*, start_date, end_date, page=1, genre_id=None, with_original_language=None, region=None):
    params = {
        "include_adult": "false",
        "include_video": "false",
        "language": "en-US",
        "sort_by": "popularity.desc",
        "page": page,
        "primary_release_date.gte": start_date,
        "primary_release_date.lte": end_date,
        "vote_count.gte": 50,  # avoids super-new / low-signal items
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


def year_from_date(d: str):
    if not d:
        return None
    try:
        return int(d[:4])
    except Exception:
        return None


def balanced_sample_by_year(movies, n: int):
    """Round-robin across years so you don't only get 2025/2024."""
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
        progressed = False
        for y in years:
            if idx < len(buckets[y]):
                picked.append(buckets[y][idx])
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
        idx += 1

    return picked[:n]


def label_movie(m):
    lang = (m.get("original_language") or "").lower()
    return "🇮🇳 Indian" if lang in set(INDIAN_LANG_CODES) else "🌍 Global"


# -----------------------------
# Chat state machine
# -----------------------------
QUESTIONS = [
    ("fav_movie", "Tell me one movie you love (or type **skip**)."),
    ("fav_actor", "Who’s your favorite hero/actor? (or **skip**)"),
    ("fav_song", "Any favorite movie song? (or **skip**)"),
    ("genre", "Pick a genre (Action / Comedy / Romance / Thriller / Sci-Fi / Horror / Drama) or type anything (or **skip**)."),
    ("mix", "Do you want **more Indian**, **more global**, or **50-50**? (default: 50-50)"),
]

def normalize_answer(text: str) -> str:
    t = (text or "").strip()
    if t.lower() in SKIP_WORDS:
        return ""
    return t

def infer_mix(text: str) -> str:
    t = (text or "").lower().strip()
    if "ind" in t:
        return "more_indian"
    if "glob" in t or "holly" in t or "other" in t:
        return "more_global"
    return "50_50"

def build_recommendations(prefs, count=10):
    today = dt.date.today()
    start_20yrs = today.replace(year=today.year - 20).isoformat()
    end_today = today.isoformat()

    genre_map = get_genre_map()
    genre_text = (prefs.get("genre") or "").lower().strip()
    genre_id = genre_map.get(genre_text) if genre_text else None

    mix_pref = prefs.get("mix", "50_50")
    if mix_pref not in {"50_50", "more_indian", "more_global"}:
        mix_pref = "50_50"

    # Pull Indian pool (multi-language)
    indian_pool = []
    for lang in INDIAN_LANG_CODES:
        for p in range(1, 3):
            data = discover_movies(
                start_date=start_20yrs, end_date=end_today, page=p,
                genre_id=genre_id, with_original_language=lang
            )
            indian_pool.extend(data.get("results", []))

    # Pull global pool (by regions for variety), then exclude Indian languages
    global_pool = []
    for region in GLOBAL_REGIONS:
        for p in range(1, 3):
            data = discover_movies(
                start_date=start_20yrs, end_date=end_today, page=p,
                genre_id=genre_id, region=region
            )
            global_pool.extend(data.get("results", []))

    indian_set = set(INDIAN_LANG_CODES)
    global_pool = [m for m in global_pool if (m.get("original_language") or "").lower() not in indian_set]

    # Actor boost (optional)
    fav_actor = prefs.get("fav_actor", "")
    if fav_actor:
        pid = search_person_id(fav_actor)
        if pid:
            actor_movies = movies_by_actor(pid, start_20yrs, end_today, pages=3)
            indian_pool.extend([m for m in actor_movies if (m.get("original_language") or "").lower() in indian_set])
            global_pool.extend([m for m in actor_movies if (m.get("original_language") or "").lower() not in indian_set])

    # Decide mix ratio
    if mix_pref == "more_indian":
        ind_n = int(round(count * 0.7))
    elif mix_pref == "more_global":
        ind_n = int(round(count * 0.3))
    else:  # 50-50
        ind_n = count // 2
    glob_n = count - ind_n

    indian_pick = balanced_sample_by_year(indian_pool, ind_n)
    global_pick = balanced_sample_by_year(global_pool, glob_n)

    final = unique_movies(indian_pick + global_pick)
    return final[:count]


# -----------------------------
# Streamlit UI (chat)
# -----------------------------
st.set_page_config(page_title="Movie Chatbot", page_icon="🎬", layout="centered")
st.title("🎬 Movie Recommendation Chatbot")
st.caption("Chat with me. Type **skip** anytime. I’ll recommend movies from the last 20 years.")

# Init session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "prefs" not in st.session_state:
    st.session_state.prefs = {}
if "q_index" not in st.session_state:
    st.session_state.q_index = 0
if "done" not in st.session_state:
    st.session_state.done = False

# If first load, bot greets + asks first question
if len(st.session_state.messages) == 0:
    st.session_state.messages.append({"role": "assistant", "content": "Hi! I’ll ask a few quick questions and then recommend movies 🍿"})
    st.session_state.messages.append({"role": "assistant", "content": QUESTIONS[0][1]})

# Render chat history
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# Chat input
user_text = st.chat_input("Type your answer… (or 'skip')")

if user_text is not None and user_text.strip() != "":
    # show user message
    st.session_state.messages.append({"role": "user", "content": user_text})

    # If already done, allow "again" or "reset"
    if st.session_state.done:
        cmd = user_text.strip().lower()
        if cmd in {"reset", "start over", "restart"}:
            st.session_state.messages.append({"role": "assistant", "content": "Cool — starting over."})
            st.session_state.prefs = {}
            st.session_state.q_index = 0
            st.session_state.done = False
            st.session_state.messages.append({"role": "assistant", "content": QUESTIONS[0][1]})
            st.rerun()
        else:
            st.session_state.messages.append({"role": "assistant", "content": "Type **reset** to start over, or share more preferences (actor/genre) and I’ll tune it."})
            st.rerun()

    # store answer for current question
    key, _question = QUESTIONS[st.session_state.q_index]
    ans = normalize_answer(user_text)

    if key == "mix":
        st.session_state.prefs[key] = infer_mix(ans) if ans else "50_50"
    else:
        st.session_state.prefs[key] = ans

    # move to next question or recommend
    st.session_state.q_index += 1

    if st.session_state.q_index < len(QUESTIONS):
        next_q = QUESTIONS[st.session_state.q_index][1]
        st.session_state.messages.append({"role": "assistant", "content": next_q})
        st.rerun()
    else:
        # Recommend
        try:
            recs = build_recommendations(st.session_state.prefs, count=10)
        except Exception as e:
            st.session_state.messages.append({"role": "assistant", "content": f"Oops — I couldn’t fetch recommendations. {e}"})
            st.session_state.done = True
            st.rerun()

        # Build response text
        lines = ["Here are your recommendations (last 20 years):"]
        for m in recs:
            title = m.get("title", "Untitled")
            date = m.get("release_date", "")
            year = date[:4] if date else "—"
            rating = m.get("vote_average", "N/A")
            tag = label_movie(m)
            lines.append(f"- **{title} ({year})** — {tag} — ⭐ {rating}")

        lines.append("\nIf you want a new list, type **reset**.")
        st.session_state.messages.append({"role": "assistant", "content": "\n".join(lines)})
        st.session_state.done = True
        st.rerun()
