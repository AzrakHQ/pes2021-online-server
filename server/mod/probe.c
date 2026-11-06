/* Probe: a debug-only add-on to the mod for exploring the game from a script (never in the released DLL).
 *
 * Built only with `PROBE=1 sh build.sh` (defines PES_PROBE). When the environment variable PES2021ONLINE_PROBE
 * holds a port number, the mod listens on 127.0.0.1:<port> for one client at a time and answers line commands:
 *
 *   base                          the game's image base (addresses below are the exe's own VAs, 0x14xxxxxxx)
 *   read VA LEN                   hex bytes (fails cleanly on unreadable memory); VA may also be an absolute
 *                                 address written as @hex
 *   write VA HEX                  patch bytes (makes the page writable for the write)
 *   ptr VA OFF [OFF ...]          follow a pointer chain: [[VA]+OFF]+OFF... -> the final address
 *   call VA [A1 A2 A3 A4]         call a function on the probe thread (only for pure readers/getters)
 *   gcall VA [A1 A2 A3 A4]        queue the call for the game's window thread (runs inside its next
 *                                 PeekMessage/TranslateMessage/DispatchMessage call, hooked in user32 itself);
 *                                 "poll" shows the result as a "gcall" event
 *   trace VA [NAME] [MAX] [DEREF] log each call of the function at VA: its four register arguments, the caller
 *                                 and the thread. MAX lines are logged (default 100), later calls only counted.
 *                                 DEREF = "1:64" also dumps 64 bytes at argument 1 (0-based: rcx=0)
 *   untrace VA | traces           remove one trace / list traces and their hit counts
 *   poll                          print and clear the queued events (trace hits, gcall results)
 *   bye                           close the connection
 * Answers: one line "ok ..." or "err ...", except poll (event lines, then "ok N").
 * Trace stubs keep every register and the stack as they were and jump to the original function, so any
 * argument count works; return values are not logged.
 */
#ifdef PES_PROBE

#define PROBE_SLOTS 128
#define PROBE_EVENTS 4096

typedef struct {
    uint64_t va;            /* hooked game VA, 0 = free */
    char name[48];
    uint8_t *stub;          /* executable stub (enter logger, then jmp to the trampoline) */
    void *tramp;
    volatile LONG hits;
    LONG max;
    int deref_arg, deref_len;
} probe_slot_t;

static probe_slot_t g_ps[PROBE_SLOTS];
static CRITICAL_SECTION g_pq_lock;
static char *g_pq[PROBE_EVENTS];
static int g_pq_n;
static volatile LONG g_pq_dropped;

typedef struct { uint64_t fn, a[4], ret; volatile LONG state; } gcall_t;   /* state 0 free, 1 queued, 2 done */
static gcall_t g_gcall[16];

static void probe_event(const char *fmt, ...)
{
    char buf[1024];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
    EnterCriticalSection(&g_pq_lock);
    if (g_pq_n < PROBE_EVENTS) g_pq[g_pq_n++] = _strdup(buf);
    else InterlockedIncrement(&g_pq_dropped);
    LeaveCriticalSection(&g_pq_lock);
}

static BOOL probe_read(const void *p, void *out, size_t n)
{
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), p, out, n, &got) && got == n;
}

static void hexdump(char *out, size_t cap, const uint8_t *b, size_t n)
{
    size_t o = 0;
    for (size_t i = 0; i < n && o + 3 < cap; i++) o += snprintf(out + o, cap - o, "%02x", b[i]);
    out[o < cap ? o : cap - 1] = 0;
}

/* Called from a stub: regs -> [r9, r8, rdx, rcx, return address] as the stub pushed them. */
static void __cdecl probe_enter(uint64_t slot, uint64_t *regs)
{
    probe_slot_t *s = &g_ps[slot];
    LONG n = InterlockedIncrement(&s->hits);
    if (n > s->max) return;
    uint64_t a[4] = { regs[3], regs[2], regs[1], regs[0] };
    uint64_t ret = regs[4], rel = (ret >= (uint64_t)g_exe && ret < (uint64_t)g_exe + 0x4000000) ? ret - (uint64_t)g_exe + PES_BASE : ret;
    char extra[300] = "";
    if (s->deref_len > 0 && s->deref_arg >= 0 && s->deref_arg < 4) {
        uint8_t b[128]; int len = s->deref_len > 128 ? 128 : s->deref_len;
        if (probe_read((void *)a[s->deref_arg], b, len)) { strcpy(extra, " *="); hexdump(extra + 3, sizeof extra - 3, b, len); }
        else strcpy(extra, " *=?");
    }
    probe_event("hit %s #%ld tid=%lu caller=%llx rcx=%llx rdx=%llx r8=%llx r9=%llx%s", s->name, (long)n,
                (unsigned long)GetCurrentThreadId(), (unsigned long long)rel, (unsigned long long)a[0],
                (unsigned long long)a[1], (unsigned long long)a[2], (unsigned long long)a[3], extra);
}

static size_t g_stub_tramp_off;                               /* where make_stub puts the trampoline address */

static uint8_t *make_stub(uint64_t slot)
{
    static const uint8_t head[] = {
        0x51, 0x52, 0x41, 0x50, 0x41, 0x51,                    /* push rcx, rdx, r8, r9 */
        0x48, 0x83, 0xEC, 0x68,                                /* sub rsp, 0x68 (shadow + 4 xmm, aligned) */
        0xF3, 0x0F, 0x7F, 0x44, 0x24, 0x28, 0xF3, 0x0F, 0x7F, 0x4C, 0x24, 0x38,   /* movdqu [rsp+28/38], xmm0/1 */
        0xF3, 0x0F, 0x7F, 0x54, 0x24, 0x48, 0xF3, 0x0F, 0x7F, 0x5C, 0x24, 0x58,   /* movdqu [rsp+48/58], xmm2/3 */
        0x48, 0xB9 };                                          /* mov rcx, imm64 (slot) */
    static const uint8_t mid[] = { 0x48, 0x8D, 0x54, 0x24, 0x68, 0x48, 0xB8 };   /* lea rdx,[rsp+0x68]; mov rax, imm64 */
    static const uint8_t tail[] = {
        0xFF, 0xD0,                                            /* call rax */
        0xF3, 0x0F, 0x6F, 0x44, 0x24, 0x28, 0xF3, 0x0F, 0x6F, 0x4C, 0x24, 0x38,
        0xF3, 0x0F, 0x6F, 0x54, 0x24, 0x48, 0xF3, 0x0F, 0x6F, 0x5C, 0x24, 0x58,
        0x48, 0x83, 0xC4, 0x68,                                /* add rsp, 0x68 */
        0x41, 0x59, 0x41, 0x58, 0x5A, 0x59,                    /* pop r9, r8, rdx, rcx */
        0xFF, 0x25, 0, 0, 0, 0 };                              /* jmp [rip+0] -> trampoline (next 8 bytes) */
    uint8_t *p = VirtualAlloc(NULL, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!p) return NULL;
    uint8_t *q = p;
    uint64_t fn = (uint64_t)probe_enter;
    memcpy(q, head, sizeof head); q += sizeof head;
    memcpy(q, &slot, 8); q += 8;
    memcpy(q, mid, sizeof mid); q += sizeof mid;
    memcpy(q, &fn, 8); q += 8;
    memcpy(q, tail, sizeof tail); q += sizeof tail;
    memset(q, 0, 8);                                           /* trampoline address, filled in later */
    g_stub_tramp_off = (size_t)(q - p);
    return p;
}

static const char *probe_trace(uint64_t va, const char *name, LONG max, const char *deref)
{
    int free_i = -1;
    for (int i = 0; i < PROBE_SLOTS; i++) {
        if (g_ps[i].va == va) return "already traced";
        if (!g_ps[i].va && free_i < 0) free_i = i;
    }
    if (free_i < 0) return "no free trace slot";
    probe_slot_t *s = &g_ps[free_i];
    if (!s->stub && !(s->stub = make_stub((uint64_t)free_i))) return "VirtualAlloc failed";
    memset(s->name, 0, sizeof s->name);
    if (!name || !*name || !strcmp(name, "-")) snprintf(s->name, sizeof s->name, "%llx", (unsigned long long)va);
    else snprintf(s->name, sizeof s->name, "%s", name);
    s->hits = 0; s->max = max > 0 ? max : 100; s->deref_arg = -1; s->deref_len = 0;
    if (deref && *deref) sscanf(deref, "%d:%d", &s->deref_arg, &s->deref_len);
    void *target = game_va(va);
    MH_STATUS st = MH_CreateHook(target, s->stub, &s->tramp);
    if (st != MH_OK) return MH_StatusToString(st);
    memcpy(s->stub + g_stub_tramp_off, &s->tramp, 8);         /* the operand of the stub's final jmp [rip+0] */
    FlushInstructionCache(GetCurrentProcess(), s->stub, 256);
    st = MH_EnableHook(target);
    if (st != MH_OK) { MH_RemoveHook(target); return MH_StatusToString(st); }
    s->va = va;
    return NULL;
}

static void probe_untrace(uint64_t va)
{
    for (int i = 0; i < PROBE_SLOTS; i++)
        if (g_ps[i].va == va) { MH_DisableHook(game_va(va)); MH_RemoveHook(game_va(va)); g_ps[i].va = 0; }
}

typedef uint64_t (__cdecl *fn4_t)(uint64_t, uint64_t, uint64_t, uint64_t);

static void probe_drain_gcalls(void)
{
    for (int i = 0; i < 16; i++)
        if (g_gcall[i].state == 1) {
            uint64_t r = ((fn4_t)game_va(g_gcall[i].fn))(g_gcall[i].a[0], g_gcall[i].a[1], g_gcall[i].a[2], g_gcall[i].a[3]);
            probe_event("gcall %llx -> %llx", (unsigned long long)g_gcall[i].fn, (unsigned long long)r);
            g_gcall[i].ret = r;
            InterlockedExchange(&g_gcall[i].state, 0);
        }
}

static BOOL (WINAPI *o_TranslateMessage)(const MSG *);
static LRESULT (WINAPI *o_DispatchMessageW)(const MSG *);
static BOOL (WINAPI *o_PeekMessageW)(LPMSG, HWND, UINT, UINT, UINT);
static BOOL (WINAPI *o_PeekMessageA)(LPMSG, HWND, UINT, UINT, UINT);
static LRESULT (WINAPI *o_DispatchMessageA)(const MSG *);
static BOOL WINAPI hk_PeekMessageW(LPMSG m, HWND h, UINT a, UINT b, UINT r) { probe_drain_gcalls(); return o_PeekMessageW(m, h, a, b, r); }
static BOOL WINAPI hk_PeekMessageA(LPMSG m, HWND h, UINT a, UINT b, UINT r) { probe_drain_gcalls(); return o_PeekMessageA(m, h, a, b, r); }
static LRESULT WINAPI hk_DispatchMessageA(const MSG *m) { probe_drain_gcalls(); return o_DispatchMessageA(m); }
static BOOL WINAPI hk_TranslateMessage(const MSG *m) { probe_drain_gcalls(); return o_TranslateMessage(m); }
static LRESULT WINAPI hk_DispatchMessageW(const MSG *m) { probe_drain_gcalls(); return o_DispatchMessageW(m); }

static uint64_t parse_va(const char *s, BOOL *abs)
{
    *abs = s[0] == '@';
    return strtoull(s + (*abs ? 1 : 0), NULL, 16);
}

static void *va_ptr(uint64_t v, BOOL abs) { return abs ? (void *)v : game_va(v); }

static void probe_reply(SOCKET c, const char *fmt, ...)
{
    char buf[8300];
    va_list ap; va_start(ap, fmt); int n = vsnprintf(buf, sizeof buf - 2, fmt, ap); va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof buf - 2) n = sizeof buf - 2;
    buf[n++] = '\n';
    send(c, buf, n, 0);
}

static void probe_command(SOCKET c, char *line)
{
    char *argv[12]; int argc = 0;
    for (char *t = strtok(line, " \t\r\n"); t && argc < 12; t = strtok(NULL, " \t\r\n")) argv[argc++] = t;
    if (!argc) return;
    const char *cmd = argv[0];
    BOOL abs;
    if (!strcmp(cmd, "base")) { probe_reply(c, "ok %p", g_exe); return; }
    if (!strcmp(cmd, "read") && argc >= 3) {
        uint64_t va = parse_va(argv[1], &abs); int n = atoi(argv[2]);
        if (n <= 0 || n > 4096) { probe_reply(c, "err length 1..4096"); return; }
        uint8_t b[4096]; char hex[8193];
        if (!probe_read(va_ptr(va, abs), b, n)) { probe_reply(c, "err unreadable"); return; }
        hexdump(hex, sizeof hex, b, n); probe_reply(c, "ok %s", hex); return;
    }
    if (!strcmp(cmd, "write") && argc >= 3) {
        uint64_t va = parse_va(argv[1], &abs); size_t n = strlen(argv[2]) / 2; uint8_t b[1024];
        if (n == 0 || n > sizeof b) { probe_reply(c, "err 1..1024 bytes"); return; }
        for (size_t i = 0; i < n; i++) { unsigned v; sscanf(argv[2] + 2 * i, "%2x", &v); b[i] = (uint8_t)v; }
        DWORD old; void *p = va_ptr(va, abs);
        if (!VirtualProtect(p, n, PAGE_EXECUTE_READWRITE, &old)) { probe_reply(c, "err protect"); return; }
        memcpy(p, b, n); VirtualProtect(p, n, old, &old); FlushInstructionCache(GetCurrentProcess(), p, n);
        probe_reply(c, "ok %u", (unsigned)n); return;
    }
    if (!strcmp(cmd, "ptr") && argc >= 2) {
        uint64_t a = (uint64_t)va_ptr(parse_va(argv[1], &abs), abs);
        for (int i = 2; i < argc; i++) {
            uint64_t v;
            if (!probe_read((void *)a, &v, 8)) { probe_reply(c, "err unreadable at %llx", (unsigned long long)a); return; }
            a = v + strtoull(argv[i], NULL, 16);
        }
        probe_reply(c, "ok %llx", (unsigned long long)a); return;
    }
    if ((!strcmp(cmd, "call") || !strcmp(cmd, "gcall")) && argc >= 2) {
        uint64_t fn = strtoull(argv[1], NULL, 16), a[4] = { 0 };
        for (int i = 0; i < 4 && i + 2 < argc; i++) a[i] = strtoull(argv[i + 2], NULL, 16);
        if (!strcmp(cmd, "call")) {
            uint64_t r = ((fn4_t)game_va(fn))(a[0], a[1], a[2], a[3]);
            probe_reply(c, "ok %llx", (unsigned long long)r); return;
        }
        for (int i = 0; i < 16; i++)
            if (InterlockedCompareExchange(&g_gcall[i].state, 3, 0) == 0) {
                g_gcall[i].fn = fn; memcpy(g_gcall[i].a, a, sizeof a); InterlockedExchange(&g_gcall[i].state, 1);
                probe_reply(c, "ok queued"); return;
            }
        probe_reply(c, "err queue full"); return;
    }
    if (!strcmp(cmd, "trace") && argc >= 2) {
        const char *e = probe_trace(strtoull(argv[1], NULL, 16), argc > 2 ? argv[2] : "", argc > 3 ? atol(argv[3]) : 0,
                                    argc > 4 ? argv[4] : "");
        if (e) probe_reply(c, "err %s", e); else probe_reply(c, "ok"); return;
    }
    if (!strcmp(cmd, "untrace") && argc >= 2) { probe_untrace(strtoull(argv[1], NULL, 16)); probe_reply(c, "ok"); return; }
    if (!strcmp(cmd, "traces")) {
        int n = 0;
        for (int i = 0; i < PROBE_SLOTS; i++)
            if (g_ps[i].va) { probe_reply(c, "trace %llx %s hits=%ld", (unsigned long long)g_ps[i].va, g_ps[i].name, (long)g_ps[i].hits); n++; }
        probe_reply(c, "ok %d", n); return;
    }
    if (!strcmp(cmd, "poll")) {
        char *ev[PROBE_EVENTS]; int n;
        EnterCriticalSection(&g_pq_lock);
        n = g_pq_n; memcpy(ev, g_pq, n * sizeof ev[0]); g_pq_n = 0;
        LeaveCriticalSection(&g_pq_lock);
        for (int i = 0; i < n; i++) { probe_reply(c, "%s", ev[i]); free(ev[i]); }
        LONG d = InterlockedExchange(&g_pq_dropped, 0);
        probe_reply(c, "ok %d%s", n, d ? " (some dropped)" : ""); return;
    }
    probe_reply(c, "err unknown command");
}

static DWORD WINAPI probe_thread(LPVOID arg)
{
    int port = (int)(intptr_t)arg;
    WSADATA w; WSAStartup(MAKEWORD(2, 2), &w);
    SOCKET l = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    struct sockaddr_in a = { 0 };
    a.sin_family = AF_INET; a.sin_port = htons((u_short)port); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (l == INVALID_SOCKET || bind(l, (struct sockaddr *)&a, sizeof a) || listen(l, 1)) {
        logf_("probe: can't listen on 127.0.0.1:%d", port); return 0;
    }
    logf_("probe listening on 127.0.0.1:%d", port);
    for (;;) {
        SOCKET c = accept(l, NULL, NULL);
        if (c == INVALID_SOCKET) continue;
        char buf[4096]; int have = 0;
        for (;;) {
            int r = recv(c, buf + have, sizeof buf - 1 - have, 0);
            if (r <= 0) break;
            have += r; buf[have] = 0;
            char *nl;
            int quit = 0;
            while ((nl = strchr(buf, '\n'))) {
                *nl = 0;
                if (!strncmp(buf, "bye", 3)) quit = 1; else probe_command(c, buf);
                memmove(buf, nl + 1, have - (nl + 1 - buf) + 1); have -= (int)(nl + 1 - buf);
            }
            if (quit || have >= (int)sizeof buf - 1) break;
        }
        closesocket(c);
    }
}

static void probe_start(void)
{
    char v[16];
    if (!GetEnvironmentVariableA("PES2021ONLINE_PROBE", v, sizeof v)) return;
    int port = atoi(v);
    if (port <= 0 || port > 65535) return;
    InitializeCriticalSection(&g_pq_lock);
    if (!g_exe) g_exe = (uint8_t *)GetModuleHandleW(NULL);
    hook(L"user32.dll", "TranslateMessage", hk_TranslateMessage, (void **)&o_TranslateMessage);
    hook(L"user32.dll", "DispatchMessageW", hk_DispatchMessageW, (void **)&o_DispatchMessageW);
    hook(L"user32.dll", "DispatchMessageA", hk_DispatchMessageA, (void **)&o_DispatchMessageA);
    hook(L"user32.dll", "PeekMessageW", hk_PeekMessageW, (void **)&o_PeekMessageW);
    hook(L"user32.dll", "PeekMessageA", hk_PeekMessageA, (void **)&o_PeekMessageA);
    MH_EnableHook(MH_ALL_HOOKS);
    CreateThread(NULL, 0, probe_thread, (LPVOID)(intptr_t)port, 0, NULL);
}

#else
static void probe_start(void) {}
#endif
