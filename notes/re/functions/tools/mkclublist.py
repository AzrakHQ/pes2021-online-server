#!/usr/bin/env python3
"""Add the clubs (by league) and the game's own tournament entry lists to the admin page's team file.

  mkclublist.py [teams.json]        (default: ../../../../server/pesserver/web/teams.json; updated in place)

From the game's own database (pesdb.py, read-only): Competition.bin (36-byte records: byte 5 = competition id,
bytes 8..36 = its internal name, e.g. ENGLAND_D1_LEAGUE, UEFA_CHAMPIONS_LEAGUE) and CompetitionEntry.bin (12-byte
records: u32 team id, u32 entry id, byte 8 = competition id). Only teams of the server's online team list
(team_list.json, mkteamlist.py) are used.

Writes into teams.json:
  national     [{region, teams}]: every national team (a team named like a country of Country.bin and in no
               league), by region. The Online Tournament team select shows them by region [live 2026-11-21].
  clubs        [{region, teams}]: the first and second divisions by country, then the other clubs by region.
               The team select shows clubs by league ("English League", "Ligue 1 Uber Eats", "Other European
               Teams") [live 2026-11-21].
  tournaments  {key: [team ids]}: the entrants of the game's own tournaments (Champions League, EURO, ...).
"""
import collections, json, os, struct, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pesdb  # noqa: E402

COUNTRY = {"ENGLAND": "England", "ITALY": "Italy", "SPAIN": "Spain", "FRANCE": "France", "NETHERLANDS": "Netherlands",
           "PORTUGAL": "Portugal", "BRAZIL": "Brazil", "ARGENTINA": "Argentina", "CHILE": "Chile",
           "BELGIUM": "Belgium", "RUSSIA": "Russia", "SWITZERLAND": "Switzerland", "TURKEY": "Turkey",
           "COLOMBIA": "Colombia", "CHINA": "China", "DENMARK": "Denmark", "SCOTLAND": "Scotland",
           "THAILAND": "Thailand"}
FAKE = {"FAKE_PES_LEAGUE_EUROPE": "PES League Europe", "FAKE_PES_LEAGUE_SAMERICA": "PES League Latin America",
        "FAKE_PES_LEAGUE_ASIA": "PES League Asia"}
# Country.bin region codes -> the regions of the team select
REGION = {513: "Europe", 514: "Europe", 1283: "Africa", 1029: "Americas", 1540: "Americas", 1541: "Americas",
          769: "Asia and Oceania", 770: "Asia and Oceania", 774: "Asia and Oceania", 1798: "Asia and Oceania"}
OTHER = {"Europe": "Other European clubs", "Americas": "Other American clubs", "Asia and Oceania": "Other Asian clubs",
         "Africa": "Other African clubs"}
TOURNAMENTS = {"ucl": "UEFA_CHAMPIONS_LEAGUE", "uel": "UEFA_EUROPE_LEAGUE", "libertadores": "COPA_LIBERTADORES",
               "afccl": "AFC_CHAMPIONS_LEAGUE", "euro": "EURO", "copa": "COPA_AMERICA", "asia": "AFC_ASIA_CUP",
               "africa": "AFRICA_NATIONS_CUP"}
NATIONAL_ORDER = ["Europe", "Africa", "Americas", "Asia and Oceania"]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "..", "..", "server", "pesserver", "web", "teams.json")
    srv = os.path.join(os.path.dirname(os.path.abspath(out)), "..", "team_list.json")
    online = {t for t, _ in json.load(open(srv))["teams"]}
    tj = json.load(open(out, encoding="utf-8"))
    db = pesdb.DB()
    cd, _ = pesdb.load("Competition.bin")
    comps = {pesdb.cstr(cd[o + 8:o + 36]): cd[o + 5] for o in range(0, len(cd), 36)}
    ed, _ = pesdb.load("CompetitionEntry.bin")
    entries = collections.defaultdict(list)
    for o in range(0, len(ed), 12):
        tid = struct.unpack_from("<I", ed, o)[0]
        if tid in online and tid not in entries[ed[o + 8]]:
            entries[ed[o + 8]].append(tid)
    name = lambda t: tj["names"].get(str(t)) or db.team_name(t)          # noqa: E731
    fold = lambda s: "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))   # noqa: E731
    by_name = lambda ids: sorted(ids, key=lambda t: fold(name(t)))      # noqa: E731

    leagues, in_league = [], set()
    for key, label in COUNTRY.items():
        for div in ("D1", "D2"):
            cid = comps.get(f"{key}_{div}_LEAGUE")
            if cid is not None and entries.get(cid):
                leagues.append({"region": label if div == "D1" else f"{label}, 2nd division", "teams": by_name(entries[cid])})
                in_league |= set(entries[cid])
    for key, label in FAKE.items():
        cid = comps.get(key)
        if cid is not None and entries.get(cid):
            leagues.append({"region": label, "teams": by_name(entries[cid])})
            in_league |= set(entries[cid])

    countries = {c["name"]: c["region"] for c in db.countries.values()}
    national, others = collections.defaultdict(list), collections.defaultdict(list)
    for t in db.teams:
        if t["id"] not in online or t["id"] in in_league:
            continue
        if t["name"] in countries:
            national[REGION.get(countries[t["name"]], "Europe")].append(t["id"])
        else:
            country = next((c for i, c in db.countries.items() if i == t["country"]), None)
            region = REGION.get(country["region"], "Europe") if country else "Europe"
            others[OTHER[region] if t["kind"] != 1 else "Classic teams"].append(t["id"])
    clubs = leagues + [{"region": r, "teams": by_name(others[r])} for r in
                       [*OTHER.values(), "Classic teams"] if others.get(r)]
    tj["national"] = [{"region": r, "teams": national[r]} for r in NATIONAL_ORDER if national.get(r)]
    tj["clubs"] = clubs
    tj["tournaments"] = {k: entries[comps[v]] for k, v in TOURNAMENTS.items() if comps.get(v) in entries}
    for t in online:
        tj["names"].setdefault(str(t), db.team_name(t))
    tj["_"] = ("Team names from PES 2021's own team list (notes/re/functions/tools/pesdb.py dump), for the admin "
               "page. national: national teams by region; clubs: clubs by league, then the others by region; "
               "tournaments: the entrants of the game's own tournaments (mkclublist.py, from Competition.bin and "
               "CompetitionEntry.bin).")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{out}: {sum(len(r['teams']) for r in tj['national'])} national teams, "
          f"{sum(len(r['teams']) for r in clubs)} clubs in {len(clubs)} groups, tournaments {sorted(tj['tournaments'])}")


if __name__ == "__main__":
    main()
