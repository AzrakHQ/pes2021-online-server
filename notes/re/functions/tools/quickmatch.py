#!/usr/bin/env python3
"""quickmatch.py — both game copies of a probe session from the main menu to kick-off of a Quick Match.

  quickmatch.py [MODE]      MODE: quick (default) | divisions — the main-menu item both copies enter

Steps (all seen live 2026-11-07): pesflow go MODE → (dialogs) → team select → Return ×3 (team, confirm, match
settings) → searching → the server pairs them → "View Opponent" quality dialog → Proceed (button 1) → match menu
(Kick Off highlighted) → Return on both → GAMELOAD → ENTRY → 1ST. Prints the phases; exits 0 at 1ST.
Keys go through the game folder's tools/pkey.sh; everything else through the probe.
"""
import os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probesession as ps          # noqa: E402
import pesflow as pf               # noqa: E402
from probe import Probe            # noqa: E402

TEAM_SCREEN = {"quick": "QuickTeamSelect", "divisions": "TeamSelect"}
MENU_SCREEN = {"quick": "QuickMatchMenu", "divisions": "MatchMenu"}


def key(inst, *ks):
    subprocess.run([os.path.join(ps.TOOLS, "pkey.sh"), *ks], env={**ps.ENV, "PES_INSTANCE": str(inst)},
                   capture_output=True)


def wait(inst, want, timeout, ok=True):
    p = Probe(ps.port(inst))
    try:
        return pf.wait_screen(p, want, timeout, ok)
    finally:
        p.close()


def last_phase():
    try:
        tail = open(ps.SERVER_OUT, errors="replace").readlines()[-400:]
    except OSError:
        return None
    for l in reversed(tail):
        m = re.search(r"CMD_CHANGE_GAMEPHASE \{.*'phase': '([A-Z0-9_]+)'", l)
        if m:
            return m.group(1)
    return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    for inst in (1, 2):
        p = Probe(ps.port(inst))
        try:
            if pf.screen(p) != "TopMenu/TopMenuNew":
                sys.exit(f"copy {inst} is not on the main menu ({pf.screen(p)})")
            pf.top_menu_go(p, mode)
        finally:
            p.close()
        print(f"copy {inst}:", wait(inst, TEAM_SCREEN[mode], 90), flush=True)
        time.sleep(3)
        for _ in range(3):
            key(inst, "Return", "sleep:2.5")
    # the server pairs them; each copy shows the quality-check dialog ("View Opponent" -> Proceed)
    end = time.time() + 90
    done = set()
    while time.time() < end and len(done) < 2:
        for inst in (1, 2):
            if inst in done:
                continue
            p = Probe(ps.port(inst))
            try:
                s = pf.screen(p) or ""
                if MENU_SCREEN[mode] in s:
                    done.add(inst)
                elif pf.dialog_ok(p, button=1):
                    pass
            finally:
                p.close()
        time.sleep(2)
    print("match menu on", sorted(done), flush=True)
    if len(done) < 2:
        sys.exit(1)
    time.sleep(3)
    key(2, "Return"); time.sleep(1); key(1, "Return")
    end = time.time() + 150
    seen = None
    while time.time() < end:
        ph = last_phase()
        if ph != seen:
            print("phase", ph, flush=True)
            seen = ph
        if ph == "1ST":
            return 0
        time.sleep(2)
    return 1


if __name__ == "__main__":
    sys.exit(main())
