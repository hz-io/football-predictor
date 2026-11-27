import json
import os
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
BASE_URL = "https://api.football-data.org/v4"

# Major leagues & competitions supported on football-data.org free tier
COMPETITIONS = ["PL", "CL", "PD", "BL1", "SA", "FL1"]


def api_get(path: str):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    if API_KEY:
        req.add_header("X-Auth-Token", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} on {path}: {e.reason}")
        return None
    except Exception as e:
        print(f"Error on {path}: {e}")
        return None


def main():
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=30)).strftime("%Y-%m-%d")

    teams_map = {}
    all_matches = []
    team_schedule = {}

    # 1. Fetch Standings & Matches per competition
    for code in COMPETITIONS:
        standings_data = api_get(f"/competitions/{code}/standings")
        if standings_data and "standings" in standings_data:
            for st in standings_data["standings"]:
                if st.get("type") == "TOTAL":
                    for row in st.get("table", []):
                        t = row.get("team", {})
                        tid = str(t.get("id"))
                        if not tid:
                            continue
                        form_raw = row.get("form") or ""
                        last5 = [x.strip() for x in form_raw.split(",") if x.strip() in ("W", "D", "L")][-5:]
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
                            "last5": last5,
                            "nextMatch": None,
                            "injuries": [],
                            "suspensions": []
                        }

        matches_data = api_get(f"/competitions/{code}/matches?dateFrom={date_from}&dateTo={date_to}")
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
                    "score": m.get("score", {}).get("fullTime", {})
                }
                all_matches.append(item)

                if status in ("SCHEDULED", "TIMED"):
                    team_schedule.setdefault(hid, []).append((utc_date, item["id"], item["away"], item["competition"], "Home"))
                    team_schedule.setdefault(aid, []).append((utc_date, item["id"], item["home"], item["competition"], "Away"))

    # 2. Attach each team's next scheduled match
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

    all_matches.sort(key=lambda x: x["utcDate"])

    output = {
        "updatedAt": now.isoformat(),
        "dateRange": {"from": date_from, "to": date_to},
        "teams": teams_map,
        "matches": all_matches
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(all_matches)} matches and {len(teams_map)} teams to data.json")


if __name__ == "__main__":
    main()
