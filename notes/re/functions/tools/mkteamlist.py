#!/usr/bin/env python3
"""Build the server's online team list (server/pesserver/team_list.json) from the game's own Team.bin.

  mkteamlist.py [OUT.json]        (default: ../../../../server/pesserver/team_list.json)

Every team with a squad (at least 16 players), except the Master League defaults and special entries, with a
strength class TEAM_A (strongest) .. TEAM_E for the game's "team strength" matchmaking option
(CMD_GET_GAME_TEAM_LIST team_class; enum 0x140218f00: TEAM_NONE, TEAM_SAME, TEAM_E .. TEAM_A).

Strength [our own estimate; Konami's classes are not known]: each player is rated by the mean of their 8 best
abilities (goalkeepers by their goalkeeping), a team by the mean of its 11 best players; teams are ranked and
split 15 % A, 20 % B, 30 % C, 20 % D, 15 % E. Reads the game files through pesdb.py; read-only.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pesdb  # noqa: E402

OUTFIELD = ("offensive_awareness", "defensive_awareness", "dribbling", "ball_control", "tight_possession",
            "finishing", "low_pass", "lofted_pass", "heading", "ball_winning", "aggression", "place_kicking", "curl",
            "speed", "physical_contact", "balance", "kicking_power", "acceleration", "jump", "stamina")
GOALIE = ("gk_awareness", "gk_catching", "gk_parrying", "gk_reflexes", "gk_reach", "jump")
SPLIT = (("TEAM_A", .15), ("TEAM_B", .20), ("TEAM_C", .30), ("TEAM_D", .20), ("TEAM_E", .15))


def player_score(p):
    par = p["params"]
    keys = GOALIE if p["registered_position"] == "GK" else OUTFIELD
    vals = sorted((par.get(k) or 0 for k in keys), reverse=True)[:8]
    return sum(vals) / len(vals)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "..", "..", "server", "pesserver", "team_list.json")
    db = pesdb.load_db() if hasattr(pesdb, "load_db") else None
    if db is None:
        tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "pesdb-teamlist.json")
        os.system(f'{sys.executable} "{os.path.join(HERE, "pesdb.py")}" dump "{tmp}" > /dev/null')
        d = json.load(open(tmp))
    else:
        d = {"teams": db.teams, "players": db.players}
    players = {p["id"]: p for p in d["players"]}
    scored = []
    for t in d["teams"]:
        if len(t["roster"]) < 16 or "ML DEFAULT" in t["name"] or t["kind"] == 7:
            continue
        ps = sorted((player_score(players[r["player"]]) for r in t["roster"] if r["player"] in players), reverse=True)[:11]
        if len(ps) == 11:
            scored.append((sum(ps) / 11, t["id"], t["name"]))
    scored.sort(reverse=True)
    rows, i = [], 0
    for cls, share in SPLIT:
        n = round(share * len(scored)) if cls != "TEAM_E" else len(scored) - i
        rows += [[tid, cls] for _, tid, _ in scored[i:i + n]]
        i += n
    with open(out, "w") as f:
        json.dump({"_": "PES 2021's teams for online play, made by notes/re/functions/tools/mkteamlist.py from the game's "
                        "Team.bin: [team id, strength class TEAM_A (strongest) .. TEAM_E (our own estimate)].",
                   "teams": sorted(rows)}, f, separators=(",", ":"))
    print(f"{len(rows)} teams -> {out}")
    for cls, _ in SPLIT:
        names = [n for (s, tid, n) in scored if [tid, cls] in rows][:5]
        print(f"  {cls}: {sum(1 for r in rows if r[1] == cls):3d}  e.g. {', '.join(names)}")


if __name__ == "__main__":
    main()
