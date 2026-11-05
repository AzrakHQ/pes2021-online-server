/*
 * PES 2021 Online - drop-in mod for eFootball PES 2021 (PC, Steam, v1.01.00).
 *
 * The host downloads dinput8.dll from their server's admin page and sends it to their friends, who
 * put it next to PES2021.exe. Nothing else on the PC changes: no hosts file, no certificates, no
 * administrator rights. Delete the file to undo.
 *
 * How it works, inside the game process only:
 *   - Each copy carries its server's identity (an Ed25519 public key) and last known addresses,
 *     written into the reserved settings area below by the server when it hands out the file.
 *   - The game looks up Konami's server names; the mod answers with the server's address instead
 *     (hooks on ws2_32 getaddrinfo / GetAddrInfoW / GetAddrInfoExW / gethostbyname).
 *   - An address is used only after it proves it is that server: the mod sends a random nonce to
 *     /mod/hello and checks the server's signature over it.
 *   - When the server restarts or its IP changes, the mod finds it again from a signed "note" (where
 *     the server is now), fetched from the server's web address or from the address book; notes not
 *     signed by the server's key are ignored.
 *   - The game's network test fetches a page over HTTPS from ntl*.service.konami.net; for those four
 *     names only, the mod tells WinHTTP to accept the server's own certificate.
 *   - Holding Shift while the game starts opens a small box to switch to another server by address.
 *   - Player id: copies of the game that don't really run through Steam all report the same Steam id,
 *     so the server can't tell their players apart by it. The mod names its install instead: a random
 *     secret kept in pes2021-online.ini, mixed with this PC's Windows machine id (so a game folder
 *     copied to another PC is a new player). It sends the hash as one line, "PESID1 <32 hex>\n", at
 *     the start of each connection to the game server's port 10000, and only to servers whose
 *     /mod/hello answer says they read it (version 2).
 * The game's own files and code are not modified. dinput8 itself is forwarded to the system DLL.
 *
 * MIT license (this file). MinHook (BSD 2-clause) hooks; Monocypher (CC0/BSD 2-clause) checks signatures.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0602
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>
#include "minhook/include/MinHook.h"
#include "monocypher/monocypher.h"
#include "monocypher/monocypher-ed25519.h"

#define MOD_VERSION "0.3.0"

/* Reserved settings area: the server overwrites everything after the 32-byte marker with
 * "key=...\nname=...\nurl=...\naddrs=a,b\nbook=...\n" (see server/pesserver/identity.py pack_mod). */
__attribute__((section(".pescfg"), used))
volatile char g_embedded[2048] = "PES2021ONLINE-EMBEDDED-SETTINGS";
#define EMBED_OFFSET 32

static const char *KONAMI_NAMES[] = {
    "pes21-x64-gate.cs.konami.net", "pes21-x64-stun.cs.konami.net", "ntl.service.konami.net",
    "ntleu.service.konami.net", "ntlus.service.konami.net", "ntljp.service.konami.net", NULL};

#define MAXADDR 8
typedef struct {
    char keyhex[65];              /* server identity ("" = none) */
    unsigned char key[32];
    char name[128];
    char url[512];                /* web address, if the host has a name for the server (optional) */
    char addrs[MAXADDR][64];      /* game addresses, best first */
    int naddrs;
    char book[512];               /* address book base URL */
    int hello_v;                  /* the server's /mod/hello version: 2 = reads the player-id line */
} server_t;

static server_t g_srv;
static wchar_t g_ini[MAX_PATH];
static char g_cache[64];          /* ini section for this server's remembered addresses */
static char g_ip[64];             /* the verified game address in use ("" = none yet) */
static BOOL g_force_prompt, g_offline;
static volatile LONG g_started;
static HANDLE g_ready;            /* set when the first background lookup has finished */
static DWORD g_last_try;
static CRITICAL_SECTION g_lock;
static FILE *g_log;
static volatile LONG g_id_on;     /* the server in use reads the player-id line */
static char g_id_line[48];        /* "PESID1 <32 hex>\n" ("" = no id) */

/* ------------------------------------------------------------------------------------------ log */
static void logf_(const char *fmt, ...)
{
    if (!g_log) return;
    SYSTEMTIME t; GetLocalTime(&t);
    fprintf(g_log, "%02d:%02d:%02d.%03d ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_list a; va_start(a, fmt); vfprintf(g_log, fmt, a); va_end(a);
    fputc('\n', g_log); fflush(g_log);
}

/* -------------------------------------------------------------------------------- dinput8 proxy */
static HMODULE real_dinput8(void)
{
    static HMODULE h;
    if (!h) {
        wchar_t p[MAX_PATH];
        UINT n = GetSystemDirectoryW(p, MAX_PATH);
        if (n && n < MAX_PATH - 16) { wcscat(p, L"\\dinput8.dll"); h = LoadLibraryW(p); }
        if (!h) logf_("ERROR: could not load the system dinput8.dll (%lu)", GetLastError());
    }
    return h;
}
#define REAL(name) (real_dinput8() ? (void *)GetProcAddress(real_dinput8(), name) : NULL)

HRESULT WINAPI DirectInput8Create(HINSTANCE i, DWORD v, REFIID r, LPVOID *o, void *u)
{
    HRESULT (WINAPI *f)(HINSTANCE, DWORD, REFIID, LPVOID *, void *) = REAL("DirectInput8Create");
    return f ? f(i, v, r, o, u) : E_FAIL;
}
HRESULT WINAPI DllCanUnloadNow(void)
{
    HRESULT (WINAPI *f)(void) = REAL("DllCanUnloadNow");
    return f ? f() : S_FALSE;
}
HRESULT WINAPI DllGetClassObject(REFCLSID c, REFIID r, LPVOID *p)
{
    HRESULT (WINAPI *f)(REFCLSID, REFIID, LPVOID *) = REAL("DllGetClassObject");
    return f ? f(c, r, p) : E_FAIL;
}
HRESULT WINAPI DllRegisterServer(void)
{
    HRESULT (WINAPI *f)(void) = REAL("DllRegisterServer");
    return f ? f() : E_FAIL;
}
HRESULT WINAPI DllUnregisterServer(void)
{
    HRESULT (WINAPI *f)(void) = REAL("DllUnregisterServer");
    return f ? f() : E_FAIL;
}
const void *WINAPI GetdfDIJoystick(void)
{
    const void *(WINAPI *f)(void) = REAL("GetdfDIJoystick");
    return f ? f() : NULL;
}

/* ------------------------------------------------------------------------------------ helpers */
static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static BOOL parse_key(const char *hex, server_t *s)
{
    if (strlen(hex) != 64) return FALSE;
    for (int i = 0; i < 32; i++) {
        int a = hexval(hex[2 * i]), b = hexval(hex[2 * i + 1]);
        if (a < 0 || b < 0) return FALSE;
        s->key[i] = (unsigned char)(a * 16 + b);
    }
    for (int i = 0; i < 64; i++) s->keyhex[i] = (char)(hex[i] | 0x20);   /* lower case */
    s->keyhex[64] = 0;
    return TRUE;
}
static int b64val(char c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}
static int b64decode(const char *in, size_t n, unsigned char *out, int cap)
{
    int len = 0, bits = 0; unsigned acc = 0;
    for (size_t i = 0; i < n && in[i] != '='; i++) {
        int v = b64val(in[i]);
        if (v < 0) return -1;
        acc = (acc << 6) | (unsigned)v; bits += 6;
        if (bits >= 8) { bits -= 8; if (len >= cap) return -1; out[len++] = (unsigned char)(acc >> bits); }
    }
    return len;
}
static void add_addr(server_t *s, const char *a)
{
    if (!a[0] || s->naddrs >= MAXADDR || strlen(a) >= 64) return;
    for (int i = 0; i < s->naddrs; i++) if (!strcmp(s->addrs[i], a)) return;
    strcpy(s->addrs[s->naddrs++], a);
}
static void trim(char *s)
{
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '/')) s[--n] = 0;
}

/* --------------------------------------------------------------------------------- settings */
static void ini_get(const char *sec, const char *key, char *out, DWORD cap)
{
    wchar_t ws[64], wk[64], wv[600];
    MultiByteToWideChar(CP_UTF8, 0, sec, -1, ws, 64); MultiByteToWideChar(CP_UTF8, 0, key, -1, wk, 64);
    GetPrivateProfileStringW(ws, wk, L"", wv, 600, g_ini);
    WideCharToMultiByte(CP_UTF8, 0, wv, -1, out, (int)cap, NULL, NULL);
}
static void ini_set(const char *sec, const char *key, const char *val)
{
    wchar_t ws[64], wk[64], wv[600];
    MultiByteToWideChar(CP_UTF8, 0, sec, -1, ws, 64); MultiByteToWideChar(CP_UTF8, 0, key, -1, wk, 64);
    MultiByteToWideChar(CP_UTF8, 0, val, -1, wv, 600);
    if (!WritePrivateProfileStringW(ws, wk, wv, g_ini))
        logf_("note: could not save %s to the settings file (%lu)", key, GetLastError());
}

static void load_settings(void)
{
    char buf[sizeof g_embedded], *line, *ctx = NULL, over[80] = "", embedded_key[65] = "";
    for (size_t i = 0; i < sizeof buf; i++) buf[i] = g_embedded[i];     /* volatile copy */
    buf[sizeof buf - 1] = 0;
    for (line = strtok_r(buf + EMBED_OFFSET, "\n", &ctx); line; line = strtok_r(NULL, "\n", &ctx)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0; char *v = eq + 1; trim(v);
        if (!strcmp(line, "key")) { if (parse_key(v, &g_srv)) strcpy(embedded_key, g_srv.keyhex); }
        else if (!strcmp(line, "name")) snprintf(g_srv.name, sizeof g_srv.name, "%s", v);
        else if (!strcmp(line, "url")) snprintf(g_srv.url, sizeof g_srv.url, "%s", v);
        else if (!strcmp(line, "book")) snprintf(g_srv.book, sizeof g_srv.book, "%s", v);
        else if (!strcmp(line, "addrs")) {
            char *c2 = NULL;
            for (char *a = strtok_r(v, ",", &c2); a; a = strtok_r(NULL, ",", &c2)) add_addr(&g_srv, a);
        }
    }
    /* A server picked with the Shift box applies only while this same DLL is in place. */
    ini_get("server", "chosen_with", over, sizeof over);
    if (over[0] && !strcmp(over, embedded_key[0] ? embedded_key : "none")) {
        char k[80], u[512];
        ini_get("server", "key", k, sizeof k); ini_get("server", "url", u, sizeof u);
        server_t s; memset(&s, 0, sizeof s);
        if (parse_key(k, &s)) {
            snprintf(s.url, sizeof s.url, "%s", u);
            snprintf(s.book, sizeof s.book, "%s", g_srv.book);
            snprintf(s.name, sizeof s.name, "%s", u);
            g_srv = s;
        }
    }
    snprintf(g_cache, sizeof g_cache, "cache-%.16s", g_srv.keyhex);
}

/* ---------------------------------------------------------------------------------- player id */
/* id = BLAKE2b-128("PES2021ONLINE-ID 1\n" secret "\n" machine id "\n"). The secret is random and kept in
 * the ini, so the id survives moving the folder; the machine id makes a folder copied to another PC (ini
 * included) a different player. If the ini can't be written, the folder path stands in for the secret. */
static void machine_id(char *out, DWORD cap)
{
    wchar_t w[128]; DWORD sz = sizeof w; HKEY k;
    out[0] = 0;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Cryptography", 0, KEY_READ | KEY_WOW64_64KEY, &k))
        return;
    if (!RegQueryValueExW(k, L"MachineGuid", NULL, NULL, (BYTE *)w, &sz) && sz >= sizeof(wchar_t)) {
        w[sz / sizeof(wchar_t) < 128 ? sz / sizeof(wchar_t) : 127] = 0;
        WideCharToMultiByte(CP_UTF8, 0, w, -1, out, (int)cap, NULL, NULL);
    }
    RegCloseKey(k);
}

static void make_player_id(void)
{
    char secret[MAX_PATH * 3] = "", back[80] = "", mid[128], msg[MAX_PATH * 3 + 200];
    const char *from = "saved";
    ini_get("player", "secret", secret, 80);
    if (strlen(secret) != 64) {
        unsigned char r[32];
        secret[0] = 0;
        if (BCryptGenRandom(NULL, r, sizeof r, BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0) {
            for (int i = 0; i < 32; i++) sprintf(secret + 2 * i, "%02x", r[i]);
            ini_set("player", "secret", secret);
            ini_get("player", "secret", back, sizeof back);
            from = "new";
        }
        if (!secret[0] || strcmp(back, secret)) {  /* not saved: this folder's path is stable instead */
            WideCharToMultiByte(CP_UTF8, 0, g_ini, -1, secret, sizeof secret, NULL, NULL);
            from = "folder";
        }
    }
    machine_id(mid, sizeof mid);
    snprintf(msg, sizeof msg, "PES2021ONLINE-ID 1\n%s\n%s\n", secret, mid);
    unsigned char h[16];
    crypto_blake2b(h, sizeof h, (const uint8_t *)msg, strlen(msg));
    crypto_wipe(msg, sizeof msg); crypto_wipe(secret, sizeof secret);
    strcpy(g_id_line, "PESID1 ");
    for (int i = 0; i < 16; i++) sprintf(g_id_line + 7 + 2 * i, "%02x", h[i]);
    strcat(g_id_line, "\n");
    logf_("player id %.8s... (%s secret%s)", g_id_line + 7, from, mid[0] ? "" : ", no machine id");
}

/* --------------------------------------------------------------------------------------- HTTP */
static BOOL http_get(const char *url8, char *out, DWORD cap, DWORD timeout_ms)
{
    URL_COMPONENTS uc; wchar_t url[700], host[256], path[1024];
    BOOL ok = FALSE; DWORD got = 0, status = 0, sz = sizeof status;
    HINTERNET s = NULL, c = NULL, r = NULL;
    MultiByteToWideChar(CP_UTF8, 0, url8, -1, url, 700);
    memset(&uc, 0, sizeof uc); uc.dwStructSize = sizeof uc;
    uc.lpszHostName = host; uc.dwHostNameLength = 256; uc.lpszUrlPath = path; uc.dwUrlPathLength = 1024;
    uc.dwExtraInfoLength = (DWORD)-1;         /* ask for a pointer to "?query" */
    out[0] = 0;
    if (!WinHttpCrackUrl(url, 0, 0, &uc)) return FALSE;
    s = WinHttpOpen(L"PES2021Online/" MOD_VERSION, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME,
                    WINHTTP_NO_PROXY_BYPASS, 0);
    if (!s) goto done;
    WinHttpSetTimeouts(s, (int)timeout_ms, (int)timeout_ms, (int)timeout_ms, (int)timeout_ms);
    c = WinHttpConnect(s, host, uc.nPort, 0);
    if (!c) goto done;
    {
        wchar_t full[1200];
        _snwprintf(full, 1200, L"%ls%.*ls", path[0] ? path : L"/",
                   uc.lpszExtraInfo ? (int)uc.dwExtraInfoLength : 0, uc.lpszExtraInfo ? uc.lpszExtraInfo : L"");
        full[1199] = 0;
        r = WinHttpOpenRequest(c, L"GET", full, NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                               uc.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0);
    }
    if (!r) goto done;
    if (!WinHttpSendRequest(r, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0)
        || !WinHttpReceiveResponse(r, NULL))
        goto done;
    WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX,
                        &status, &sz, WINHTTP_NO_HEADER_INDEX);
    if (status != 200) goto done;
    for (;;) {
        DWORD n = 0;
        if (got + 1 >= cap || !WinHttpReadData(r, out + got, cap - 1 - got, &n) || !n) break;
        got += n;
    }
    out[got] = 0; ok = TRUE;
done:
    if (!ok) logf_("GET %s failed (status %lu, error %lu)", url8, status, GetLastError());
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    if (s) WinHttpCloseHandle(s);
    return ok;
}

/* "<base64 sig>\n<body>": checks the signature with `key`; returns the body or NULL. */
static char *signed_body(char *text, const unsigned char key[32])
{
    unsigned char sig[64];
    char *nl = strchr(text, '\n');
    if (!nl) return NULL;
    if (b64decode(text, (size_t)(nl - text), sig, 64) != 64) return NULL;
    if (crypto_ed25519_check(sig, key, (const uint8_t *)nl + 1, strlen(nl + 1)) != 0) return NULL;
    return nl + 1;
}

/* Does `ip` prove it is server `s`? (signed reply to a fresh random nonce)
 * With an empty s->keyhex (a server typed into the Shift box), it learns the key from the reply. */
static BOOL hello(const char *ip, server_t *s)
{
    unsigned char rnd[16]; char nonce[33], url[160], body[1024];
    if (BCryptGenRandom(NULL, rnd, sizeof rnd, BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) return FALSE;
    for (int i = 0; i < 16; i++) sprintf(nonce + 2 * i, "%02x", rnd[i]);
    snprintf(url, sizeof url, "http://%s:8080/mod/hello?n=%s&v=2", ip, nonce);
    if (!http_get(url, body, sizeof body, 2500)) return FALSE;
    char *nl = strchr(body, '\n');
    if (!nl) return FALSE;
    char claimed[65] = "";
    int v = 0;                                /* older servers ignore v=2 and answer version 1 */
    if (sscanf(nl + 1, "PES2021ONLINE-HELLO %d\n%64[0-9a-f]\n", &v, claimed) != 2 || (v != 1 && v != 2)) return FALSE;
    server_t learned; memset(&learned, 0, sizeof learned);
    if (!s->keyhex[0]) {                      /* first contact with a typed address: trust on first use */
        if (!parse_key(claimed, &learned)) return FALSE;
    }
    const unsigned char *key = s->keyhex[0] ? s->key : learned.key;
    char *b = signed_body(body, key);
    char expect[200];
    snprintf(expect, sizeof expect, "PES2021ONLINE-HELLO %d\n%s\n%s\n", v, s->keyhex[0] ? s->keyhex : claimed, nonce);
    if (!b || strcmp(b, expect)) { logf_("address %s is not this server (bad proof)", ip); return FALSE; }
    if (!s->keyhex[0]) { memcpy(s->key, learned.key, 32); strcpy(s->keyhex, claimed); }
    s->hello_v = v;
    return TRUE;
}

/* Fetch a note ("where the server is now") from `url`; on success fill addrs/url and return its time. */
static long long fetch_note(const char *url, server_t *s, server_t *into)
{
    char body[2048];
    if (!s->keyhex[0] || !http_get(url, body, sizeof body, 6000)) return -1;
    char *b = signed_body(body, s->key);
    if (!b) { logf_("note from %s is not signed by this server; ignored", url); return -1; }
    char *ctx = NULL, *line = strtok_r(b, "\n", &ctx);
    if (!line || strcmp(line, "PES2021ONLINE-NOTE 1")) return -1;
    char *tl = strtok_r(NULL, "\n", &ctx), *ul = strtok_r(NULL, "\n", &ctx);
    if (!tl || !ul) return -1;
    long long t = atoll(tl);
    into->naddrs = 0;
    if (strcmp(ul, "-")) snprintf(into->url, sizeof into->url, "%s", ul);
    for (char *a = strtok_r(NULL, "\n", &ctx); a; a = strtok_r(NULL, "\n", &ctx)) add_addr(into, a);
    return t;
}

static BOOL try_addr(const char *ip)
{
    if (!ip[0] || !hello(ip, &g_srv)) return FALSE;
    snprintf(g_ip, sizeof g_ip, "%s", ip);
    ini_set(g_cache, "last_ip", ip);
    InterlockedExchange(&g_id_on, g_srv.hello_v >= 2 && g_id_line[0]);
    logf_("server \"%s\" is at %s (verified%s)", g_srv.name, ip, g_id_on ? ", sends player id" : ", older server: no player id");
    return TRUE;
}

/* Find and verify the server's current game address. Fills g_ip. */
static BOOL resolve(void)
{
    char cached[64] = "", tbuf[32] = "", url[600];
    g_ip[0] = 0;
    if (!g_srv.keyhex[0]) { logf_("no server set up"); return FALSE; }
    /* 1. where it was last time */
    ini_get(g_cache, "last_ip", cached, sizeof cached);
    if (try_addr(cached)) return TRUE;
    /* 2. the newest signed note: from its web address, or the address book */
    ini_get(g_cache, "last_t", tbuf, sizeof tbuf);
    long long best_t = atoll(tbuf);
    server_t best = g_srv, n;
    best.naddrs = 0;                          /* the file's own addresses are tried last, once */
    char known_url[512];
    ini_get(g_cache, "url", known_url, sizeof known_url);
    const char *urls[2] = {known_url[0] ? known_url : g_srv.url, g_srv.url};
    for (int i = 0; i < 2; i++) {
        if (!urls[i][0] || (i == 1 && !strcmp(urls[0], urls[1]))) continue;
        snprintf(url, sizeof url, "%s/mod/note", urls[i]);
        n = g_srv;
        long long t = fetch_note(url, &g_srv, &n);
        if (t >= 0 && t >= best_t) { best = n; best_t = t; }
    }
    if (g_srv.book[0]) {
        snprintf(url, sizeof url, "%s/v1/%s", g_srv.book, g_srv.keyhex);
        n = g_srv;
        long long t = fetch_note(url, &g_srv, &n);
        if (t >= 0 && t >= best_t) { best = n; best_t = t; logf_("address book: newest note"); }
    }
    if (best_t > 0) {
        snprintf(tbuf, sizeof tbuf, "%lld", best_t);
        ini_set(g_cache, "last_t", tbuf);
        if (best.url[0]) ini_set(g_cache, "url", best.url);
    }
    /* 3. try the addresses: newest note first, then the ones this file came with */
    for (int i = 0; i < best.naddrs; i++) if (try_addr(best.addrs[i])) return TRUE;
    for (int i = 0; i < g_srv.naddrs; i++) if (try_addr(g_srv.addrs[i])) return TRUE;
    logf_("server \"%s\" not found: is the host's server running?", g_srv.name);
    return FALSE;
}

static DWORD WINAPI resolver_thread(LPVOID p)
{
    (void)p;
    EnterCriticalSection(&g_lock);
    resolve();
    g_last_try = GetTickCount();
    LeaveCriticalSection(&g_lock);
    SetEvent(g_ready);
    return 0;
}

/* The game wants a Konami address: wait for the background lookup; retry if the host was offline. */
static const char *server_ip(void)
{
    if (g_offline || !g_srv.keyhex[0]) return NULL;
    WaitForSingleObject(g_ready, 20000);
    EnterCriticalSection(&g_lock);
    if (!g_ip[0] && GetTickCount() - g_last_try > 15000) {
        logf_("trying again to find the server");
        resolve();
        g_last_try = GetTickCount();
    }
    LeaveCriticalSection(&g_lock);
    return g_ip[0] ? g_ip : NULL;
}

/* ------------------------------------------------------------------- the Shift box (advanced) */
static HWND g_dlg, g_edit, g_status, g_ok, g_off;
static BOOL g_dlg_done;
static HFONT g_font;

static void base_url(const wchar_t *in, char *out, size_t cap)
{
    char buf[600], *s, *e;
    WideCharToMultiByte(CP_UTF8, 0, in, -1, buf, sizeof buf, NULL, NULL);
    s = strstr(buf, "http"); if (!s) s = buf;
    while (*s == ' ' || *s == '"' || *s == '\'') s++;
    for (e = s; *e && *e != ' ' && *e != '"' && *e != '\'' && *e != '|'; e++) ;
    *e = 0; trim(s);
    size_t n = strlen(s);
    if (n >= 5 && !_stricmp(s + n - 5, "/join")) s[n - 5] = 0;
    if (strstr(s, "://")) { snprintf(out, cap, "%s", s); return; }
    snprintf(out, cap, "http://%s", s);         /* IP or name: the server's /mod/note is plain HTTP */
}

static void dlg_try(void)
{
    wchar_t typed[512]; char base[600], url[700], body[2048];
    GetWindowTextW(g_edit, typed, 512);
    if (!typed[0]) { SetWindowTextW(g_status, L"Paste your host's server address."); return; }
    base_url(typed, base, sizeof base);
    SetWindowTextW(g_status, L"Connecting...");
    EnableWindow(g_ok, FALSE); EnableWindow(g_off, FALSE);
    HCURSOR old = SetCursor(LoadCursor(NULL, IDC_WAIT));
    UpdateWindow(g_dlg);
    /* Ask that address where its game server is, then make the server prove its identity. */
    server_t s; memset(&s, 0, sizeof s);
    snprintf(s.url, sizeof s.url, "%s", base);
    snprintf(s.book, sizeof s.book, "%s", g_srv.book);
    BOOL ok = FALSE;
    snprintf(url, sizeof url, "%s/mod/note", base);
    if (http_get(url, body, sizeof body, 8000)) {
        char *nl = strchr(body, '\n'), *ctx = NULL;
        char *b = nl ? nl + 1 : body;
        strtok_r(b, "\n", &ctx); strtok_r(NULL, "\n", &ctx); strtok_r(NULL, "\n", &ctx);
        for (char *a = strtok_r(NULL, "\n", &ctx); a; a = strtok_r(NULL, "\n", &ctx)) add_addr(&s, a);
    }
    if (!strncmp(base, "http://", 7)) {           /* typed an IP: it is a candidate itself */
        char h[64]; snprintf(h, sizeof h, "%s", base + 7); char *colon = strchr(h, ':'); if (colon) *colon = 0;
        add_addr(&s, h);
    }
    for (int i = 0; i < s.naddrs && !ok; i++) {
        if (hello(s.addrs[i], &s)) {
            server_t keep = s;
            g_srv = keep; snprintf(g_srv.name, sizeof g_srv.name, "%s", base);
            snprintf(g_cache, sizeof g_cache, "cache-%.16s", g_srv.keyhex);
            snprintf(g_ip, sizeof g_ip, "%s", s.addrs[i]);
            ini_set(g_cache, "last_ip", g_ip);
            ini_set("server", "key", g_srv.keyhex);
            ini_set("server", "url", base);
            InterlockedExchange(&g_id_on, g_srv.hello_v >= 2 && g_id_line[0]);
            ok = TRUE;
        }
    }
    SetCursor(old);
    EnableWindow(g_ok, TRUE); EnableWindow(g_off, TRUE);
    if (ok) {
        char embedded[80] = "none";
        if (g_embedded[EMBED_OFFSET]) {    /* remember which DLL this choice belongs to */
            server_t e; memset(&e, 0, sizeof e);
            const char *k = strstr((const char *)g_embedded + EMBED_OFFSET, "key=");
            if (k && sscanf(k + 4, "%64[0-9a-fA-F]", embedded) == 1 && parse_key(embedded, &e)) strcpy(embedded, e.keyhex);
        }
        ini_set("server", "chosen_with", embedded);
        g_offline = FALSE; g_dlg_done = TRUE;
        logf_("switched to %s (key %.16s...)", base, g_srv.keyhex);
        DestroyWindow(g_dlg);
    } else {
        wchar_t msg[700];
        _snwprintf(msg, 700, L"Couldn't reach a PES 2021 server at %hs. Check the address, or ask your host.", base);
        msg[699] = 0;
        SetWindowTextW(g_status, msg);
        SetFocus(g_edit);
    }
}

static LRESULT CALLBACK dlg_proc(HWND h, UINT m, WPARAM wp, LPARAM lp)
{
    switch (m) {
    case WM_COMMAND:
        if (LOWORD(wp) == IDOK) { dlg_try(); return 0; }
        if (LOWORD(wp) == IDCANCEL) { g_dlg_done = TRUE; DestroyWindow(h); return 0; }
        break;
    case WM_CLOSE:
        g_dlg_done = TRUE; DestroyWindow(h); return 0;
    case WM_CTLCOLORSTATIC:
        if ((HWND)lp == g_status) { SetTextColor((HDC)wp, RGB(170, 40, 30)); SetBkMode((HDC)wp, TRANSPARENT);
                                     return (LRESULT)GetSysColorBrush(COLOR_BTNFACE); }
        break;
    }
    return DefWindowProcW(h, m, wp, lp);
}

static HWND child(const wchar_t *cls, const wchar_t *text, DWORD style, int x, int y, int w, int h, int id)
{
    HWND c = CreateWindowExW(0, cls, text, WS_CHILD | WS_VISIBLE | style, x, y, w, h, g_dlg,
                             (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)g_font, TRUE);
    return c;
}

static void show_dialog(void)
{
    WNDCLASSW wc; NONCLIENTMETRICSW ncm; MSG msg; RECT wr = {0, 0, 520, 214};
    wchar_t cur[600] = L"";
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = dlg_proc; wc.hInstance = GetModuleHandleW(NULL); wc.lpszClassName = L"PES2021OnlineSetup";
    wc.hCursor = LoadCursor(NULL, IDC_ARROW); wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    RegisterClassW(&wc);
    ncm.cbSize = sizeof ncm;
    SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, sizeof ncm, &ncm, 0);
    g_font = CreateFontIndirectW(&ncm.lfMessageFont);
    DWORD style = WS_CAPTION | WS_SYSMENU | WS_VISIBLE;
    AdjustWindowRectEx(&wr, style, FALSE, WS_EX_TOPMOST | WS_EX_DLGMODALFRAME);
    int w = wr.right - wr.left, h = wr.bottom - wr.top;
    g_dlg = CreateWindowExW(WS_EX_TOPMOST | WS_EX_DLGMODALFRAME, wc.lpszClassName, L"PES 2021 Online", style,
                            (GetSystemMetrics(SM_CXSCREEN) - w) / 2, (GetSystemMetrics(SM_CYSCREEN) - h) / 2, w, h,
                            NULL, NULL, wc.hInstance, NULL);
    MultiByteToWideChar(CP_UTF8, 0, g_srv.url, -1, cur, 600);
    child(L"STATIC", L"Play on another server: paste its address.", 0, 16, 14, 488, 20, -1);
    g_edit = child(L"EDIT", cur, WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, 16, 38, 488, 26, 101);
    child(L"STATIC", L"For example 203.0.113.7 (your host's internet address) or its name. "
                     L"Close this box to keep your current server.", 0, 16, 72, 488, 36, -1);
    g_status = child(L"STATIC", L"", 0, 16, 112, 488, 40, 102);
    g_ok = child(L"BUTTON", L"Switch server", WS_TABSTOP | BS_DEFPUSHBUTTON, 252, 164, 132, 32, IDOK);
    g_off = child(L"BUTTON", L"Keep current", WS_TABSTOP, 392, 164, 112, 32, IDCANCEL);
    SetForegroundWindow(g_dlg);
    SetFocus(g_edit);
    SendMessageW(g_edit, EM_SETSEL, 0, -1);
    g_dlg_done = FALSE;
    while (!g_dlg_done && GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(g_dlg, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    DeleteObject(g_font);
}

/* Runs once, on the game's thread, just before its first window appears. Never blocks the game
 * unless the player held Shift (or has no server at all): the lookup runs in the background. */
static void startup(void)
{
    if (g_force_prompt || !g_srv.keyhex[0]) {
        show_dialog();
        if (g_ip[0]) { SetEvent(g_ready); return; }
    }
    HANDLE t = CreateThread(NULL, 0, resolver_thread, NULL, 0, NULL);
    if (t) CloseHandle(t); else SetEvent(g_ready);
}

/* ------------------------------------------------------------------------------------- hooks */
static int is_konami(const char *n)
{
    if (!n) return 0;
    for (int i = 0; KONAMI_NAMES[i]; i++) if (!_stricmp(n, KONAMI_NAMES[i])) return 1;
    return 0;
}
static int is_konami_w(const wchar_t *n)
{
    char a[256];
    if (!n || !WideCharToMultiByte(CP_UTF8, 0, n, -1, a, sizeof a, NULL, NULL)) return 0;
    return is_konami(a);
}
static int is_ntl_w(const wchar_t *n)
{
    return n && !_wcsnicmp(n, L"ntl", 3) && is_konami_w(n);
}

typedef INT (WSAAPI *getaddrinfo_t)(PCSTR, PCSTR, const ADDRINFOA *, PADDRINFOA *);
typedef INT (WSAAPI *GetAddrInfoW_t)(PCWSTR, PCWSTR, const ADDRINFOW *, PADDRINFOW *);
typedef INT (WSAAPI *GetAddrInfoExW_t)(PCWSTR, PCWSTR, DWORD, LPGUID, const ADDRINFOEXW *, PADDRINFOEXW *,
                                       struct timeval *, LPOVERLAPPED, LPLOOKUPSERVICE_COMPLETION_ROUTINE, LPHANDLE);
typedef struct hostent *(WSAAPI *gethostbyname_t)(const char *);
typedef HINTERNET (WINAPI *WinHttpConnect_t)(HINTERNET, LPCWSTR, INTERNET_PORT, DWORD);
typedef HINTERNET (WINAPI *WinHttpOpenRequest_t)(HINTERNET, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR *, DWORD);
typedef BOOL (WINAPI *WinHttpSendRequest_t)(HINTERNET, LPCWSTR, DWORD, LPVOID, DWORD, DWORD, DWORD_PTR);
typedef BOOL (WINAPI *WinHttpCloseHandle_t)(HINTERNET);
typedef HWND (WINAPI *CreateWindowExW_t)(DWORD, LPCWSTR, LPCWSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);
typedef HWND (WINAPI *CreateWindowExA_t)(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);

static getaddrinfo_t o_getaddrinfo;
static GetAddrInfoW_t o_GetAddrInfoW;
static GetAddrInfoExW_t o_GetAddrInfoExW;
static gethostbyname_t o_gethostbyname;
static WinHttpConnect_t o_WinHttpConnect;
static WinHttpOpenRequest_t o_WinHttpOpenRequest;
static WinHttpSendRequest_t o_WinHttpSendRequest;
static WinHttpCloseHandle_t o_WinHttpCloseHandle;
static CreateWindowExW_t o_CreateWindowExW;
static CreateWindowExA_t o_CreateWindowExA;

typedef int (WSAAPI *send_t)(SOCKET, const char *, int, int);
typedef int (WSAAPI *WSASend_t)(SOCKET, LPWSABUF, DWORD, LPDWORD, DWORD, LPWSAOVERLAPPED, LPWSAOVERLAPPED_COMPLETION_ROUTINE);
typedef int (WSAAPI *closesocket_t)(SOCKET);
static send_t o_send;
static WSASend_t o_WSASend;
static closesocket_t o_closesocket;

static INT WSAAPI hk_getaddrinfo(PCSTR node, PCSTR svc, const ADDRINFOA *hints, PADDRINFOA *res)
{
    const char *ip;
    if (is_konami(node) && (ip = server_ip())) { logf_("lookup %s -> %s", node, ip); node = ip; }
    return o_getaddrinfo(node, svc, hints, res);
}
static INT WSAAPI hk_GetAddrInfoW(PCWSTR node, PCWSTR svc, const ADDRINFOW *hints, PADDRINFOW *res)
{
    const char *ip; wchar_t w[64];
    if (is_konami_w(node) && (ip = server_ip())) {
        MultiByteToWideChar(CP_UTF8, 0, ip, -1, w, 64);
        logf_("lookup %ls -> %s", node, ip);
        node = w;
    }
    return o_GetAddrInfoW(node, svc, hints, res);
}
static INT WSAAPI hk_GetAddrInfoExW(PCWSTR name, PCWSTR svc, DWORD ns, LPGUID id, const ADDRINFOEXW *hints,
                                    PADDRINFOEXW *res, struct timeval *to, LPOVERLAPPED ov,
                                    LPLOOKUPSERVICE_COMPLETION_ROUTINE cb, LPHANDLE nh)
{
    const char *ip; wchar_t w[64];
    if (is_konami_w(name) && (ip = server_ip())) {
        MultiByteToWideChar(CP_UTF8, 0, ip, -1, w, 64);
        logf_("lookup %ls -> %s", name, ip);
        name = w;
    }
    return o_GetAddrInfoExW(name, svc, ns, id, hints, res, to, ov, cb, nh);
}
static struct hostent *WSAAPI hk_gethostbyname(const char *name)
{
    const char *ip;
    if (is_konami(name) && (ip = server_ip())) { logf_("lookup %s -> %s", name, ip); name = ip; }
    return o_gethostbyname(name);
}

/* WinHTTP handles that belong to the network test (ntl*.service.konami.net) */
#define MAXH 64
static HINTERNET g_ntl[MAXH];
static void ntl_add(HINTERNET h) { for (int i = 0; i < MAXH; i++) if (!g_ntl[i]) { g_ntl[i] = h; return; } }
static int ntl_has(HINTERNET h) { if (!h) return 0; for (int i = 0; i < MAXH; i++) if (g_ntl[i] == h) return 1; return 0; }
static void ntl_del(HINTERNET h) { for (int i = 0; i < MAXH; i++) if (g_ntl[i] == h) g_ntl[i] = NULL; }

static HINTERNET WINAPI hk_WinHttpConnect(HINTERNET s, LPCWSTR name, INTERNET_PORT port, DWORD r)
{
    /* The network test is HTTPS on 443. The server also speaks HTTPS on 8080 (it tells the two apart by
       the first byte), and 8080 needs no administrator rights on the server computer. */
    int ntl = is_ntl_w(name) && server_ip();
    HINTERNET c = o_WinHttpConnect(s, name, ntl && port == 443 ? 8080 : port, r);
    if (c && ntl) {
        EnterCriticalSection(&g_lock); ntl_add(c); LeaveCriticalSection(&g_lock);
        logf_("network test connection to %ls:%u (server port %u)", name, port, port == 443 ? 8080 : port);
    }
    return c;
}
static HINTERNET WINAPI hk_WinHttpOpenRequest(HINTERNET c, LPCWSTR verb, LPCWSTR obj, LPCWSTR ver, LPCWSTR ref,
                                              LPCWSTR *types, DWORD flags)
{
    HINTERNET r = o_WinHttpOpenRequest(c, verb, obj, ver, ref, types, flags);
    if (r) { EnterCriticalSection(&g_lock); if (ntl_has(c)) ntl_add(r); LeaveCriticalSection(&g_lock); }
    return r;
}
static BOOL WINAPI hk_WinHttpSendRequest(HINTERNET r, LPCWSTR hdr, DWORD hl, LPVOID opt, DWORD ol, DWORD tl, DWORD_PTR ctx)
{
    int mine;
    EnterCriticalSection(&g_lock); mine = ntl_has(r); LeaveCriticalSection(&g_lock);
    if (mine) {
        /* The server has its own certificate, not Konami's: accept it, for these four names only. */
        DWORD f = SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                  SECURITY_FLAG_IGNORE_CERT_DATE_INVALID | SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
        BOOL ok = WinHttpSetOption(r, WINHTTP_OPTION_SECURITY_FLAGS, &f, sizeof f);
        logf_("network test request: accepting the server's certificate (%s)", ok ? "ok" : "failed");
    }
    return o_WinHttpSendRequest(r, hdr, hl, opt, ol, tl, ctx);
}
static BOOL WINAPI hk_WinHttpCloseHandle(HINTERNET h)
{
    EnterCriticalSection(&g_lock); ntl_del(h); LeaveCriticalSection(&g_lock);
    return o_WinHttpCloseHandle(h);
}

/* Player id line: before the first bytes the game sends on a TCP connection to port 10000 (the game
 * server), send g_id_line once. Sockets already handled are remembered until closesocket. */
#define GATE_PORT 10000
#define MAXS 64
static SOCKET g_sent[MAXS];
static int g_nsent;
static CRITICAL_SECTION g_slock;      /* own lock: g_lock can be held for seconds during a lookup */

static int sent_find(SOCKET s) { for (int i = 0; i < g_nsent; i++) if (g_sent[i] == s) return i; return -1; }
static void sent_drop(int i) { g_sent[i] = g_sent[--g_nsent]; }

static int is_gate_socket(SOCKET s)
{
    struct sockaddr_storage a; int al = sizeof a, type = 0, tl = sizeof type;
    if (getpeername(s, (struct sockaddr *)&a, &al)) return 0;
    if (a.ss_family == AF_INET) { if (ntohs(((struct sockaddr_in *)&a)->sin_port) != GATE_PORT) return 0; }
    else if (a.ss_family == AF_INET6) { if (ntohs(((struct sockaddr_in6 *)&a)->sin6_port) != GATE_PORT) return 0; }
    else return 0;
    return !getsockopt(s, SOL_SOCKET, SO_TYPE, (char *)&type, &tl) && type == SOCK_STREAM;
}

static void before_send(SOCKET s)
{
    if (!g_id_on) return;
    EnterCriticalSection(&g_slock);
    if (sent_find(s) < 0 && is_gate_socket(s)) {
        /* Remember the socket first: Wine's send() calls WSASend() (or the other way round), and the
           hook on that one must not send the line a second time. */
        if (g_nsent == MAXS) sent_drop(0);          /* table full: forget the oldest */
        g_sent[g_nsent++] = s;
        int n = (int)strlen(g_id_line), off = 0, tries = 0;
        while (off < n && tries++ < 200) {          /* a fresh socket takes 40 bytes at once */
            int k = o_send(s, g_id_line + off, n - off, 0);
            if (k > 0) { off += k; continue; }
            if (WSAGetLastError() != WSAEWOULDBLOCK) break;
            Sleep(5);
        }
        if (off == n) logf_("player id sent on a new game-server connection");
        else if (off) logf_("ERROR: player id only partly sent (%d of %d bytes)", off, n);  /* the server drops it */
        else { int i = sent_find(s); if (i >= 0) sent_drop(i); }
        /* off == 0: not connected yet; the game's own send fails the same way and we try again next time */
    }
    LeaveCriticalSection(&g_slock);
}

static int WSAAPI hk_send(SOCKET s, const char *buf, int len, int flags)
{
    before_send(s);
    return o_send(s, buf, len, flags);
}
static int WSAAPI hk_WSASend(SOCKET s, LPWSABUF b, DWORD n, LPDWORD sent, DWORD flags, LPWSAOVERLAPPED ov,
                             LPWSAOVERLAPPED_COMPLETION_ROUTINE cr)
{
    before_send(s);
    return o_WSASend(s, b, n, sent, flags, ov, cr);
}
static int WSAAPI hk_closesocket(SOCKET s)
{
    EnterCriticalSection(&g_slock);
    int i = sent_find(s);
    if (i >= 0) sent_drop(i);
    LeaveCriticalSection(&g_slock);
    return o_closesocket(s);
}

static void maybe_start(HWND parent, DWORD style)
{
    if (!parent && !(style & WS_CHILD) && !InterlockedCompareExchange(&g_started, 1, 0)) startup();
}
static HWND WINAPI hk_CreateWindowExW(DWORD ex, LPCWSTR cls, LPCWSTR name, DWORD style, int x, int y, int w, int h,
                                      HWND parent, HMENU menu, HINSTANCE inst, LPVOID param)
{
    maybe_start(parent, style);
    return o_CreateWindowExW(ex, cls, name, style, x, y, w, h, parent, menu, inst, param);
}
static HWND WINAPI hk_CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR name, DWORD style, int x, int y, int w, int h,
                                      HWND parent, HMENU menu, HINSTANCE inst, LPVOID param)
{
    maybe_start(parent, style);
    return o_CreateWindowExA(ex, cls, name, style, x, y, w, h, parent, menu, inst, param);
}

static void hook(const wchar_t *dll, const char *fn, void *detour, void **orig)
{
    if (!GetModuleHandleW(dll) && !LoadLibraryW(dll)) { logf_("ERROR: %ls not loaded", dll); return; }
    MH_STATUS st = MH_CreateHookApi(dll, fn, detour, orig);
    if (st != MH_OK) logf_("ERROR: hook %ls!%s: %s", dll, fn, MH_StatusToString(st));
}

static void install_hooks(void)
{
    MH_STATUS st = MH_Initialize();
    if (st != MH_OK) { logf_("ERROR: MinHook: %s", MH_StatusToString(st)); return; }
    hook(L"ws2_32.dll", "getaddrinfo", hk_getaddrinfo, (void **)&o_getaddrinfo);
    hook(L"ws2_32.dll", "GetAddrInfoW", hk_GetAddrInfoW, (void **)&o_GetAddrInfoW);
    hook(L"ws2_32.dll", "GetAddrInfoExW", hk_GetAddrInfoExW, (void **)&o_GetAddrInfoExW);
    hook(L"ws2_32.dll", "gethostbyname", hk_gethostbyname, (void **)&o_gethostbyname);
    if (g_id_line[0]) {
        hook(L"ws2_32.dll", "send", hk_send, (void **)&o_send);
        hook(L"ws2_32.dll", "WSASend", hk_WSASend, (void **)&o_WSASend);
        hook(L"ws2_32.dll", "closesocket", hk_closesocket, (void **)&o_closesocket);
    }
    hook(L"winhttp.dll", "WinHttpConnect", hk_WinHttpConnect, (void **)&o_WinHttpConnect);
    hook(L"winhttp.dll", "WinHttpOpenRequest", hk_WinHttpOpenRequest, (void **)&o_WinHttpOpenRequest);
    hook(L"winhttp.dll", "WinHttpSendRequest", hk_WinHttpSendRequest, (void **)&o_WinHttpSendRequest);
    hook(L"winhttp.dll", "WinHttpCloseHandle", hk_WinHttpCloseHandle, (void **)&o_WinHttpCloseHandle);
    hook(L"user32.dll", "CreateWindowExW", hk_CreateWindowExW, (void **)&o_CreateWindowExW);
    hook(L"user32.dll", "CreateWindowExA", hk_CreateWindowExA, (void **)&o_CreateWindowExA);
    st = MH_EnableHook(MH_ALL_HOOKS);
    if (st == MH_OK) logf_("hooks ready");
    else logf_("ERROR: enabling hooks: %s", MH_StatusToString(st));
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(h);
    InitializeCriticalSection(&g_lock);
    InitializeCriticalSection(&g_slock);
    g_ready = CreateEventW(NULL, TRUE, FALSE, NULL);
    wchar_t dir[MAX_PATH], logp[MAX_PATH];
    GetModuleFileNameW(h, dir, MAX_PATH);
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) slash[1] = 0;
    _snwprintf(g_ini, MAX_PATH, L"%lspes2021-online.ini", dir); g_ini[MAX_PATH - 1] = 0;
    _snwprintf(logp, MAX_PATH, L"%lspes2021-online.log", dir); logp[MAX_PATH - 1] = 0;
    g_log = _wfopen(logp, L"w");
    if (!g_log) {
        GetTempPathW(MAX_PATH - 24, logp); wcscat(logp, L"pes2021-online.log");
        g_log = _wfopen(logp, L"w");
    }
    load_settings();
    make_player_id();
    g_force_prompt = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
    logf_("PES 2021 Online mod " MOD_VERSION "; server \"%s\" key %.16s...%s", g_srv.name,
          g_srv.keyhex[0] ? g_srv.keyhex : "(none)", g_force_prompt ? " (Shift held)" : "");
    install_hooks();
    return TRUE;
}
