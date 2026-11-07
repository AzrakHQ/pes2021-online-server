"""Game-state helpers on top of probe.py (addresses: notes/re/functions/flow.md)."""
import struct
from probe import Probe

FLOW = 0x1437f8990          # flow engine object; current node name = std::string at +0x60


def msvc_string(p, addr):
    """Read an MSVC std::string at an absolute address (inline when capacity < 16)."""
    raw = p.read(f"@{addr:x}", 32)
    size, cap = struct.unpack_from("<QQ", raw, 16)
    if size > 4096:
        return None
    if cap < 16:
        return raw[:size].decode("utf-8", "replace")
    ptr = struct.unpack_from("<Q", raw, 0)[0]
    return p.read(f"@{ptr:x}", size).decode("utf-8", "replace") if size else ""


def screen(p):
    """The current flow node, e.g. 'TopMenu/TopMenuNew'."""
    eng = p.u64(FLOW)
    return msvc_string(p, eng + 0x60) if eng else None


TASKS = 0x1437f89d8         # task root: first child at +0x40, next at +0x48 (circular), name std::string at +8
TOPMENU_VT = 0x14266ddd8    # vtable of menu::TopModeSelect
TOPMENU_DECIDE = 0x140ab03f0
TOP_ITEMS = {"info": 0x19, "settings": 0x21, "quick": 0x11, "divisions": 0x12, "coop": 0x13, "lobby": 0x14, "teamplay": 0x15, "matchday": 0x16,
             "tournament": 0x17, "exhibition": 1}


def rtti(p, obj):
    """C++ class name of an object, from MSVC RTTI (vtable[-1] -> CompleteObjectLocator -> TypeDescriptor)."""
    try:
        vt = p.u64(f"@{obj:x}")
        col = p.u64(f"@{vt - 8:x}")
        td_rva = p.u32(f"@{col + 12:x}")
        return p.cstr(f"@{0x140000000 + td_rva + 16:x}", 96)
    except Exception:  # noqa: BLE001
        return "?"


def nodes(p):
    """[(address, class, name)] of the live flow nodes."""
    out, h = [], p.u64(TASKS)
    first = c = p.u64(f"@{h + 0x40:x}") if h else 0
    while c and len(out) < 64:
        out.append((c, rtti(p, c), msvc_string(p, c + 8)))
        c = p.u64(f"@{c + 0x48:x}")
        if c == first:
            break
    return out


def find_node(p, vtable=None, klass=None):
    for addr, k, _ in nodes(p):
        if (vtable and p.u64(f"@{addr:x}") == vtable) or (klass and klass in k):
            return addr
    return None


def top_menu_go(p, item):
    """On TopMenu/TopMenuNew: select an eFootball/Kick Off item (TOP_ITEMS name or id) and confirm it."""
    menu = find_node(p, vtable=TOPMENU_VT)
    if menu is None:
        raise RuntimeError(f"no top menu (screen is {screen(p)})")
    iid = TOP_ITEMS.get(item, item)
    if isinstance(iid, str):
        iid = int(iid, 0)                  # a number from the command line ("0x21", "33")
    level = p.u32(f"@{menu + 0x98:x}")
    p.write(f"@{menu + 0x9c + 4 * level:x}", struct.pack("<I", iid))
    return p.gcall(TOPMENU_DECIDE, menu)


INPUT_WIN = 0x140e3c810    # called with each window that has input (dialogs included)
DIALOG_OK = 0x188           # vtable slot: OK / decide; +0x190 = cancel


def input_windows(p, wait=1.5):
    """Objects currently getting input, found by tracing the input-window function for a moment."""
    import time
    p.poll()
    p.untrace(INPUT_WIN)                 # (a trace left over from an interrupted script)
    p.trace(INPUT_WIN, "inputwin", 20)
    time.sleep(wait)
    p.untrace(INPUT_WIN)
    seen = []
    for e in p.poll():
        if "hit inputwin" in e:
            a = int(e.split("rcx=")[1].split()[0], 16)
            if a not in seen:
                seen.append(a)
    return [(a, rtti(p, a)) for a in seen]


# Windows that block like a dialog and close with the same A handler (vtable +0x188), seen live 2026-11-07:
# the one-time message box (CMD_GET_URGENT_ANNOUNCEMENT) and the Information pop-up (news / private list).
# the lobby notice (CMD_GET_LOBBYINFO has_info YES) is a menuutility::OnlineInformationNotice, closed the same way.
POPUPS = ("Dialog", "MenuUrgentAnnouncement", "InformationNotice")


def is_popup(klass):
    return any(x in (klass or "") for x in POPUPS)


def dialog_ok(p, cancel=False, button=None):
    """Confirm (or cancel) the open dialog, if any; returns its class name or None.
    button=i first moves the cursor to button i (Yes/No dialogs: the buttons left to right, from 0)."""
    for a, k in input_windows(p):
        if is_popup(k):
            if button is not None and "Dialog" in k:
                cur = p.u64(f"@{a + 0x88:x}") + 0xd8
                p.write(f"@{cur:x}", struct.pack("<I", button))
            vt = p.u64(f"@{a:x}")
            fn = p.u64(f"@{vt + DIALOG_OK + (8 if cancel else 0):x}")
            p.gcall(fn, a)
            return k
    return None


TITLE_PRESS = 0x140a98d00     # menu::TitleStart "a button was pressed" (plays titleOut, then posts proceed)


BIND_PADS = 0x141b52a60     # input manager: take the pressed device as the player's (what a real title press does)
PADS_BOUND = 0x141b52380    # 1 once the player has a controller


def bind_controller(p):
    """Give the player a controller, as a real key press on the title screen does. Without it, keys work in menus
    but nobody moves in a match, which then ends after 39 s for "no operation" (static 0x140a9a6c0 → 0x141b52a60;
    live 2026-11-07: 0 → 1)."""
    if p.call(PADS_BOUND) == 0:
        p.gcall(BIND_PADS, 1)
        import time
        time.sleep(0.5)
    return p.call(PADS_BOUND)


def title_press(p):
    """Leave the title screen (Intro/MenuTitle) without a key press, with the player's controller bound."""
    for a, k, n in nodes(p):
        if "TitleStart" in (k or ""):
            bind_controller(p)
            p.gcall(TITLE_PRESS, a)
            return k
    return None


NCL_ASSIGN = 0x1403d3aa0      # network Buffer assign(buf, data, len): sees every message body in plain msgpack
NCL_TX, NCL_RX = 0x14184e5b8, 0x141856aa1    # callers: a request being sent / a message received (decoded)


def _msg_head(raw):
    """msgid / rqid / result from the first bytes of a msgpack map (the trace dumps 128 bytes)."""
    import msgpack
    u = msgpack.Unpacker(raw=False, strict_map_key=False, unicode_errors="replace")
    u.feed(raw)
    out, keys = {}, []
    try:
        n = u.read_map_header()
        for _ in range(n):
            k = u.unpack()
            keys.append(str(k))
            v = u.unpack()
            if k in ("msgid", "rqid", "result"):
                out[k] = v
            if len(out) == 3:
                break
    except Exception:  # noqa: BLE001 - truncated dump
        pass
    if "msgid" not in out:
        out["msgid"] = "?{" + ",".join(keys[:4]) + "}" if keys else "?(not msgpack: HTTP/JSON?)"
    return out


def traffic(p, seconds=60, show=print):
    """Log every request the game sends and every message it receives, decoded inside the game."""
    import time
    p.poll()
    p.untrace(NCL_ASSIGN)
    p.trace(NCL_ASSIGN, "ncl", 100000, "1:128")
    end = time.time() + seconds
    try:
        while time.time() < end:
            for e in p.poll():
                if "hit ncl" not in e:
                    continue
                caller = int(e.split("caller=")[1].split()[0], 16)
                if caller not in (NCL_TX, NCL_RX):
                    continue
                n = int(e.split("r8=")[1].split()[0], 16)
                raw = bytes.fromhex(e.split("*=")[1].strip())[:n]
                h = _msg_head(raw)
                arrow = "->" if caller == NCL_TX else "<-"
                show(f"{time.strftime('%H:%M:%S')} {arrow} {h.get('msgid', '?')} rqid={h.get('rqid')}"
                     f"{' ' + str(h['result']) if 'result' in h else ''} ({n} bytes)")
            time.sleep(0.3)
    finally:
        p.untrace(NCL_ASSIGN)


NOAFK_VA = 0x14213cfe0     # MatchOnline "no operation" trigger: sets [this+0x11], reports VIXT2253 (line 2253),
#                            then ends the match with reason 5; the caller measures the idle time
NOAFK_OLD, NOAFK_NEW = bytes.fromhex("4053"), bytes.fromhex("c390")   # push rbx -> ret; nop


def noafk(p, on=True):
    """TEST COPIES ONLY (probe build, in memory): matches are never called off for "no operation", so a match
    with nobody at the controls runs to full time. Returns the state."""
    cur = p.read(NOAFK_VA, 2)
    want, other = (NOAFK_NEW, NOAFK_OLD) if on else (NOAFK_OLD, NOAFK_NEW)
    if cur == want:
        return "already " + ("on" if on else "off")
    if cur != other:
        raise RuntimeError(f"unexpected bytes {cur.hex()} at {NOAFK_VA:#x}")
    p.write(NOAFK_VA, want)
    return "on" if on else "off"


def wait_screen(p, want, timeout=60, ok_dialogs=True):
    """Wait until the screen name contains `want`, confirming dialogs on the way."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        s = screen(p) or ""
        if want in s:
            return s
        if ok_dialogs:
            dialog_ok(p)
        time.sleep(1)
    raise TimeoutError(f"screen is {screen(p)!r}, not {want!r}")


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 27801
    p = Probe(port)
    cmd = sys.argv[2] if len(sys.argv) > 2 else "screen"
    if cmd == "screen":
        print(screen(p))
    elif cmd == "nodes":
        for a, k, n in nodes(p):
            print(hex(a), k, n)
    elif cmd == "windows":
        for a, k in input_windows(p):
            print(hex(a), k)
    elif cmd == "traffic":                  # pesflow.py PORT traffic [SECONDS]  (-> sent, <- received)
        traffic(p, int(sys.argv[3]) if len(sys.argv) > 3 else 60)
    elif cmd == "bindpad":                  # pesflow.py PORT bindpad  (the player gets a controller; see title)
        print(bind_controller(p))
    elif cmd == "noafk":                    # pesflow.py PORT noafk [off]  (test copies: no idle end in matches)
        print(noafk(p, not (len(sys.argv) > 3 and sys.argv[3] == "off")))
    elif cmd == "title":                    # pesflow.py PORT title   (title screen -> login, no keys)
        print(title_press(p) or "not on the title screen")
    elif cmd == "ok":                       # pesflow.py PORT ok [BUTTON]
        print(dialog_ok(p, button=int(sys.argv[3]) if len(sys.argv) > 3 else None) or "no dialog")
    elif cmd == "go":                       # pesflow.py PORT go lobby|divisions|matchday|quick|coop|info|settings|ID
        print(top_menu_go(p, sys.argv[3]))
    elif cmd == "wait":                     # pesflow.py PORT wait <screen substring> [timeout]
        print(wait_screen(p, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 60))
    else:
        sys.exit("commands: screen | nodes | windows | traffic [S] | title | ok [BUTTON] | go ITEM | wait SCREEN [TIMEOUT]")
