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
 *   - Same math on every PC: in a match both PCs simulate the game and must compute identical numbers.
 *     The game takes sinf, powf, atan2f... and qsort from the C runtime, which is Microsoft's on Windows
 *     and Wine's own under Proton / Steam Deck; their results differ in the last bits, so a Windows
 *     player and a Linux player fell out of sync at kick-off ("SYNC_FAILED", RMYN393). The mod hooks
 *     those functions in ucrtbase.dll and answers with its own copies (musl-math/), the same code on
 *     every PC. Only with servers that keep such players apart from older mods (/mod/hello version 3;
 *     the id line then says "PESID2 <id> math1"); otherwise the runtime's own functions are used.
 *   - Online Co-op: the game forgets to give its P2P layer the opponent it was matched with, so co-op never
 *     connects. Two hooks on the game's co-op code add the step Quick Match has (see "Online Co-op" below);
 *     the id line then also says "coop1". Only on the exact 1.01.00 code.
 *   - Cup finals end with Konami's trophy ceremony (mod 0.7.0): the server arms it through /mod/final and the
 *     id line says "trophy1" (see "trophy ceremony" below). Only on the exact 1.01.00 code.
 *   - A cup shows Konami's own bracket screen ("Tournament Table") after each cup match and in place of
 *     Rankings (mod 0.8.0, "bracket1", the server's /mod/bracket; see "the cup bracket" below).
 *   - Invites: a copy the host made for one friend carries an invite code ("invite=" in the settings area);
 *     the id line then ends with " inv=<code>" so the server knows that friend on any PC. Sent only to the
 *     server whose key made the file, and only if its /mod/hello answers version 4.
 * The game's own files are not modified; in memory only the co-op functions above are hooked. dinput8 itself is forwarded to the system DLL.
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

#define MOD_VERSION "0.8.0"

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
    int hello_v;                  /* the server's /mod/hello version: 2 = reads the player-id line,
                                     3 = also pairs players by math (PESID2 line) */
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
static CRITICAL_SECTION g_slock;  /* own lock for the id line and sockets: g_lock can be held for seconds */
static FILE *g_log;
static volatile LONG g_id_on;     /* the server in use reads the player-id line */
static volatile LONG g_math_on;   /* the game gets our math functions (server pairs by them) */
static volatile LONG g_coop_fix;  /* the co-op hooks are in (id line says coop1) */
static volatile LONG g_trophy_on; /* the trophy-ceremony hooks are in (id line says trophy1) */
static volatile LONG g_bracket_on; /* the cup-bracket hooks are in (id line says bracket1) */
static char g_id_hex[33];         /* this install's player id ("" = none) */
static char g_id_line[128];       /* the line for the server in use */
static char g_invite[33];         /* this copy's invite code from the host ("" = none) */
static char g_invite_key[65];     /* the key of the server that made it: the code goes to that server only */

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
        else if (!strcmp(line, "invite")) {
            size_t n = strlen(v), ok = n == 32;
            for (size_t i = 0; ok && i < n; i++) ok = (v[i] >= '0' && v[i] <= '9') || (v[i] >= 'a' && v[i] <= 'f');
            if (ok) strcpy(g_invite, v);
        }
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
    if (g_invite[0] && embedded_key[0]) strcpy(g_invite_key, embedded_key);
    else g_invite[0] = 0;
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
    for (int i = 0; i < 16; i++) sprintf(g_id_hex + 2 * i, "%02x", h[i]);
    logf_("player id %.8s... (%s secret%s)", g_id_hex, from, mid[0] ? "" : ", no machine id");
}

/* What this mod tells the server it uses, from the server's /mod/hello version. */
static void use_server(int hello_v)
{
    LONG math = hello_v >= 3 || GetEnvironmentVariableA("PES2021ONLINE_TEST_MATH", NULL, 0) > 0;  /* tests only */
    EnterCriticalSection(&g_slock);
    /* the invite code only to the server that made this file, and only if it reads it (version 4) */
    BOOL inv = hello_v >= 4 && g_invite[0] && !strcmp(g_srv.keyhex, g_invite_key);
    if (hello_v >= 3) snprintf(g_id_line, sizeof g_id_line, "PESID2 %s math1%s%s%s%s%s\n", g_id_hex,
                               g_coop_fix ? " coop1" : "", g_trophy_on ? " trophy1" : "", g_bracket_on ? " bracket1" : "",
                               inv ? " inv=" : "", inv ? g_invite : "");
    else snprintf(g_id_line, sizeof g_id_line, "PESID1 %s\n", g_id_hex);
    LeaveCriticalSection(&g_slock);
    InterlockedExchange(&g_id_on, hello_v >= 2 && g_id_hex[0]);
    if (InterlockedExchange(&g_math_on, math) != math)
        logf_("same-math functions %s", math ? "on (sinf, powf, atan2f, qsort...)" : "off");
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
    snprintf(url, sizeof url, "http://%s:8080/mod/hello?n=%s&v=4", ip, nonce);
    if (!http_get(url, body, sizeof body, 2500)) return FALSE;
    char *nl = strchr(body, '\n');
    if (!nl) return FALSE;
    char claimed[65] = "";
    int v = 0;                                /* older servers answer a lower version */
    if (sscanf(nl + 1, "PES2021ONLINE-HELLO %d\n%64[0-9a-f]\n", &v, claimed) != 2 || v < 1 || v > 4) return FALSE;
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
    use_server(g_srv.hello_v);
    logf_("server \"%s\" is at %s (verified%s)", g_srv.name, ip, g_srv.hello_v >= 3 ? ", sends player id, same math"
          : g_id_on ? ", sends player id; older server: game's own math" : ", older server: no player id");
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
            use_server(g_srv.hello_v);
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

/* Same math on every PC (see the top of the file). Each hook answers with our function while
 * g_math_on is set, else with the C runtime's own (the trampoline). */
#define MATH_D1(X) X(acos) X(asin) X(atan) X(cos) X(cosh) X(exp) X(log) X(log10) X(sin) X(sinh) X(tan) X(tanh)
#define MATH_F1(X) X(acosf) X(asinf) X(atanf) X(cbrtf) X(cosf) X(expf) X(log10f) X(logf) X(sinf) X(tanf)
#define MATH_D2(X) X(atan2) X(pow)
#define MATH_F2(X) X(atan2f) X(powf)
#define DEF_D1(n) double pesm_##n(double); static double (*o_##n)(double); \
    static double hk_##n(double x) { return g_math_on ? pesm_##n(x) : o_##n(x); }
#define DEF_F1(n) float pesm_##n(float); static float (*o_##n)(float); \
    static float hk_##n(float x) { return g_math_on ? pesm_##n(x) : o_##n(x); }
#define DEF_D2(n) double pesm_##n(double, double); static double (*o_##n)(double, double); \
    static double hk_##n(double x, double y) { return g_math_on ? pesm_##n(x, y) : o_##n(x, y); }
#define DEF_F2(n) float pesm_##n(float, float); static float (*o_##n)(float, float); \
    static float hk_##n(float x, float y) { return g_math_on ? pesm_##n(x, y) : o_##n(x, y); }
MATH_D1(DEF_D1) MATH_F1(DEF_F1) MATH_D2(DEF_D2) MATH_F2(DEF_F2)

typedef int (__cdecl *qsort_cmp)(const void *, const void *);
void pesm_qsort(void *, size_t, size_t, qsort_cmp);
static void (__cdecl *o_qsort)(void *, size_t, size_t, qsort_cmp);
static void __cdecl hk_qsort(void *b, size_t n, size_t w, qsort_cmp c)
{
    if (g_math_on) pesm_qsort(b, n, w, c); else o_qsort(b, n, w, c);
}

static void hook(const wchar_t *dll, const char *fn, void *detour, void **orig);
static void hook_math(void)
{
    /* The game imports these from api-ms-win-crt-math / -utility, which lead to ucrtbase.dll on
       Windows and under Wine alike. Hooking them there also covers lookups by GetProcAddress. */
#define HOOK_IT(n) hook(L"ucrtbase.dll", #n, (void *)hk_##n, (void **)&o_##n);
    MATH_D1(HOOK_IT) MATH_F1(HOOK_IT) MATH_D2(HOOK_IT) MATH_F2(HOOK_IT) HOOK_IT(qsort)
#undef HOOK_IT
}

/* ------------------------------------------------------------------------ Online Co-op (1 vs 1)
 * In 1.01.00 Online Co-op never connects. After matching, the game builds the list of the players it must
 * reach (TaskCoopSession, 0x141134c00) but, unlike Quick Match and the Friendly Match Lobby, never hands that
 * list to its P2P layer (0x141092720), so the P2P layer looks for "pid 0" and gives up (MXNV_0013). The game's
 * WATCH_COOP_MATCHING handler (0x14110fe00) also drops the peers' addresses that the reply parser decoded.
 * Two hooks put back exactly what Quick Match does; nothing else in the game changes:
 *   - after the co-op peer list is built, hand it to the P2P layer;
 *   - after the matching push is handled, copy each member's host/reflexive address and NAT type into the
 *     member record the peer list is built from.
 * The P2P layer takes one peer, so this carries 1 vs 1 co-op (one human per side). The hooks go in only when
 * the code at every address is the 1.01.00 code; the server then pairs co-op players whose id line says
 * "coop1" (notes/re/functions/coop2.md). */
#define PES_BASE 0x140000000ULL
static uint8_t *g_exe;                                      /* the game's image base */
static void *game_va(uint64_t va) { return g_exe + (va - PES_BASE); }
static uintptr_t (*o_coop_peers)(void *, void *, void *, void *);   /* 0x141134c00 (TaskCoopSession *) */
static uintptr_t (*o_coop_watch)(void *, void *, void *, void *);   /* 0x14110fe00 (watch command *) */
static void (*game_hand_peers)(void *);                      /* 0x141092720 (network-mode struct *) */

/* g = [0x1436fced8]: online mode globals. g+0x30 = co-op state (member slots at +0x58 / +0x90, each with a
   vector of 0x68-byte member records at +0x18), g+0x58 = network-mode struct (peer vector at +0x60). */
static uint8_t *coop_globals(void) { return *(uint8_t **)game_va(0x1436fced8); }

static uintptr_t hk_coop_peers(void *a, void *b, void *c, void *d)
{
    uintptr_t r = o_coop_peers(a, b, c, d);
    uint8_t *g = coop_globals();
    uint8_t *net = g ? *(uint8_t **)(g + 0x58) : NULL;
    if (!net) return r;
    uint8_t *p0 = *(uint8_t **)(net + 0x60), *p1 = *(uint8_t **)(net + 0x68);
    size_t n = p0 && p1 > p0 ? (size_t)(p1 - p0) / 0x30 : 0;
    game_hand_peers(net);
    logf_("co-op: %u peer(s) handed to the P2P layer%s (first pid %d)", (unsigned)n,
          n == 1 ? "" : " (it takes exactly one; 1 vs 1 only)", n ? *(int32_t *)p0 : 0);
    return r;
}

/* Address fields: parsed member (0x3e8 bytes) host family +0xd8 / sa_data +0xdc, reflexive +0xf0 / +0xf4,
   NAT type +0x120; member record (0x68 bytes) +0x3c / +0x40, +0x50 / +0x54, +0x64. sa_data is 14 bytes. */
typedef struct { int32_t pid; uint8_t host[4 + 14], refl[4 + 14]; uint32_t nat; } coop_addr;

static uintptr_t hk_coop_watch(void *cmd, void *b, void *c, void *d)
{
    coop_addr got[24]; int n = 0;
    uint8_t *res = cmd ? *(uint8_t **)((uint8_t *)cmd + 0xd0) : NULL;   /* the parsed reply (0x141173e20) */
    if (res) {
        uint8_t *groups = *(uint8_t **)(res + 0x1d8);
        uint32_t ng = *(uint32_t *)(res + 0x1e0);
        for (uint32_t i = 0; groups && i < ng && i < 8; i++) {
            uint8_t *mem = *(uint8_t **)(groups + i * 0x20 + 8);
            uint32_t nm = *(uint32_t *)(groups + i * 0x20 + 0x10);
            for (uint32_t j = 0; mem && j < nm && j < 3 && n < 24; j++, n++) {
                uint8_t *m = mem + j * 0x3e8;
                got[n].pid = *(int32_t *)(m + 0x28);
                memcpy(got[n].host, m + 0xd8, 4); memcpy(got[n].host + 4, m + 0xdc, 14);
                memcpy(got[n].refl, m + 0xf0, 4); memcpy(got[n].refl + 4, m + 0xf4, 14);
                got[n].nat = *(uint32_t *)(m + 0x120);
            }
        }
    }
    uintptr_t r = o_coop_watch(cmd, b, c, d);                /* builds the member records */
    uint8_t *g = coop_globals(), *co = g ? *(uint8_t **)(g + 0x30) : NULL;
    int copied = 0;
    for (int s = 0; co && s < 2; s++) {
        uint8_t *slot = co + (s ? 0x90 : 0x58);
        uint8_t *rec = *(uint8_t **)(slot + 0x18), *end = *(uint8_t **)(slot + 0x20);
        for (; rec && rec < end; rec += 0x68)
            for (int k = 0; k < n; k++)
                if (got[k].pid && got[k].pid == *(int32_t *)(rec + 0x28)) {
                    memcpy(rec + 0x3c, got[k].host, 4); memcpy(rec + 0x40, got[k].host + 4, 14);
                    memcpy(rec + 0x50, got[k].refl, 4); memcpy(rec + 0x54, got[k].refl + 4, 14);
                    memcpy(rec + 0x64, &got[k].nat, 4);
                    copied++;
                    break;
                }
    }
    if (n) logf_("co-op: addresses of %d of %d member(s) kept", copied, n);
    return r;
}

/* The exact 1.01.00 bytes at each address: a different build gets no hooks (and no "coop1"). */
static const struct { uint64_t va; const char *hex; } coop_code[] = {
    { 0x14110fe00, "40554154415541564157488dac24b0ed" },  /* WATCH_COOP_MATCHING handler */
    { 0x141134c00, "488bc4554154415541564157488d68b8" },  /* co-op peer list builder */
    { 0x1411346c3, "e838050000b001" },                    /* its only caller: call 0x141134c00; mov al,1 */
    { 0x141092720, "48895c240848896c2410488974241857" },  /* hand the peer list to the P2P layer */
    { 0x14111a8fe, "498bcee81a7ef7ff" },                  /* Quick Match does: mov rcx,r14; call 0x141092720 */
    { 0x140ff0a20, "488b05b1c47002c3" },                  /* g getter: mov rax,[0x1436fced8]; ret */
};

static BOOL code_is(uint64_t va, const char *hex)
{
    const uint8_t *p = game_va(va);
    for (size_t i = 0; hex[2 * i]; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1 || p[i] != v) return FALSE;
    }
    return TRUE;
}

static void hook_coop(void)
{
    g_exe = (uint8_t *)GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)(g_exe + ((const IMAGE_DOS_HEADER *)g_exe)->e_lfanew);
    if (PES_BASE + nt->OptionalHeader.SizeOfImage <= 0x1436fced8 + 8) { logf_("co-op fix: not PES 2021 1.01.00, off"); return; }
    for (size_t i = 0; i < sizeof coop_code / sizeof coop_code[0]; i++)
        if (!code_is(coop_code[i].va, coop_code[i].hex)) {
            logf_("co-op fix: game code at %llx is not 1.01.00, off", (unsigned long long)coop_code[i].va);
            return;
        }
    game_hand_peers = (void (*)(void *))game_va(0x141092720);
    if (MH_CreateHook(game_va(0x141134c00), (void *)hk_coop_peers, (void **)&o_coop_peers) != MH_OK ||
        MH_CreateHook(game_va(0x14110fe00), (void *)hk_coop_watch, (void **)&o_coop_watch) != MH_OK) {
        logf_("ERROR: co-op fix hooks"); return;
    }
    InterlockedExchange(&g_coop_fix, 1);
    logf_("co-op fix ready (1 vs 1)");
}

/* ------------------------------------------------------------------ trophy ceremony ("trophy1")
 * An offline cup final ends with Konami's trophy ceremony (an END demo group of
 * common/demo/fixdemo/end/table_end.bin: celebration, trophy lift, team photo). Online the game skips every
 * end demo: the full-time step of the match sequencer (0x140447290) asks match::Online whether the match is
 * online (vfunc +0x88, true) and then plays none (notes/re/functions/tourney.md, "Trophy ceremony").
 * For a cup final the server arms the ceremony: at half time the mod asks /mod/final which END group to play
 * (0 = none). At full time (match phase 8..10, also after a shootout) the full-time step then takes the
 * offline branch, and the END group picker (0x1404879b0) returns that group (its _PK variant after a
 * shootout) with the winner's side from the match score. Both players' games do the same from the same
 * score, so they show the same ceremony; the server arms a final only when both mods say "trophy1".
 * Half time and every other match stay as they were. */
static uintptr_t (*o_end_step)(void *, uintptr_t, void *, void *);   /* 0x140447290 full-time step */
static uint32_t (*o_end_pick)(void *, void *, void *, uintptr_t);   /* 0x1404879b0 END group picker */
static volatile LONG g_final_group;           /* the END group the server armed for this match (0 = none) */
static volatile LONGLONG g_final_asked;       /* GetTickCount64 of the last question */

/* [0x1436f3dc8] = match::registry; its Ref holders keep a raw pointer at +0x18 while the match job runs:
   +0x270 MatchInfo (phase at +0x1168: 4 half time, 8..10 the end), +0x310 the score (side 0 / 1 goals at
   +0x18 / +0x1c, shootout goals included: the shootout end compares them the same way). */
static uint8_t *match_ref(size_t off)
{
    uint8_t *reg = *(uint8_t **)game_va(0x1436f3dc8);
    return reg ? *(uint8_t **)(reg + off) : NULL;
}
static int match_phase(void) { uint8_t *mi = match_ref(0x270); return mi ? *(int32_t *)(mi + 0x1168) : 0; }

/* The game's own question at the full-time step: [0x1434a2d40] -> vfunc +0x88 (match::Online: true). An offline
   match keeps its own end demos whatever the server said. */
static BOOL match_online(void)
{
    void **obj = *(void ***)game_va(0x1434a2d40);
    if (!obj || !*obj) return FALSE;
    char (*is_online)(void *) = (char (*)(void *))((void **)*obj)[0x88 / 8];
    return is_online(obj) != 0;
}

/* The winner's side (0 / 1) once the match is decided, else -1: phase 8..10 (the end of normal time, of extra
   time, of a shootout) with goals that are not level. A level end of extra time goes on to penalties; the
   offline end branch there (or at half time) crashes an online game (live 2026-11-21), so it stays closed. */
static int match_winner(void)
{
    int ph = match_phase();
    uint8_t *score = match_ref(0x310);
    if (ph < 8 || ph > 10 || !score) return -1;
    int32_t g0 = *(int32_t *)(score + 0x18), g1 = *(int32_t *)(score + 0x1c);
    return g0 == g1 ? -1 : g0 > g1 ? 0 : 1;
}

/* The full-time step's "online: no end demo" branch (je at 0x1404473ef): 0x74 = the game's own; 0xeb = go on
   to the END group picker, as offline. */
static void end_branch(uint8_t op)
{
    uint8_t *p = game_va(0x1404473ef);
    DWORD old;
    if (*p == op || !VirtualProtect(p, 1, PAGE_EXECUTE_READWRITE, &old)) return;
    *p = op;
    VirtualProtect(p, 1, old, &old);
    FlushInstructionCache(GetCurrentProcess(), p, 1);
}

static DWORD WINAPI final_thread(LPVOID over)
{
    char ip[64], url[256], body[256];
    snprintf(ip, sizeof ip, "%s", g_ip);        /* (a copy: the address changes only when the server moves) */
    long grp = 0;
    if (ip[0] && g_id_hex[0]) {
        snprintf(url, sizeof url, "http://%s:8080/mod/final?id=%s%s", ip, g_id_hex, over ? "&over=1" : "");
        if (http_get(url, body, sizeof body, 4000)) {
            const char *g = strstr(body, "group=");
            if (g) grp = strtol(g + 6, NULL, 0);
        }
    }
    if (over) return 0;                          /* (the server has been told; the ceremony is under way) */
    if (grp < 0x70001 || grp > 0x70028) grp = 0;
    InterlockedExchange(&g_final_group, grp);
    if (grp) logf_("cup final: the trophy ceremony is ready (END group %lx)", grp);
    return 0;
}

static uintptr_t hk_end_step(void *self, uintptr_t flag, void *a, void *b)
{
    int ph = match_phase();
    ULONGLONG now = GetTickCount64();
    if (ph > 0 && ph < 8 && now - (ULONGLONG)g_final_asked > 60000 && match_online()) {   /* half time: ask once */
        InterlockedExchange64(&g_final_asked, (LONGLONG)now);
        InterlockedExchange(&g_final_group, 0);
        HANDLE t = CreateThread(NULL, 0, final_thread, NULL, 0, NULL);
        if (t) CloseHandle(t);
    }
    BOOL armed = g_final_group && match_winner() >= 0 && match_online();
    end_branch(armed ? 0xeb : 0x74);
    uintptr_t r = o_end_step(self, flag, a, b);
    if (armed && (r & 0xff)) {                     /* the full-time step is over: disarm */
        InterlockedExchange(&g_final_group, 0);
        end_branch(0x74);
    }
    return r;
}

static uint32_t hk_end_pick(void *a, void *b, void *res, uintptr_t pk)
{
    LONG grp = g_final_group;
    int side = match_winner();
    if (grp && side >= 0 && res && match_online()) {
        *(int32_t *)((uint8_t *)res + 0x200) = side;                /* the side the ceremony is about */
        logf_("cup final over (phase %d%s): trophy ceremony for side %d", match_phase(), (pk & 0xff) ? ", shootout" : "", side);
        /* the match is over for this game: tell the server, which settles it as at a final whistle */
        HANDLE t = CreateThread(NULL, 0, final_thread, (LPVOID)1, 0, NULL);
        if (t) CloseHandle(t);
        return (uint32_t)grp + ((pk & 0xff) ? 1 : 0);                /* each END group's _PK variant follows it */
    }
    return o_end_pick(a, b, res, pk);
}

/* The exact 1.01.00 bytes: a different build gets no trophy hooks (and no "trophy1"). */
static const struct { uint64_t va; const char *hex; } trophy_code[] = {
    { 0x140447290, "48896c24104889742418574881ec9000" },  /* full-time step */
    { 0x1404473e7, "ff9088000000" "84c0746a" },          /* call [rax+0x88] (online?); test al,al; je no-demo */
    { 0x1404879b0, "40555657415441554156415748" },        /* END group picker */
    { 0x140446445, "4c8bc5400fb6d6488bcbe83c0e0000" },    /* sequencer event 7 -> the full-time step */
};

static void hook_trophy(void)
{
    if (!g_exe) g_exe = (uint8_t *)GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)(g_exe + ((const IMAGE_DOS_HEADER *)g_exe)->e_lfanew);
    if (PES_BASE + nt->OptionalHeader.SizeOfImage <= 0x1436f3dc8 + 8) { logf_("trophy ceremony: not PES 2021 1.01.00, off"); return; }
    for (size_t i = 0; i < sizeof trophy_code / sizeof trophy_code[0]; i++)
        if (!code_is(trophy_code[i].va, trophy_code[i].hex)) {
            logf_("trophy ceremony: game code at %llx is not 1.01.00, off", (unsigned long long)trophy_code[i].va);
            return;
        }
    if (MH_CreateHook(game_va(0x140447290), (void *)hk_end_step, (void **)&o_end_step) != MH_OK ||
        MH_CreateHook(game_va(0x1404879b0), (void *)hk_end_pick, (void **)&o_end_pick) != MH_OK) {
        logf_("ERROR: trophy ceremony hooks"); return;
    }
    InterlockedExchange(&g_trophy_on, 1);
    logf_("trophy ceremony for cup finals ready");
}

/* ------------------------------------------------------------------ the cup bracket ("bracket1")
 * Konami's own bracket screen of the offline cups ("Tournament Table", flow node Common/CmnTournamentMenu,
 * menu::MenuModeTournamentProgress) for an online cup: after a cup match, and in place of Rankings, the mod
 * opens it with the server's knockout (/mod/bracket). The screen reads the offline competition the save
 * has selected (save+0x1787b5c: competition id, instance id; save+0x183789c: data state; the instance table
 * at save+0xc12e9c, 0x314 bytes each: +0x170 the team per place, +0x30a the number of places), the round
 * tables (0x141579ee0(&instance, &kind) -> 16 match records of 0x20 bytes at +4, count at +0x204; kinds
 * 0x2e..0x35, 0x35 the final, 0x36 third place), fixtures (0x1414bb280(save, id) -> played at +4 bit 30, teams
 * at +0x14 / +0x18, goals at +0x1c / +0x1f) and the winner of a match record (0x14151b230). While it is open the
 * mod lends it one of the save's own knockout competitions with our teams in it (put back on close), and
 * answers the round-table, fixture and winner questions for that competition from the server's bracket;
 * everything else in the game reads its own data. Return goes back to the cup's menu. Team ids: id << 14.
 * notes/re/functions/tourney.md, "Cup bracket". */
#define BR_FID0 0xf000                          /* our fixture ids (the game's own are far below) */
typedef struct { int rnd, idx; uint32_t ta, tb; int ga, gb, win, played; } br_tie;
static struct {
    int size, rounds, nties;
    uint32_t slot[32];
    br_tie tie[64];
    br_tie third; int has_third, third_place;    /* third: the match; third_place: the cup has one */
} g_br;                                         /* the server's bracket (filled before opening) */
static uint8_t g_br_round[9][0x208];            /* our round tables: [0..7] kinds 0x2e..0x35, [8] 0x36 */
static uint8_t g_br_fix[66][0x17c];             /* our fixtures: BR_FID0 + n */
static int32_t g_br_win[66];                    /* their winners: 0 / 1 side, -1 none */
static volatile LONG g_br_open;                 /* the lent competition is in place */
static CRITICAL_SECTION g_br_lock;              /* lending and giving back (the game thread and br_thread) */
static volatile LONG g_br_ready;                /* g_br holds the bracket of the cup whose menu is shown */
static uintptr_t (*o_flow_post)(void *, uintptr_t, const char *);   /* 0x14149b790(mgr, message, text) */
static uint16_t g_br_inst;                      /* its instance id */
static uint8_t *g_br_entry;                     /* its entry in the save */
static uint8_t g_br_save_entry[0x314], g_br_save_key[12], g_br_save_state[4];
static uintptr_t (*o_br_round)(void *, void *);           /* 0x141579ee0 */
static uint8_t *(*o_br_fixture)(void *, uintptr_t);       /* 0x1414bb280 */
static uint32_t *(*o_br_winner)(uint32_t *, void *);      /* 0x14151b230 */
static uintptr_t (*o_br_fix_exists)(void *, uint16_t *);  /* 0x1414bb950(save, &id) */
static void (*game_post)(void *, uint32_t, const char *); /* 0x14149b790(mgr, message, text) */

static uint8_t *game_save(void)
{
    uint8_t *m = *(uint8_t **)game_va(0x143705e10);
    return m ? *(uint8_t **)(m + 0x48) : NULL;
}

/* jump to a flow node; names: game strings 0x142658880 "Common/CmnTournamentMenu",
   0x1426dae38 "Online/PesLg/1vs1/PesLg1vs1MainMenu" (the message queue takes posts from any thread) */
static void flow_jump(uint64_t name_va)
{
    void *mgr = *(void **)game_va(0x143704e88);
    if (mgr) game_post(mgr, 0x1030008, (const char *)game_va(name_va));
}

/* the current flow node's name ([0x1437f8990]+0x60, an MSVC std::string) */
static void flow_name(char *out, size_t cap)
{
    out[0] = 0;
    uint8_t *fl = *(uint8_t **)game_va(0x1437f8990);
    if (!fl) return;
    uint8_t *str = fl + 0x60;
    size_t n = *(size_t *)(str + 0x10), c = *(size_t *)(str + 0x18);
    if (n >= cap || n > 200) return;
    const char *t = c >= 16 ? *(const char **)str : (const char *)str;
    if (!t) return;
    memcpy(out, t, n);
    out[n] = 0;
}

/* a screen change is under way ([0x1437f8990]+0x30, the transition step: 0 = idle); a jump posted
   meanwhile is lost (live 2026-11-22) */
static BOOL flow_busy(void)
{
    uint8_t *fl = *(uint8_t **)game_va(0x1437f8990);
    return !fl || *(uint32_t *)(fl + 0x30) != 0;
}

static BOOL br_parse(const char *body)
{
    memset(&g_br, 0, sizeof g_br);
    for (const char *l = body; l && *l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : NULL) {
        unsigned a[8] = {0}; int n;
        if (sscanf(l, "size %u", &a[0]) == 1) g_br.size = (int)a[0];
        else if (sscanf(l, "thirdplace %u", &a[0]) == 1) g_br.third_place = (int)a[0];
        else if (sscanf(l, "slot %u %u", &a[0], &a[1]) == 2 && a[0] < 32) g_br.slot[a[0]] = a[1] ? a[1] << 14 : 0xffffffff;
        else if ((n = sscanf(l, "tie %u %u %u %u %u %u %d %u", &a[0], &a[1], &a[2], &a[3], &a[4], &a[5], (int *)&a[6], &a[7])) == 8
                 && g_br.nties < 64) {
            br_tie *t = &g_br.tie[g_br.nties++];
            t->rnd = (int)a[0]; t->idx = (int)a[1]; t->ta = a[2] ? a[2] << 14 : 0xffffffff; t->tb = a[3] ? a[3] << 14 : 0xffffffff;
            t->ga = (int)a[4]; t->gb = (int)a[5]; t->win = (int)a[6]; t->played = (int)a[7];
        } else if (sscanf(l, "third %u %u %u %u %d %u", &a[0], &a[1], &a[2], &a[3], (int *)&a[4], &a[5]) == 6) {
            br_tie *t = &g_br.third; g_br.has_third = 1;
            t->ta = a[0] ? a[0] << 14 : 0xffffffff; t->tb = a[1] ? a[1] << 14 : 0xffffffff;
            t->ga = (int)a[2]; t->gb = (int)a[3]; t->win = (int)a[4]; t->played = (int)a[5];
        }
    }
    int r = 0;
    while ((1 << r) < g_br.size) r++;
    g_br.rounds = r;
    return g_br.size >= 2 && g_br.size <= 32 && g_br.size == 1 << r;
}

static BOOL br_fetch(void)
{
    char ip[64], url[256];
    static char body[8192];
    snprintf(ip, sizeof ip, "%s", g_ip);
    if (!ip[0] || !g_id_hex[0]) return FALSE;
    snprintf(url, sizeof url, "http://%s:8080/mod/bracket?id=%s", ip, g_id_hex);
    return http_get(url, body, sizeof body, 2500) && br_parse(body);
}

/* our round tables and fixtures from g_br */
static void br_build(void)
{
    memset(g_br_round, 0, sizeof g_br_round);
    memset(g_br_fix, 0, sizeof g_br_fix);
    int nf = 0;
    for (int i = 0; i <= g_br.nties; i++) {
        br_tie *t = i < g_br.nties ? &g_br.tie[i] : (g_br.has_third ? &g_br.third : NULL);
        if (!t) break;
        int k = i < g_br.nties ? 7 - (g_br.rounds - t->rnd) : 8;          /* the final is kind 0x35 (index 7) */
        if (t->ta == 0xffffffff || t->tb == 0xffffffff) continue;          /* (a tie exists once both are known) */
        if (k < 0 || k > 8 || nf >= 66) continue;
        uint8_t *R = g_br_round[k];
        uint32_t cnt = *(uint32_t *)(R + 0x204);
        if (cnt >= 16) continue;
        uint8_t *m = R + 4 + 0x20 * cnt;
        *(uint32_t *)(m + 0) = t->ta; *(uint32_t *)(m + 4) = t->tb;
        *(uint16_t *)(m + 8) = (uint16_t)(BR_FID0 + nf); *(uint16_t *)(m + 0xa) = 0xffff;
        *(uint32_t *)(R + 0x204) = cnt + 1;
        uint8_t *F = g_br_fix[nf++];
        *(uint16_t *)F = (uint16_t)(BR_FID0 + nf - 1);                    /* its own id (checked) */
        *(uint32_t *)(F + 4) = (t->played ? 1u << 30 : 0) | 1u << 28;       /* played; leg type 1: one match */
        *(uint32_t *)(F + 0x14) = t->ta; *(uint32_t *)(F + 0x18) = t->tb;
        F[0x1c] = (uint8_t)t->ga; F[0x1f] = (uint8_t)t->gb;
        g_br_win[nf - 1] = t->win;
    }
}

static int br_round_kind(uint32_t kind) { return kind >= 0x2e && kind <= 0x35 ? (int)(kind - 0x2e) : kind == 0x36 ? 8 : -1; }

static int g_br_debug;                          /* PES2021ONLINE_BRACKET_DEBUG=1: no round tables (tests only) */
static uintptr_t hk_br_round(void *inst, void *kind)
{
    if (g_br_open && inst && kind && *(uint16_t *)inst == g_br_inst) {
        if (g_br_debug == 1) return 0;
        int k = br_round_kind(*(uint32_t *)kind);
        return k >= 0 && *(uint32_t *)(g_br_round[k] + 0x204) ? (uintptr_t)g_br_round[k] : 0;
    }
    return o_br_round(inst, kind);
}

static uint8_t *hk_br_fixture(void *save, uintptr_t id)
{
    unsigned f = (unsigned)(id & 0xffff);
    if (g_br_open && f >= BR_FID0 && f < BR_FID0 + 66) return g_br_fix[f - BR_FID0];
    return o_br_fixture(save, id);
}

static uintptr_t hk_br_fix_exists(void *save, uint16_t *id)
{
    if (g_br_open && id && *id >= BR_FID0 && *id < BR_FID0 + 66) return 1;
    return o_br_fix_exists(save, id);
}

static uint32_t *hk_br_winner(uint32_t *out, void *rec)
{
    uint8_t *m = rec;                        /* (often the screen's own copy of a record: known by its fixture id) */
    unsigned f = m ? *(uint16_t *)(m + 8) : 0;
    if (g_br_open && out && f >= BR_FID0 && f < BR_FID0 + 66) {
        int32_t w = g_br_win[f - BR_FID0];
        *out = w == 0 ? *(uint32_t *)m : w == 1 ? *(uint32_t *)(m + 4) : 0xffffffff;
        return out;
    }
    return o_br_winner(out, rec);
}

/* Lend the screen one of the save's own knockout competitions, with our places in it. */
static BOOL br_lend_locked(void)
{
    uint8_t *save = game_save();
    if (!save || g_br_open) return FALSE;
    uint32_t count = *(uint32_t *)(save + 0xd0bcf4);
    static const uint32_t final_set[] = { 2, 3, 5, 6, 8, 47, 48, 70, 71, 72, 89, 91, 97, 128 };
    uint8_t *pick = NULL;
    for (uint32_t i = 0; i < count && i < 300; i++) {
        uint8_t *e = save + 0xc12e9c + i * 0x314;
        uint32_t comp = *(uint32_t *)(e + 0x80), kind = *(uint32_t *)(e + 0x84);
        if (kind != 3 || (*(uint16_t *)(e + 0x30a) & 0x7f) < 2 || *(uint16_t *)(e + 0x76) != 0xffff) continue;
        BOOL skip = FALSE;
        for (size_t j = 0; j < sizeof final_set / sizeof final_set[0]; j++) skip |= comp == final_set[j];
        if (skip) continue;
        if (!pick || comp == 34) pick = e;                                 /* (the Copa América if it is there) */
        if (comp == 34) break;
    }
    if (!pick) { logf_("cup bracket: no knockout competition in this save to lend"); return FALSE; }
    g_br_entry = pick;
    g_br_inst = *(uint16_t *)pick;
    memcpy(g_br_save_entry, pick, 0x314);
    memcpy(g_br_save_key, save + 0x1787b5c, 12);
    memcpy(g_br_save_state, save + 0x183789c, 4);
    for (int i = 0; i < 32; i++) *(uint32_t *)(pick + 0x170 + 4 * i) = i < g_br.size ? g_br.slot[i] : 0xffffffff;
    *(uint16_t *)(pick + 0x30a) = (uint16_t)((*(uint16_t *)(pick + 0x30a) & ~0x7f) | g_br.size);
    uint32_t f = *(uint32_t *)(pick + 0x304);     /* bit 9: the "3rd Place Playoffs" card (set in the Copa America) */
    *(uint32_t *)(pick + 0x304) = g_br.third_place ? f | 0x200 : f & ~0x200u;
    *(uint32_t *)(save + 0x1787b5c) = *(uint32_t *)(pick + 0x80);
    *(uint16_t *)(save + 0x1787b60) = g_br_inst;
    *(uint32_t *)(save + 0x183789c) = 1;
    br_build();
    InterlockedExchange(&g_br_open, 1);
    return TRUE;
}

static BOOL br_lend(void)
{
    EnterCriticalSection(&g_br_lock);
    BOOL ok = br_lend_locked();
    LeaveCriticalSection(&g_br_lock);
    return ok;
}

static void br_give_back(void)
{
    EnterCriticalSection(&g_br_lock);
    uint8_t *save = game_save();
    if (g_br_open && save && g_br_entry) {         /* (the save first, the flag last) */
        memcpy(g_br_entry, g_br_save_entry, 0x314);
        memcpy(save + 0x1787b5c, g_br_save_key, 12);
        memcpy(save + 0x183789c, g_br_save_state, 4);
    }
    InterlockedExchange(&g_br_open, 0);
    LeaveCriticalSection(&g_br_lock);
}

/* Rankings in a cup: the cup menu queues flow message 0x1030006 "02_ranking" (fire that event) once its menu
   has closed; with the cup's bracket at hand the mod queues the jump to the bracket instead. (Going to
   Rankings first and on from there crashed the game: its ranking request answers a screen that is gone.)
   The text is an MSVC std::string; a short one keeps its characters in place. */
static uintptr_t hk_flow_post(void *mgr, uintptr_t msg, const char *text)
{
    if ((uint32_t)msg == 0x1030006 && text && g_br_open && !strncmp(text, "return", 7)) {
        char name[128];                      /* Return on our bracket: back to the online cup's menu */
        flow_name(name, sizeof name);
        if (!strcmp(name, "Common/CmnTournamentMenu"))
            return o_flow_post(mgr, 0x1030008, (const char *)game_va(0x1426dae38));
    }
    if ((uint32_t)msg == 0x1030006 && text && g_br_ready && !g_br_open && !strncmp(text, "02_ranking", 11)) {
        char name[128];
        flow_name(name, sizeof name);
        if (!strcmp(name, "Online/PesLg/1vs1/PesLg1vs1MainMenu") && br_lend()) {
            logf_("cup bracket: %d places, %d ties (Rankings)", g_br.size, g_br.nties);
            return o_flow_post(mgr, 0x1030008, (const char *)game_va(0x142658880));
        }
    }
    return o_flow_post(mgr, msg, text);
}

/* Watches the screens: after a cup match (a Match/ screen, then the cup's menu again) and on Rankings in a
   cup, asks the server for the bracket and opens it; gives the competition back once the screen is left. */
static DWORD WINAPI br_thread(LPVOID unused)
{
    (void)unused;
    char name[128], last[128] = "";
    BOOL after_match = FALSE, seen = FALSE;
    ULONGLONG left_at = 0, lent_at = 0, fetched_at = 0;
    for (;;) {
        Sleep(150);
        flow_name(name, sizeof name);
        if (!name[0]) continue;
        BOOL on_br = !strcmp(name, "Common/CmnTournamentMenu");
        if (!g_br_open) { seen = FALSE; lent_at = 0; left_at = 0; }
        else if (on_br) { seen = TRUE; left_at = 0; }
        else {
            ULONGLONG now = GetTickCount64();
            if (!lent_at) lent_at = now;
            if (seen && !left_at) left_at = now;
            /* the screen was left (1.5 s for it to fade out), or the jump never arrived (10 s) */
            if ((seen && now - left_at > 1500) || (!seen && now - lent_at > 10000 && !flow_busy())) {
                br_give_back();
                logf_("cup bracket closed%s", seen ? "" : " (it never opened)");
                last[0] = 0;                         /* (the cup's menu again: fetch the bracket anew) */
                continue;
            }
        }
        if (!strcmp(name, last) && !g_br_open && !strcmp(name, "Online/PesLg/1vs1/PesLg1vs1MainMenu")
            && GetTickCount64() - fetched_at > 15000) {          /* still on the cup's menu: the draw may be new */
            fetched_at = GetTickCount64();
            InterlockedExchange(&g_br_ready, br_fetch());
        }
        if (strcmp(name, last)) {
            if (!strncmp(name, "Match/", 6) && match_online()) after_match = TRUE;     /* (online matches only) */
            if (!strncmp(name, "TopMenu/", 8)) after_match = FALSE;
            BOOL menu = !strcmp(name, "Online/PesLg/1vs1/PesLg1vs1MainMenu");
            if (menu && g_br_open) continue;            /* (back from the bracket: handled once it is given back) */
            if (menu) {
                InterlockedExchange(&g_br_ready, 0);
                fetched_at = GetTickCount64();
                InterlockedExchange(&g_br_ready, br_fetch());                 /* (for Rankings too) */
                if (after_match && g_br_ready) {
                    char was[128];
                    snprintf(was, sizeof was, "%s", name);
                    for (int i = 0; i < 100 && flow_busy(); i++) Sleep(30);     /* let the screen finish arriving */
                    Sleep(300);
                    flow_name(name, sizeof name);
                    if (!strcmp(name, was) && br_lend()) {                     /* (still there: nobody moved on) */
                        logf_("cup bracket: %d places, %d ties (after a match)", g_br.size, g_br.nties);
                        flow_jump(0x142658880);
                    }
                }
                after_match = FALSE;
            } else if (!menu && strcmp(name, "Common/CmnTournamentMenu")) InterlockedExchange(&g_br_ready, 0);
            snprintf(last, sizeof last, "%s", name);
        }
    }
    return 0;
}

static const struct { uint64_t va; const char *hex; } bracket_code[] = {
    { 0x141579ee0, "48895c240848896c2410488974241857" },  /* round table of (instance, kind) */
    { 0x1414bb280, "40534883ec20488bd9e8d21200004885" },  /* fixture by id */
    { 0x14151b230, "48895c2408574883ec20488bfa488bd9" },  /* winner of a match record */
    { 0x1414bb950, "4883ec280fb712e8040c00004885c00f" },  /* does a fixture id exist */
    { 0x14149b790, "405356574883ec6048c7442428feffff" },  /* post a flow message */
    { 0x142658880, "436f6d6d6f6e2f436d6e546f75726e61" },  /* "Common/CmnTournamentMenu" */
    { 0x1426dae38, "4f6e6c696e652f5065734c672f317673" },  /* "Online/PesLg/1vs1/PesLg1vs1MainMenu" */
};

static void hook_bracket(void)
{
    if (!g_exe) g_exe = (uint8_t *)GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)(g_exe + ((const IMAGE_DOS_HEADER *)g_exe)->e_lfanew);
    if (PES_BASE + nt->OptionalHeader.SizeOfImage <= 0x143705e10 + 8) { logf_("cup bracket: not PES 2021 1.01.00, off"); return; }
    for (size_t i = 0; i < sizeof bracket_code / sizeof bracket_code[0]; i++)
        if (!code_is(bracket_code[i].va, bracket_code[i].hex)) {
            logf_("cup bracket: game code at %llx is not 1.01.00, off", (unsigned long long)bracket_code[i].va);
            return;
        }
    game_post = (void (*)(void *, uint32_t, const char *))game_va(0x14149b790);
    InitializeCriticalSection(&g_br_lock);
    g_br_debug = GetEnvironmentVariableA("PES2021ONLINE_BRACKET_DEBUG", NULL, 0) > 0 ? 1 : 0;
    if (MH_CreateHook(game_va(0x141579ee0), (void *)hk_br_round, (void **)&o_br_round) != MH_OK ||
        MH_CreateHook(game_va(0x1414bb280), (void *)hk_br_fixture, (void **)&o_br_fixture) != MH_OK ||
        MH_CreateHook(game_va(0x14151b230), (void *)hk_br_winner, (void **)&o_br_winner) != MH_OK ||
        MH_CreateHook(game_va(0x1414bb950), (void *)hk_br_fix_exists, (void **)&o_br_fix_exists) != MH_OK ||
        MH_CreateHook(game_va(0x14149b790), (void *)hk_flow_post, (void **)&o_flow_post) != MH_OK) {
        logf_("ERROR: cup bracket hooks"); return;
    }
    HANDLE t = CreateThread(NULL, 0, br_thread, NULL, 0, NULL);
    if (t) CloseHandle(t);
    InterlockedExchange(&g_bracket_on, 1);
    logf_("cup bracket screen ready");
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

#include "probe.c"

static void install_hooks(void)
{
    MH_STATUS st = MH_Initialize();
    if (st != MH_OK) { logf_("ERROR: MinHook: %s", MH_StatusToString(st)); return; }
    hook(L"ws2_32.dll", "getaddrinfo", hk_getaddrinfo, (void **)&o_getaddrinfo);
    hook(L"ws2_32.dll", "GetAddrInfoW", hk_GetAddrInfoW, (void **)&o_GetAddrInfoW);
    hook(L"ws2_32.dll", "GetAddrInfoExW", hk_GetAddrInfoExW, (void **)&o_GetAddrInfoExW);
    hook(L"ws2_32.dll", "gethostbyname", hk_gethostbyname, (void **)&o_gethostbyname);
    hook_math();
    hook_coop();
    hook_trophy();
    hook_bracket();
    if (g_id_hex[0]) {
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
    probe_start();          /* debug builds only (PROBE=1 sh build.sh), and only with PES2021ONLINE_PROBE=<port> */
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
    logf_("PES 2021 Online mod " MOD_VERSION "; server \"%s\" key %.16s...%s%s", g_srv.name,
          g_srv.keyhex[0] ? g_srv.keyhex : "(none)", g_force_prompt ? " (Shift held)" : "",
          g_invite[0] ? "; made for one player (invite)" : "");
    install_hooks();
    if (GetEnvironmentVariableA("PES2021ONLINE_TEST_MATH", NULL, 0)) use_server(0);  /* tests: same math, no server */
    return TRUE;
}
