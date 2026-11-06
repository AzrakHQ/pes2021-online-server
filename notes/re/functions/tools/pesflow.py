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
TOP_ITEMS = {"quick": 0x11, "divisions": 0x12, "coop": 0x13, "lobby": 0x14, "teamplay": 0x15, "matchday": 0x16,
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


def dialog_ok(p, cancel=False):
    """Confirm (or cancel) the open dialog, if any; returns its class name or None."""
    for a, k in input_windows(p):
        if "Dialog" in k:
            vt = p.u64(f"@{a:x}")
            fn = p.u64(f"@{vt + DIALOG_OK + (8 if cancel else 0):x}")
            p.gcall(fn, a)
            return k
    return None


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
    elif cmd == "ok":
        print(dialog_ok(p) or "no dialog")
    elif cmd == "go":                       # pesflow.py PORT go lobby|divisions|matchday|quick|coop|...
        print(top_menu_go(p, sys.argv[3]))
    elif cmd == "wait":                     # pesflow.py PORT wait <screen substring> [timeout]
        print(wait_screen(p, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 60))
    else:
        sys.exit("commands: screen | nodes | windows | ok | go ITEM | wait SCREEN [TIMEOUT]")
