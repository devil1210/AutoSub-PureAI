import json
import re
import time
import urllib.parse
import urllib.request

def fetch_anime_web_context(series_name):
    search_query = re.sub(r"\(\d{4}\)|\[.*?\]", "", series_name).strip()
    headers = {"User-Agent": "SP-AutoSub/3.5 (Homelab Anime Subtitle Adapter)"}
    web_info = {
        "found": False,
        "official_title": series_name,
        "japanese_title": "",
        "genres": [],
        "synopsis": "",
        "characters_raw": []
    }

    try:
        q_enc = urllib.parse.quote(search_query)
        jikan_search_url = f"https://api.jikan.moe/v4/anime?q={q_enc}&limit=1"
        req = urllib.request.Request(jikan_search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            if results:
                anime = results[0]
                mal_id = anime.get("mal_id")
                web_info["found"] = True
                web_info["official_title"] = anime.get("title_english") or anime.get("title") or series_name
                web_info["japanese_title"] = anime.get("title_japanese") or ""
                web_info["synopsis"] = anime.get("synopsis") or ""
                web_info["genres"] = [g.get("name") for g in anime.get("genres", []) + anime.get("themes", []) if g.get("name")]

                if mal_id:
                    time.sleep(0.4)
                    chars_url = f"https://api.jikan.moe/v4/anime/{mal_id}/characters"
                    req_c = urllib.request.Request(chars_url, headers=headers)
                    with urllib.request.urlopen(req_c, timeout=5) as resp_c:
                        c_data = json.loads(resp_c.read().decode("utf-8")).get("data", [])
                        c_data.sort(key=lambda x: (0 if x.get("role") == "Main" else 1, -(x.get("favorites") or 0)))
                        for c_entry in c_data[:12]:
                            char_obj = c_entry.get("character", {})
                            raw_name = char_obj.get("name", "")
                            if "," in raw_name:
                                parts = [p.strip() for p in raw_name.split(",", 1)]
                                clean_char_name = f"{parts[1]} {parts[0]}"
                            else:
                                clean_char_name = raw_name
                            web_info["characters_raw"].append({
                                "name": clean_char_name,
                                "role": c_entry.get("role", "Supporting")
                            })
    except Exception as e:
        print(f"[WebContext] Jikan API omitido para '{search_query}': {e}", flush=True)

    if not web_info["found"]:
        try:
            wiki_search = f"https://es.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(search_query)}&format=json"
            req_w = urllib.request.Request(wiki_search, headers=headers)
            with urllib.request.urlopen(req_w, timeout=4) as resp_w:
                w_data = json.loads(resp_w.read().decode("utf-8"))
                search_hits = w_data.get("query", {}).get("search", [])
                if search_hits:
                    page_title = urllib.parse.quote(search_hits[0]["title"].replace(" ", "_"))
                    extract_url = f"https://es.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&titles={page_title}&format=json"
                    req_ex = urllib.request.Request(extract_url, headers=headers)
                    with urllib.request.urlopen(req_ex, timeout=4) as resp_ex:
                        ex_data = json.loads(resp_ex.read().decode("utf-8"))
                        pages = ex_data.get("query", {}).get("pages", {})
                        for _, p_val in pages.items():
                            snippet = p_val.get("extract", "")
                            if snippet:
                                web_info["found"] = True
                                web_info["synopsis"] = snippet[:500]
                                break
        except Exception:
            pass

    return web_info
