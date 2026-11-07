#!/usr/bin/env python3
"""scenario.py — scripted live tests: reply variants x navigation steps, with crash detection and a report.

  scenario.py FILE.json [VARIANT...]     run every variant (or the named ones) of a scenario
  scenario.py --list FILE.json           list its variants

Needs a probe session (probesession.py up 1|2|both): the test server runs the real repo handlers with
testserver/replypatch.py, so each variant only says how our replies differ. A scenario file:

  {"name": "matchday-open", "inst": 1,
   "variants": [{"name": "as-is"},
                {"name": "no-overview", "patch": {"patch": {"CMD_GET_MATCHDAY_INFO": {"del": ["compe_info.info_overview"]}}}},
                {"name": "err", "patch": {"err": {"CMD_GET_MATCHDAY_INFO": "ERR_DATABASE"}}}],
   "steps": [{"home": true},
             {"go": "matchday"},
             {"reply": "CMD_GET_MATCHDAY_INFO", "timeout": 60},
             {"wait": "Matchday", "timeout": 60, "ok": false},
             {"shot": "matchday"}]}

Steps (each may have "timeout", and "optional": true so a miss does not fail the variant; reply / no_reply take
"since": "variant" to count requests from the start of the variant instead of the step):
  home            back to the main menu (probe dialogs + Escape keys; relaunch + login if that fails)
  relaunch        restart the game and log in again (a fresh game session)
  go ITEM         main-menu item (pesflow TOP_ITEMS)
  wait SCREEN     until the screen name contains SCREEN; "ok": true|false confirms dialogs on the way (default true)
  screen          record the current screen name
  watch N         record every screen change for N seconds ("ok": confirm dialogs, default true; "press": KEY
                  when nothing changed for "idle" seconds; "until": SCREEN stops early)
  not_screen S    fail if the screen name contains S now
  reply CMD       our server answered CMD since the step started (from logs/replies.jsonl); "ok": true
                  confirms dialogs on the way, until the reply is seen
  no_reply CMD    the game did not ask for CMD during "timeout" seconds
  ok              confirm the open dialog (fails if there is none); "ok": N picks button N first (0 = left)
  key [K...]      key presses (tools/pkey.sh tokens) — last resort
  trace           {"trace": "0x140e47d00", "name": "dlgmsg", "deref": "", "max": 50}: hits go to the report
  sleep N
  shot NAME       screenshot into the report folder
  patch {...}     change the reply patches mid-variant (merged over the variant's)
  dialog          record the open dialog's class and text (fails if none)
"traffic": false at the top turns off the in-game message log (on by default: every request the game sends and
every message it receives, decoded inside the game, before its own filters).
After every step the runner checks that the game still runs (probe port + process); a crash ends the variant
with the Wine log tail, the last requests and replies, and the next variant relaunches the game.
Report: logs/scenarios/<name>-<time>/report.md (+ screenshots, replies.jsonl excerpt per variant).
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probesession as ps          # noqa: E402
import pesflow as pf               # noqa: E402
from probe import Probe            # noqa: E402

GAME_LOG = lambda inst: os.path.join(ps.LOGS, "game-dev.out" if inst == 1 else f"game-dev{inst}.out")


class StepFail(Exception):
    pass


class Crash(Exception):
    pass


def game_running(inst):
    exe = os.path.join(ps.game(inst), "files", "PES2021.exe")
    out = subprocess.run(["pgrep", "-af", "[P]ES2021.exe"], capture_output=True, text=True).stdout
    return any(l.split(None, 1)[1:] and l.split(None, 1)[1].startswith(exe) for l in out.splitlines())


def replies_since(t0, msgid=None):
    out = []
    try:
        with open(ps.REPLIES) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r["t"] >= t0 and (msgid is None or r["msgid"] == msgid):
                    out.append(r)
    except OSError:
        pass
    return out


def server_lines_since(pos, pats=("UNHANDLED", "MISMATCH", "HANDLER CRASH", "Traceback", "PATCH", "ERROR")):
    try:
        with open(ps.SERVER_OUT, "rb") as f:
            f.seek(pos); text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [l[24:260] for l in text.splitlines() if any(p in l for p in pats)]


def wine_tail(inst, n=25):
    try:
        lines = open(GAME_LOG(inst), errors="replace").read().splitlines()
    except OSError:
        return []
    hits = [l for l in lines if "seh" in l or "err:" in l or "EXCEPTION" in l or "Unhandled" in l]
    return hits[-n:]


def write_patch(d):
    tmp = ps.PATCH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, ps.PATCH)


def merge(a, b):
    out = json.loads(json.dumps(a))
    for k, v in (b or {}).items():
        out[k] = merge(out.get(k, {}), v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Runner:
    def __init__(self, sc, outdir):
        self.sc, self.inst, self.outdir = sc, sc.get("inst", 1), outdir
        self.p = None
        self.traces = {}
        self.traffic = []
        self.want_traffic = sc.get("traffic", True)

    def probe(self):
        if self.p is None:
            self.p = Probe(ps.port(self.inst))
            self.p.sink = self.sink
            if self.want_traffic:
                self.p.untrace(pf.NCL_ASSIGN)
                self.p.trace(pf.NCL_ASSIGN, "ncl", 100000, "1:128")
        return self.p

    def sink(self, events):
        for e in events:
            if "hit ncl" in e:
                caller = int(e.split("caller=")[1].split()[0], 16)
                if caller in (pf.NCL_TX, pf.NCL_RX):
                    n = int(e.split("r8=")[1].split()[0], 16)
                    h = pf._msg_head(bytes.fromhex(e.split("*=")[1].strip())[:n])
                    self.traffic.append(f"{time.strftime('%H:%M:%S')} {'->' if caller == pf.NCL_TX else '<-'} "
                                        f"{h.get('msgid', '?')} {h.get('rqid')} {h.get('result', '')}".rstrip())
            elif "hit " in e:
                name = e.split("hit ", 1)[1].split()[0]
                if name in self.traces:            # only traces a step asked for (not pesflow's own)
                    self.traces[name].append(e)

    def reset_probe(self):
        try:
            self.p and self.p.close()
        except Exception:  # noqa: BLE001
            pass
        self.p = None

    def relaunch(self):
        self.reset_probe()
        ps.launch(self.inst)
        ps.login(self.inst)

    def home(self, timeout):
        end = time.time() + timeout
        tries = 0
        while time.time() < end:
            s = pf.screen(self.probe()) or ""
            if s == "TopMenu/TopMenuNew":            # (TopMenu/TopMenuOnlineSettingSub etc. are sub-menus)
                return s
            if s.startswith("Intro/"):
                self.reset_probe(); ps.login(self.inst); continue
            if not pf.dialog_ok(self.probe()):
                ps_key(self.inst, "Escape")
            tries += 1
            time.sleep(2)
            if tries > 8:
                break
        self.relaunch()
        return pf.screen(self.probe())

    def step(self, st, log):
        t0 = time.time()
        to = st.get("timeout", 30)
        p = self.probe()
        if "home" in st:
            return self.home(st.get("timeout", 120))
        if "relaunch" in st:
            self.relaunch()
            return pf.screen(self.probe())
        if "go" in st:
            return pf.top_menu_go(p, st["go"])
        if "wait" in st:
            try:
                return pf.wait_screen(p, st["wait"], to, st.get("ok", True))
            except TimeoutError as e:
                raise StepFail(str(e))
        if "watch" in st:
            # log every screen change for N seconds; "ok": true confirms dialogs, "press": K presses K when the
            # screen has not changed for "idle" seconds (menus that wait for a button)
            seen, last, still = [], None, time.time()
            while time.time() < t0 + float(st["watch"]):
                s_ = pf.screen(p) or ""
                if s_ != last:
                    seen.append(f"{time.time() - t0:.0f}s {s_}")
                    last, still = s_, time.time()
                if st.get("ok", True) and pf.dialog_ok(p):
                    seen.append(f"{time.time() - t0:.0f}s (dialog ok)")
                    still = time.time()
                elif st.get("press") and time.time() - still > float(st.get("idle", 6)):
                    ps_key(self.inst, st["press"])
                    seen.append(f"{time.time() - t0:.0f}s (pressed {st['press']})")
                    still = time.time()
                if st.get("until") and st["until"] in s_:
                    break
                self.check_alive()
                time.sleep(0.7)
            return " | ".join(seen)
        if "screen" in st:
            return pf.screen(p)
        if "not_screen" in st:
            s = pf.screen(p) or ""
            if st["not_screen"] in s:
                raise StepFail(f"screen is {s!r}")
            return s
        since = self.t_variant if st.get("since") == "variant" else t0
        if "reply" in st:
            while time.time() < t0 + to:
                hit = replies_since(since, st["reply"])
                if hit:
                    return f"{st['reply']} rqid={hit[-1]['rqid']} patched={hit[-1]['patched']}"
                self.check_alive()
                if st.get("ok") and not replies_since(since, st["reply"]):
                    pf.dialog_ok(p)          # e.g. "No new Live Update" on the way into a mode
                time.sleep(0.5)
            raise StepFail(f"no {st['reply']} in {to}s")
        if "no_reply" in st:
            while time.time() < t0 + to:
                if replies_since(since, st["no_reply"]):
                    raise StepFail(f"game asked for {st['no_reply']}")
                self.check_alive()
                time.sleep(0.5)
            return "not asked"
        if "ok" in st:
            b = st["ok"] if isinstance(st["ok"], int) and not isinstance(st["ok"], bool) else None
            r = pf.dialog_ok(p, button=b)
            if not r:
                raise StepFail("no dialog")
            return r
        if "dialog" in st:
            d = dialog_info(p)
            if not d:
                raise StepFail("no dialog")
            return d
        if "key" in st:
            ks = st["key"] if isinstance(st["key"], list) else [st["key"]]
            ps_key(self.inst, *ks)
            return " ".join(ks)
        if "trace" in st:
            name = st.get("name") or st["trace"]
            r = p.trace(int(st["trace"], 16) if isinstance(st["trace"], str) else st["trace"], name,
                        st.get("max", 50), st.get("deref", ""))
            self.traces[name] = []
            return r
        if "sleep" in st:
            end = t0 + float(st["sleep"])
            while time.time() < end:
                self.check_alive(); time.sleep(min(1, max(0, end - time.time())))
            return "slept"
        if "shot" in st:
            out = os.path.join(self.outdir, f"{self.vname}-{st['shot']}.png")
            subprocess.run(["python3", os.path.join(ps.TOOLS, "pes.py"), "shot", out], capture_output=True,
                           env={**ps.ENV, "PES_INSTANCE": str(self.inst)})
            log.append(f"  ![{st['shot']}]({os.path.basename(out)})")
            return os.path.basename(out)
        if "patch" in st:
            self.cur_patch = merge(self.cur_patch, st["patch"])
            write_patch(self.cur_patch)
            return "patched"
        raise StepFail(f"unknown step {st}")

    def check_alive(self):
        """The game process runs and the probe (one client at a time: ours) still answers."""
        if not game_running(self.inst):
            raise Crash("game process exited")
        try:
            self.probe().base()
        except Exception:  # noqa: BLE001 - probe gone: crash (or a hang) if it does not come back
            self.reset_probe()
            time.sleep(2)
            if not game_running(self.inst):
                raise Crash("game process exited")
            try:
                self.probe().base()
            except Exception as e:  # noqa: BLE001
                raise Crash(f"probe stopped answering ({e!r})")

    def drain_traces(self):
        try:
            self.probe().poll()          # the sink files the events
        except Exception:  # noqa: BLE001
            pass

    def variant(self, v):
        self.vname = v["name"]
        self.cur_patch = v.get("patch", {})
        write_patch(self.cur_patch)
        log = [f"## {self.vname}", "", "```json", json.dumps(self.cur_patch), "```", ""]
        spos = os.path.getsize(ps.SERVER_OUT) if os.path.exists(ps.SERVER_OUT) else 0
        t_v = self.t_variant = time.time()
        result = "PASS"
        if not game_running(self.inst):
            log.append("- game not running: relaunch + login")
            self.relaunch()
        for i, st in enumerate(self.sc["steps"]):
            label = json.dumps(st)[:90]
            self.t_step0 = time.time()
            try:
                r = self.step(st, log)
                self.drain_traces()
                self.check_alive()
                log.append(f"- ok   `{label}` → {str(r)[:1500 if ('dialog' in st or 'watch' in st) else 160]} ({time.time() - self.t_step0:.1f}s)")
            except StepFail as e:
                log.append(f"- {'skip' if st.get('optional') else 'FAIL'} `{label}` → {e}")
                if not st.get("optional"):
                    result = "FAIL"
                    break
            except (RuntimeError, ValueError, KeyError, TimeoutError) as e:      # a step that could not run
                log.append(f"- **ERROR** `{label}` → {e!r}")
                result = "ERROR"
                break
            except (Crash, ConnectionError, OSError, EOFError) as e:
                time.sleep(2)
                result = "CRASH" if not game_running(self.inst) else "ERROR"
                log.append(f"- **{result}** at `{label}` → {e!r}")
                break
        if result == "CRASH":
            log += ["", "Wine log tail:", "```", *wine_tail(self.inst), "```"]
            self.reset_probe()
        reqs = replies_since(t_v)
        log += ["", f"Requests answered ({len(reqs)}): " + ", ".join(
            f"{r['msgid'][4:]}{'*' if r['patched'] else ''}" for r in reqs[-40:])]
        srv = server_lines_since(spos)
        if srv:
            log += ["", "Server log:", "```", *srv[-30:], "```"]
        for name, hits in self.traces.items():
            if hits:
                log += ["", f"Trace {name} ({len(hits)} hits):", "```", *[h[:200] for h in hits[:20]], "```"]
        if self.traffic:
            log += ["", f"In-game traffic ({len(self.traffic)} messages; -> sent, <- received):", "```",
                    *[t for t in self.traffic if "HEARTBEAT" not in t][-60:], "```"]
        self.traces, self.traffic = {}, []
        with open(os.path.join(self.outdir, f"{self.vname}-replies.jsonl"), "w") as f:
            for r in reqs:
                f.write(json.dumps(r) + "\n")
        log.insert(1, f"**{result}** in {time.time() - t_v:.0f}s")
        return result, log


def ps_key(inst, *ks):
    subprocess.run([os.path.join(ps.TOOLS, "pkey.sh"), *ks], env={**ps.ENV, "PES_INSTANCE": str(inst)})


def dialog_info(p):
    """Class and message text of the open dialog (std::string at dlg+0x268, see flow.md)."""
    import re
    for addr, klass in pf.input_windows(p):
        if "Dialog" not in (klass or "") and pf.is_popup(klass):
            return klass                       # message windows keep their text elsewhere
        if "Dialog" in (klass or ""):
            try:
                txt = re.sub(r"\x11..", "", pf.msvc_string(p, addr + 0x268) or "")
            except Exception:  # noqa: BLE001
                txt = "?"
            return f"{klass} {txt!r}"
    return None


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "--list":
        for v in json.load(open(a[1]))["variants"]:
            print(v["name"])
        return
    sc = json.load(open(a[0]))
    want = set(a[1:])
    variants = [v for v in sc["variants"] if not want or v["name"] in want]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(ps.LOGS, "scenarios", f"{sc['name']}-{stamp}")
    os.makedirs(outdir, exist_ok=True)
    r = Runner(sc, outdir)
    rows, body = [], []
    try:
        for v in variants:
            print(f"== {v['name']}", flush=True)
            res, log = r.variant(v)
            print("\n".join(log[:2] + [l for l in log if l.startswith("- ")]), flush=True)
            rows.append(f"| {v['name']} | {res} |")
            body += log + [""]
    finally:
        write_patch({})
    rep = os.path.join(outdir, "report.md")
    with open(rep, "w") as f:
        f.write(f"# Scenario {sc['name']} ({stamp})\n\n| variant | result |\n|---|---|\n" + "\n".join(rows) +
                "\n\n" + "\n".join(body))
    print("report:", rep)


if __name__ == "__main__":
    main()
