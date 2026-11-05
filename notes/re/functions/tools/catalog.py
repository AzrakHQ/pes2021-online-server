"""Catalog of every CMD_* the game knows: area, transport, request keys, reply fields, our status."""
import json, re, sys, collections
import kb, pe
S = sys.argv[1]  # folder with allcmds.txt, handled.txt, seen.txt; writes catalog.json there
names = [l.strip() for l in open(f"{S}/allcmds.txt") if l.strip()]
sch = json.load(open(kb.SCHEMAS))
handled = set(l.strip() for l in open(f"{S}/handled.txt"))
seen = collections.Counter()
for l in open(f"{S}/seen.txt"):
    n, c = l.split(); seen[c] = int(n)
AREAS = [("Fair play", r"CHEAT|TAMPER|MANNER|KICK_REQUEST"),
         ("Lobby & rooms", r"(?<!COOP_)ROOM|LOBBY|INVITE"),
         ("myClub", r"MYCLUB|SCOUT|AUCTION|CONTRACT|AGENT|TRAINER|MANAGER|SQUAD|PLAYERCARD|LIVEEVENT"),
         ("Co-op", r"COOP"), ("Clans", r"CLAN"), ("PES League", r"PESLEAGUE|PESLG|COMPLG"), ("Matchday", r"MATCHDAY"),
         ("Events & competitions", r"COMPEV|EVENT_CATEGORY|FINISH_SEASON"),
         ("Random Selection", r"RANDOMSELECTION"),
         ("Spectating", r"WATCHING|OBSERVE|STREAM"),
         ("Connectivity / relay", r"TURN|NTL|GAMERELAY|PUNCH|IPANDPORT|NAT"),
         ("Matchmaking", r"MATCHING|QUICKMATCH"),
         ("Divisions & rankings", r"DIVISION|RANKING|RANK"),
         ("Social", r"FOLLOW|BLOCK|TEXTCHAT|DEMOCHAT"),
         ("In-match sync", r"_DATA_SYNC|SYNC_COMMENTARY|SELECT_SIDE|TEAM_SELECT|^CMD_ERR$|^CMD_LACK$|SENDFAILED|LOST_GUEST|^CMD_SET_GUEST$|REVISION_CHECK|^CMD_RECONNECT$"),
         ("Store & platform", r"STORE|STEAM|DMM|TRIAL|KONAMIID|XSTS|PSID|COIN|GP_|PRODUCT|TRANSACTION|E_FOOTBALL_POINT|PLATFORM_SESSION|ONLINE_ID"),
         ("News & info", r"INFO|ANNOUNCE|MAINTENANCE|BANNER|_AD|EULA"),
         ("Match", r"GAME|MATCH|STATS|RESULT|FOUL|SCORE|PHASE|NOTICE|ALIVE|ABNORMAL|PLAYTIME|HERO|TRACKRECORD|POSITION_RATING|OFTEN_USED"),
         ("Login & account", r"LOGIN|AUTH|PLAYER|SVR|URL|CHALLENGE|LANGUAGE|AVATAR|PROFILE|DISCONNECT|HEARTBEAT|CURRENT|ONLINE_MODE|OFFLINE_MODE|SERVER_ENV|DLC|LIVEDATA|STRING|NATION|LOG|AGING")]
def area(n):
    for a, rx in AREAS:
        if re.search(rx, n): return a
    return "Other"
out = []
for n in names:
    cls, s = kb.camel(n)
    req, http = [], False
    for v, off, ms in pe.vftables(f"commandautomake::Cmd{cls}Option"):
        if off == 0 and len(ms) > 3 and ms[3] != 0x140ca1a50:
            http = True; req = [k.rstrip("=") for _, k in kb.fn_strings(ms[3]) if k.endswith("=")]
        if off == 280 and len(ms) > 1 and ms[1] != 0x140ca1a50 and not req:
            req = [k for _, k in kb.fn_strings(ms[1]) if k.isidentifier()]
    std = {"msgid", "rqid", "svrtype", "client", "urltype"}
    out.append({"cmd": n, "area": area(n), "transport": "HTTP" if http else "gate",
                "request": [k for k in dict.fromkeys(req) if k not in std],
                "reply": sorted(k for k in (s or {}).get("fields", {}) if k not in ("msgid", "rqid", "result")),
                "status": "handled" if n in handled else ("acked" if seen[n] else "unseen"), "seen": seen[n]})
json.dump(out, open(f"{S}/catalog.json", "w"), indent=0)
print(len(out), collections.Counter(o["area"] for o in out).most_common(), collections.Counter(o["status"] for o in out))
