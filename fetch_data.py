import json
import os
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

FD_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
AF_API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

FD_BASE = "https://api.football-data.org/v4"
AF_BASE = "https://v3.football.api-sports.io"

COMPETITIONS = ["PL", "CL", "PD", "BL1", "SA", "FL1"]


def http_get_json(url: str, headers: dict):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} on {url}: {e.reason}")
        return None
    except Exception as e:
        print(f"Error on {url}: {e}")
        return None


def fd_get(path: str):
    headers = {"X-Auth-Token": FD_API_KEY} if FD_API_KEY else {}
    return http_get_json(f"{FD_BASE}{path}", headers)


def af_get(path: str):
    if not AF_API_KEY:
        return None
    headers = {"x-apisports-key": AF_API_KEY}
    return http_get_json(f"{AF_BASE}{path}", headers)


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in (name or "") if ch.isalnum())


def fetch_injuries_and_bans_from_api_football(teams_by_norm: dict):
    """Fetch injuries/suspensions from API-Football free tier (100 req/day) if key is set."""
    if not AF_API_KEY:
        return

    # Major league IDs in API-Football: EPL=39, UCL=2, LaLiga=140, Bundesliga=78, SerieA=135, Ligue1=61
    af_leagues = [39, 2, 140, 78, 135, 61]
    season = 2026

    for lid in af_leagues:
        data = af_get(f"/injuries?league={lid}&season={season}")
        if not data or not isinstance(data.get("response"), list):
            continue

        for item in data["response"]:
            team_info = item.get("team") or {}
            player_info = item.get("player") or {}
            t_name = team_info.get("name", "")
            p_name = player_info.get("name", "")
            reason = (player_info.get("reason") or "").lower()
            p_type = (player_info.get("type") or "").lower()

            if not t_name or not p_name:
                continue

            norm = normalize_name(t_name)
            matched_team = teams_by_norm.get(norm)
            if not matched_team:
                for k, v in teams_by_norm.items():
                    if norm and (norm in k or k in norm):
                        matched_team = v
                        break

            if not matched_team:
                continue

            is_ban = ("suspend" in reason) or ("ban" in reason) or ("card" in reason) or ("suspend" in p_type)
            entry = {
                "name": p_name,
                "reason": player_info.get("reason") or player_info.get("type") or ("Suspension" if is_ban else "Injury"),
                "impact": 0.08 if is_ban else 0.07
            }

            if is_ban:
                if not any(x.get("name") == p_name for x in matched_team["suspensions"]):
                    matched_team["suspensions"].append(entry)
            else:
                if not any(x.get("name") == p_name for x in matched_team["injuries"]):
                    matched_team["injuries"].append(entry)


def main():
    now = datetime.now(timezone.utc)
    # Look back 35 days for recent finished matches (to build true Last 5 across competitions)
    # and look ahead 30 days for upcoming fixtures
    past_from = (now - timedelta(days=35)).strftime("%Y-%m-%d")
    future_to = (now + timedelta(days=30)).strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")

    teams_map = {}
    all_matches = []
    team_schedule = {}
    team_finished_results = {}  # tid -> list of (utcDate, W/D/L)

    for code in COMPETITIONS:
        standings_data = fd_get(f"/competitions/{code}/standings")
        if standings_data and "standings" in standings_data:
            for st in standings_data["standings"]:
                if st.get("type") == "TOTAL":
                    for row in st.get("table", []):
                        t = row.get("team", {})
                        tid = str(t.get("id"))
                        if not tid:
                            continue
                        gf = row.get("goalsFor", 0)
                        ga = row.get("goalsAgainst", 0)
                        played = max(1, row.get("playedGames", 1))
                        teams_map[tid] = {
                            "id": tid,
                            "name": t.get("shortName") or t.get("name") or "Unknown",
                            "leagueCode": code,
                            "leagueName": standings_data.get("competition", {}).get("name", code),
                            "leagueRank": row.get("position", 0),
                            "leaguePoints": row.get("points", 0),
                            "played": played,
                            "gfAvg": round(gf / played, 2),
                            "gaAvg": round(ga / played, 2),
                            "last5": [],
                            "nextMatch": None,
                            "injuries": [],
                            "suspensions": []
                        }

        matches_data = fd_get(f"/competitions/{code}/matches?dateFrom={past_from}&dateTo={future_to}")
        if matches_data and "matches" in matches_data:
            comp_name = matches_data.get("competition", {}).get("name", code)
            is_cup = code in ("CL",)
            for m in matches_data["matches"]:
                home = m.get("homeTeam", {})
                away = m.get("awayTeam", {})
                hid = str(home.get("id", ""))
                aid = str(away.get("id", ""))
                utc_date = m.get("utcDate", "")
                status = m.get("status", "SCHEDULED")
                ft = m.get("score", {}).get("fullTime", {})
                hg = ft.get("home")
                ag = ft.get("away")

                item = {
                    "id": str(m.get("id")),
                    "utcDate": utc_date,
                    "status": status,
                    "competition": comp_name,
                    "competitionCode": code,
                    "type": "Cup" if is_cup else "League",
                    "homeId": hid,
                    "home": home.get("shortName") or home.get("name") or "Home",
                    "awayId": aid,
                    "away": away.get("shortName") or away.get("name") or "Away",
                    "score": ft
                }
                all_matches.append(item)

                if status == "FINISHED" and hg is not None and ag is not None:
                    if hg > ag:
                        h_res, a_res = "W", "L"
                    elif hg < ag:
                        h_res, a_res = "L", "W"
                    else:
                        h_res, a_res = "D", "D"
                    if hid:
                        team_finished_results.setdefault(hid, []).append((utc_date, h_res))
                    if aid:
                        team_finished_results.setdefault(aid, []).append((utc_date, a_res))

                if status in ("SCHEDULED", "TIMED") and utc_date[:10] >= today_str:
                    team_schedule.setdefault(hid, []).append((utc_date, item["id"], item["away"], item["competition"], "Home"))
                    team_schedule.setdefault(aid, []).append((utc_date, item["id"], item["home"], item["competition"], "Away"))

    # Build true Last 5 across all competitions for every team
    for tid, t_obj in teams_map.items():
        res_list = team_finished_results.get(tid, [])
        res_list.sort(key=lambda x: x[0])
        t_obj["last5"] = [r[1] for r in res_list[-5:]]

    # Attach each team's next scheduled match
    for tid, sched_list in team_schedule.items():
        sched_list.sort(key=lambda x: x[0])
        if tid in teams_map and len(sched_list) >= 1:
            nxt = sched_list[0]
            teams_map[tid]["nextMatch"] = {
                "date": nxt[0][:10],
                "opponent": nxt[2],
                "comp": nxt[3],
                "venue": nxt[4]
            }

    # Optional enrichment: Injuries & Suspensions via API-Football free key
    teams_by_norm = {}
    for t_obj in teams_map.values():
        teams_by_norm[normalize_name(t_obj["name"])] = t_obj
    fetch_injuries_and_bans_from_api_football(teams_by_norm)

    # Filter matches to display (recent 3 days + upcoming 30 days)
    display_from = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    display_matches = [m for m in all_matches if (m.get("utcDate") or "")[:10] >= display_from]
    display_matches.sort(key=lambda x: x["utcDate"])

    output = {
        "updatedAt": now.isoformat(),
        "dateRange": {"from": display_from, "to": future_to},
        "teams": teams_map,
        "matches": display_matches
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(display_matches)} matches and {len(teams_map)} teams to data.json")


if __name__ == "__main__":
    main()
