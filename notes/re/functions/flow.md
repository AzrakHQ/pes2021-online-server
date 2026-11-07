# Flow engine: current screen, navigation, dialogs (for the debug probe)

> **Confirmed live with the probe (2026-11-06):**
> - current screen = the std::string at [0x1437f8990]+0x60 ("Intro/MenuTitle", "TopMenu/TopMenuNew", "Online/Login/LoginFlow", "Online/Lobby/LobbyServerSel", "Online/Lobby/LobbyRoomList");
> - the live nodes under [0x1437f89d8] carry RTTI class names (TopModeSelect, OnlineFlowLoginIntro, OnlineLobbyRoomSelect);
> - `trace 1414a8d60 flowjson 300 0:100` logs every screen change; `trace 140e3c810` lists the windows getting input, including an open dialog (DialogProceed);
> - writing the item id and calling the top menu's decide handler 0x140ab03f0 on the game thread took the game from the main menu into the Friendly Match Lobby login flow; calling a dialog's vtable+0x188 confirmed "No new Live Update available."; the game reached the Match Room List without a key press.
> - The game's window thread only pumps messages when one arrives: the probe posts WM_NULL after queueing a game-thread call.
> Helpers: `tools/pesflow.py` (screen, nodes, windows, ok, go ITEM, wait SCREEN).

PES2021.exe 1.01.00, static reading only. Tags: **[static]** = read at the address given, **[inferred]** = follows from
the code but not proven, **[guess]** = plausible only. Every claim has a probe line to check it live.

**Probe notes.** `ptr`/`read`/`trace`/`gcall` take exe VAs, and the probe translates them. But **gcall arguments are
passed raw**. If the exe is not loaded at 0x140000000 (check with `base`), convert any VA you pass as an argument
(a string literal, a vtable to compare) with `abs = base + (va - 0x140000000)`. Pointers read from memory are
already absolute, so pass them as they are (and use `@` when reading them).

MSVC `std::string` (32 bytes): +0 is the text if it fits (capacity < 16), otherwise a pointer to the text; +0x10 is
the size; +0x18 is the capacity. Helper used below:

```python
def sstr(p, a):                       # a = absolute address of a std::string
    b = p.read(f"@{a:x}", 32); n = int.from_bytes(b[16:24], 'little'); cap = int.from_bytes(b[24:32], 'little')
    if cap >= 16: return p.read(f"@{int.from_bytes(b[0:8], 'little'):x}", n).decode('utf-8', 'replace')
    return b[:n].decode('utf-8', 'replace')
def rtti(p, obj):                     # class name of a polymorphic object
    vt = p.u64(f"@{obj:x}"); col = p.u64(f"@{vt-8:x}"); rva = p.u32(f"@{col+0xc:x}")
    return p.cstr(f"@{p.base()+rva+0x10:x}")
```
(If `Probe.u64` doesn't accept "@…" strings, use `int.from_bytes(p.read(...), 'little')`.)

---

## The objects (how the flow engine is built)

- **flow::FlowListener** (vtable 0x142bb6f48). There is one instance, stored by its constructor 0x141fa04f0 in the
  global **0x1437f8990** (written once, at 0x141fa052d). [static]
  - +0x28 is the embedded **flow::FlowTransition** (constructor 0x141fa8020 is called with this+0x28). [static]
  - +0x180 is the embedded **FlowMutation** (constructor 0x141fa5810; it handles interrupts and resets). [static]
- **FlowTransition fields** (FT = [0x1437f8990]+0x28). [static, constructor 0x141fa8020 and reset 0x141fa9570]
  - FT+0x08 u32 **step**: 0 NEXT_FLOW_WAIT (idle), 1 FADEOUT, 2 JSON_LOAD, 3 FLOW_TRANSITION, 4 CANDIE, 5 POSTPROCESS,
    6 FADEINSTART, 7 FADEIN. The names come from 0x141fa9470; the setter is SetStep 0x141faadc0(FT, step).
  - FT+0x18 is the parsed JSON of the current flow node.
  - **FT+0x38 is a std::string holding the current flow node name.** It is set to "nil" at reset. At step 4 the
    engine copies the new node's name into it (0x141fa9d69).
  - FT+0x78 holds the "accepting events" bits. FT+0x80/+0x88 is the vector of pending transitions (0xb0 bytes
    each; the target name is a std::string at +0x90). FT+0x98 points to the PostProcessHolder. FT+0xa0 is the
    descriptor of the node being entered (its name std::string is at FT+0x130).
- **Flow node types** (registered by 0x140403860 into the list 0x1437f8998): "menu" → 0x1404036a0, "process" →
  0x1404036d0, "command" → 0x140403660, "disconnect" → 0x140403620. The dispatcher is 0x141fa0bc0, called from
  the node-creation step 0x141fa8fa0. [static]
  - The class is looked up by the JSON `data.key`, or by the flow name when there is no key.
  - Menu registry: hash table 0x1436f9df0, lookup **0x140e9fce0(const char* key)**.
  - Process registry: hash table 0x1436fee00, lookup **0x14130fa10(const char* key)**.
  - Each registry record is {create fn, "Class::CreateObject" name, flow name, …}. Example: TopMenu/TopMenuNew →
    `TopModeSelect::CreateTaskUnit` 0x140aaf4a0 → class menu::TopModeSelect (vtable 0x14266ddd8). [static, record
    at 0x1434a6670]
- **flow::PostProcessHolder** (vtable 0x142bb9678, task name "ProcessHolder"). Global **0x1437f89d8**, the same
  object as [FT+0x98]. [static]
  - **Every live flow node object (menu or process) is a child task of the holder.** The node creators call
    0x14149fc70(holder, node). [static 0x140a95674, 0x14125f841]
  - Task layout (constructor 0x14149fa80): +0 vtable, **+8 std::string task name**, +0x30 priority, +0x38 parent,
    +0x40 first child, +0x48 next sibling, +0x50 previous sibling (a ring). [static]
  - Menus and processes are named after their flow name. The name is passed from FT+0x38 through the creator. [inferred]
  - holder+0x70/+0x78 is a history stack of 0x38-byte entries {std::string flow name, byte, functor}. The last
    entry is the most recent node. [inferred from StackNextProcess 0x141fac7d0 / CreateExecutionStack 0x141fabdd0]
- **The current node's event table** (rebuilt from the JSON `events` by 0x141faa5f0) is in the global
  **0x1437f89a8**. The object has a vector at +8/+0x10 of 0x68-byte entries: +0 is the full key ("12_freelobby"),
  +0x20 is a short alias.
  - Posting an event goes through 0x141fa1b30, which accepts either the key or the alias. It returns "" when the
    name is not in the table, and then nothing happens. [static]
  - The alias being "freelobby" (the "NN_" prefix removed) is [inferred]. lobby.md saw "freelobby" work live.
- **Message queue.** The global **0x143704e88** is the task/message manager. **0x14149b790(mgr, u32 id, const char*
  text)** queues {id, std::string} under a lock (mgr+0xc0). [static] FlowListener::OnMessage 0x141fa0770 handles:
  [static]
  - 0x1030006 text = **fire an event of the current node** (0x141fa8ed0 → 0x141faa110 → AddFlow). Ignored unless
    the flow accepts events (bit 0 of FT+0x78).
  - 0x1030004 = a node finished. 0x1030007 text = an event variant.
  - 0x1030008 text = **jump to the flow node named in the text**. The descriptor is built by 0x141faddb0: by
    default go "forward" and clear "all". The text may instead be a 5-'@' spec (format not decoded).
  - 0x1010003 text = reset to a flow. 0x103000b text = FlowInterrupt. 0x103000e/0x103000f = lock/unlock
    interrupts. 0x1070003 = disconnect flow.

## Q1. What is the current screen?

**A. Poll the current flow name (best).** [static] It is the std::string at **[0x1437f8990]+0x60** (FT+0x38).
```
ptr 1437f8990 60          -> ADDR (address of the std::string)
read @ADDR 32             -> if bytes 24..31 (capacity) >= 0x10:  ptr 1437f8990 60 0  -> TEXT;  read @TEXT 64
ptr 1437f8990 8           -> address of the step u32 (FT+0x08 = FL+0x30); 0 = idle, anything else = transition running
```
Python: `fl = p.u64(0x1437f8990); print(sstr(p, fl+0x60), p.u32(f"@{fl+0x30:x}"))`. Expect "TopMenu/TopMenuNew" on
the main menu, and e.g. "Online/Lobby/LobbyRoomList" in the room list.

**B. The live node object and its class.** [static layout; that the children are the live nodes is inferred]
```python
h = p.u64(0x1437f89d8); first = c = p.u64(f"@{h+0x40:x}")
while c:
    print(hex(c), rtti(p, c), sstr(p, c+8)); c = p.u64(f"@{c+0x48:x}")
    if c == first: break
```
On the main menu this should list a `.?AVTopModeSelect@menu@@` object named "TopMenu/TopMenuNew".

**C. Trace every screen change (names in plain text).**
- `trace 1414a8d60 flowjson 300 0:100` — the file-exists check. Each transition calls it first with
  "cpk_dat/common/script/flow/<Node>.json" in rcx (call site 0x141fa99f4, return address 0x141fa99f9). It has
  other callers too: filter on that caller. [static]
- `trace 140e9fce0 menunode 200 0:80` and `trace 14130fa10 procnode 200 0:80` — the class key (rcx = char*) of
  every menu node / process node created. TopMenu/TopMenu is special-cased and goes through 0x141312520 instead.
  [static]
- `trace 14149b790 flowmsg 300 2:48` — every queued message: rdx = message id, r8 = text. You see event keys
  (0x1030006 "12_freelobby", "proceed", …), jumps and 0x1071114. [static]
- `trace 141faadc0 step 200` — rdx = new step. One transition is 1→2→3→4→5→6→7→0. [static]

## Q2. Trigger navigation

**Posting an event the way the game does it.** [static] Menus call **0x140e42e60(window, u8 back, const char*
event)** (631 call sites).
- It sets TaskState [win+0x78]: +8 = 1 (forward) or 2 (back), +0x10 = the event name.
- When the window's TaskState is in state 10 it also copies the name to [[win+0x88]+0x3b0].
- With event = NULL it uses "proceed" (0x1427b2d18), or "return" (0x1427b2d0c) when back = 1.
- The window then closes itself, resolves the name in the event table and posts 0x1030006 (0x140e403e0). [static]
- Processes use **0x141311980(any, std::string* event)** (476 call sites). It resolves the name and posts 0x1030006.
  [static]

**Recipe 1 — from the top menu straight to a mode (recommended).** The node must be TopMenu/TopMenuNew.
- menu = the TopModeSelect object (Q1-B; check that [menu] == vtable 0x14266ddd8).
- Its decide handler **0x140ab03f0(menu)** is vtable slot 49 (+0x188), the same function the A button runs. It reads
  `level = u32[menu+0x98]` and `id = u32[menu+0x9c+4*level]`. For a leaf it then:
  1. sets [0x1436fb9c4] = 1;
  2. calls SetMode 0x14149a570([0x143704e38], mode);
  3. posts the item's event (0x140ab19c0 → 0x140e42e60).
  [static 0x140ab0420-0x140ab065c]
- So write the item id, then decide:
```
read @MENU+98 4                     -> LEVEL
write @<MENU+0x9c+4*LEVEL> <id as 4-byte LE>
gcall 140ab03f0 <MENU>              -> poll shows "gcall 140ab03f0 -> …"; the flow name changes within ~1 s
```
- Item ids (from the initializer 0x1401c7fb0) [static]:

  | id | item | mode | event | flow route |
  |---|---|---|---|---|
  | **0x14** | Friendly Match Lobby | 0x1a | `freelobby` | `12_freelobby` → TopMenuModeInit `1_mode_online` → LoginFlow `01_lobby` → Online/Lobby/LobbyProcessModeInit |
  | **0x12** | Online Divisions | 0x18 | rankmatch | `07_rankmatch` |
  | **0x16** | Matchday | 0x3b | compeonline | — |
  | 0x11 | Quick Match | 0x19 | — | — |
  | 0x15 | Team Play Lobby | 0x1b | — | — |
  | 0x13 | Online Co-op | 0x1c | — | — |
  | 0x17 | Online Tournament | 0x36 | — | — |
  | 1 | Exhibition | 8 | — | — |

  Ids are [static]. The event names for 0x12/0x16 come from lobby.md; the routes after login are in lobby.md and
  modes2.md.
- Example (base 0x140000000; write in little-endian): `write @MENU+0x9c+4*L 14000000`, then `gcall 140ab03f0 MENU`.
- Live test: on TopMenuNew, do it for 0x14. Pass if the flow name goes TopMenu/TopMenuModeInit →
  Online/Login/LoginFlow → Online/Lobby/LobbyProcessModeInit, and the server log shows the login and then
  GET_LOBBYLIST. [inferred]

**Recipe 2 — any menu: fire one of its events.** [inferred; it's the same call the game's pad handler makes]
- First list the events of the current node: walk [0x1437f89a8]+8..+0x10 with stride 0x68 and print
  `sstr(e+0)` / `sstr(e+0x20)`.
- Put the name in memory. Use an existing literal, or a scratch area: the unused tail of .rsrc at **0x15b957100**
  (the section is 0x14 bytes long; check that `read 15b957100 64` returns zeros first) [static section table].
  ```
  write 15b957100 <hex of "12_freelobby\0">
  gcall 140ac5390 <[MENU+0x88]+0x3b0> <abs 0x15b957100>     # optional: std::string assign, as the pad handler does
  gcall 140e42e60 <MENU> 0 <abs 0x15b957100>               # back = 0 (proceed). Back/B: gcall 140e42e60 <MENU> 1 0
  ```
- Existing literals: "freelobby" 0x1427103c0, "rankmatch" 0x142710378, "compeonline" 0x1427103a0, "quick"
  0x1427103ac, "proceed" 0x1427b2d18, "return" 0x1427b2d0c. [static]

**Recipe 3 — raw jump to a named node (fallback, risky).** [static mechanism; safety is a guess]
```
gcall 14149b790 <[0x143704e88]> 1030008 <abs ptr to "Online/Lobby/LobbyProcessModeInit\0">
```
- The engine itself uses this for interrupts and resets (0x141fa65d0). It skips mode setup (SetMode, login). The
  old node may also hold the transition at step 4 (CANDIE). Use it only if Recipes 1–2 fail.
- Posting 0x1030006 directly with a full key (`gcall 14149b790 <mgr> 1030006 <ptr "12_freelobby">`) skips the
  menu's own exit and has the same risk. [guess]

**Thread.** Flow tasks run on the main loop, which also pumps window messages. So gcall (run inside PeekMessage)
is on the right thread. [inferred] Check: the `tid=` of a `trace 141fa0770 flowlistener 5` hit should match the
thread that runs your gcalls.

## Q3. Dialogs: detect and confirm

- **Every modal dialog is a menusystem::Dialog** (vtable 0x1427b4488), and every subclass constructor calls the
  base constructor **0x140e46f90(dlg, const char* name, …)**. [static]
  - Callers include DialogProceed 0x140e48470 (OK button), DialogSelect, DialogReturn 0x140e6fd10, DialogMessage
    0x140e59710, DialogAgreement 0x140e60720, menu::FlowDialogProceed 0x140a96a20, and others. Server errors use
    "CommandErrDialog" via 0x1411ca5c0.
  - Detect: `trace 140e46f90 dlgnew 100 1:48`. rcx = dialog object, and the dump of rdx (argument 1) shows the name text, e.g.
    "TaskWarningManner", "CommandErrDialog".
- Dialog fields: [static 0x140e46f90, 0x140e47d00, 0x140e47a00, 0x140e47b20]
  - **+0x268 std::string = the message text** (UTF-8, with `\x11xx` macros).
  - +0xc8 = button label strings (0x20 each).
  - **+0x2ba byte = 1 once closing.**
  - The cursor (button index) is [[dlg+0x88]+0xd8].
- An open dialog is a task that is a child of whichever task opened it (0x14149fc70). When it ends, the parent
  gets message 0x1080005 with the dialog pointer. [static 0x140fdf690]
- Which window has input: the pad dispatcher **0x140e3c810(window, …)** runs for the window taking input.
  `trace 140e3c810 inputwin 10` gives that window in rcx. If its rtti() ends in "Dialog", a modal dialog has focus.
  [inferred: probably called every frame]
- **Confirm a dialog = call its A handler, vtable slot 49 (+0x188).** [static]
  - DialogProceed → 0x140e485f0: button 0, then 0x140e42e60(dlg, 0, NULL) ("proceed"), then close 0x140e47b20.
  - DialogSelect → 0x140e70100: uses the current cursor. To pick button i, first
    `write @<[dlg+0x88]+0xd8> <i LE>`. [live: DialogAgreementCB "Enter this Competition. Proceed?" with i = 1 →
    Yes; `pesflow.py PORT ok 1`]
  - Slot 50 (+0x190) is B: DialogSelect → 0x140e700c0 ("return").
  ```
  read @DLG 8 -> VT ;  read @VT+188 8 -> FN ;  gcall <FN - base + 0x140000000> <DLG>
  ```
  (gcall translates the function address, so give it as a VA.)
- Generic fallback for any dialog: `gcall 140e42e60 <DLG> 0 0` then `gcall 140e47b20 <DLG>`. [inferred]
- Live test: trigger the manner-warning or any server-error dialog, read sstr(DLG+0x268), gcall slot 49. Pass if
  the parent continues (the next command shows in the server log) and +0x2ba becomes 1.

## Q4. "Show dialog with message id" — trace targets

- **0x140e47d00(dlg, u32 msgid)** [static]: copies the msgid's text into dlg+0x268 (the text lookup is
  0x1414969e0) and refreshes. 305 call sites; the typical pattern is DialogProceed::Create 0x140e48570("Name", 0,
  1, kind) followed by 0x140e47d00. `trace 140e47d00 dlgmsg 300` gives rcx = dialog, rdx = msgid. Look the msgid
  up in messages.md: group = msgid >> 16.
- Siblings:
  - 0x140e47db0(dlg, u32* ids, count) joins several ids: `trace 140e47db0 dlgmsgs 100 1:16`.
  - 0x140e47cb0(dlg, std::string* text) sets ready-made text. Server errors with "CODE:…" use it (0x1411caa23);
    read dlg+0x268 afterwards.
  - 0x140e47a00(dlg, button, msgid) sets a button label (0x6c0019 = default OK).
- Together with `trace 140e46f90` this lists every dialog with its name, msgid or text. [static]

## Q5. Title screen, every message, parser bench

See [parsebench.md](parsebench.md): `pesflow.py PORT title` leaves Intro/MenuTitle without keys (0x140a98d00),
`pesflow.py PORT traffic` logs every message inside the game, and `parsebench.py` runs reply parsers on our bytes.

## Not settled statically
- That the holder's children are exactly the live nodes, and that task names are the flow names. Check with Q1-B.
- Whether 0x140e3c810 runs every frame or only on pad input.
- The exact alias format in the event table. Dump it on TopMenuNew.
- Whether a raw 0x1030008 / 0x1030006 jump hangs at the CANDIE step.
- How Matchday / Online Tournament both called "compeonline" are told apart (by mode, after TopMenuModeInit).
