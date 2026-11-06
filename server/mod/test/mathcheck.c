/* Checks that every PC gets the same math in a match (musl-math/README.md).
 *
 * Built for Windows (with the mod's DLL as dinput8.dll next to it), it prints a fingerprint of what
 * the C runtime (ucrtbase.dll) answers for each function the mod replaces: first without the mod
 * ("native ..." lines: Microsoft's or Wine's own results), then with the mod loaded and its same-math
 * switch on. Built for Linux, it prints the same lines straight from musl-math (the reference).
 * The lines without "native" must be identical everywhere.
 *   Linux:   cc -O2 -ffp-contract=off -Imusl-math ... (see .github/workflows/program.yml)
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#ifdef _WIN32
#include <windows.h>
#endif

#define N 200000
typedef double (*d1)(double); typedef float (*f1)(float);
typedef double (*d2)(double, double); typedef float (*f2)(float, float);
typedef int (*cmpf)(const void *, const void *);
typedef void (*qsortf)(void *, size_t, size_t, cmpf);

#define D1(X) X(acos) X(asin) X(atan) X(cos) X(cosh) X(exp) X(log) X(log10) X(sin) X(sinh) X(tan) X(tanh)
#define F1(X) X(acosf) X(asinf) X(atanf) X(cbrtf) X(cosf) X(expf) X(log10f) X(logf) X(sinf) X(tanf)
#define D2(X) X(atan2) X(pow)
#define F2(X) X(atan2f) X(powf)

static uint64_t rng;
static uint64_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
/* Inputs: special values, then random values with exponents from 2^-24 to 2^24, both signs. */
static double in_d(int i)
{
    static const double sp[] = {0.0, -0.0, 1.0, -1.0, 0.5, 2.0, 3.141592653589793, 1e-310, 1e300, -1e300};
    if (i < 10) return sp[i];
    if (i == 10) return 1.0 / 0.0;
    if (i == 11) return -1.0 / 0.0;
    uint64_t r = next();
    int e = (int)(r % 49) - 24;
    double m = 1.0 + (double)(r >> 12) / 4503599627370496.0;
    double x = m * __builtin_ldexp(1.0, e);
    return (r >> 11 & 1) ? -x : x;
}
static float in_f(int i) { return (float)in_d(i); }
static uint64_t fnv(uint64_t h, const void *p, size_t n)
{
    const unsigned char *b = p;
    while (n--) { h ^= *b++; h *= 1099511628211ULL; }
    return h;
}

struct item { int key, idx; };
static int by_key(const void *a, const void *b) { return ((const struct item *)a)->key - ((const struct item *)b)->key; }
static uint64_t sort_check(qsortf qs, int n)
{
    struct item *v = malloc(sizeof *v * (size_t)n);
    rng = 99;
    for (int i = 0; i < n; i++) { v[i].key = (int)(next() % 50); v[i].idx = i; }
    qs(v, (size_t)n, sizeof *v, by_key);
    uint64_t h = fnv(14695981039346656037ULL, v, sizeof *v * (size_t)n);
    free(v);
    return h;
}

#define RUN_D1(name, fn) { d1 f = (d1)(fn); uint64_t h = 14695981039346656037ULL; rng = 7; \
    for (int i = 0; i < N; i++) { double y = f(in_d(i)); h = fnv(h, &y, 8); } printf("%s%s %016llx\n", tag, name, (unsigned long long)h); }
#define RUN_F1(name, fn) { f1 f = (f1)(fn); uint64_t h = 14695981039346656037ULL; rng = 7; \
    for (int i = 0; i < N; i++) { float y = f(in_f(i)); h = fnv(h, &y, 4); } printf("%s%s %016llx\n", tag, name, (unsigned long long)h); }
#define RUN_D2(name, fn) { d2 f = (d2)(fn); uint64_t h = 14695981039346656037ULL; rng = 7; \
    for (int i = 0; i < N; i++) { double a = in_d(i), b = in_d(i) / 4096; double y = f(a, b); h = fnv(h, &y, 8); } \
    printf("%s%s %016llx\n", tag, name, (unsigned long long)h); }
#define RUN_F2(name, fn) { f2 f = (f2)(fn); uint64_t h = 14695981039346656037ULL; rng = 7; \
    for (int i = 0; i < N; i++) { float a = in_f(i), b = in_f(i) / 4096; float y = f(a, b); h = fnv(h, &y, 4); } \
    printf("%s%s %016llx\n", tag, name, (unsigned long long)h); }

#ifdef _WIN32
static HMODULE crt;
static void *get(const char *n) { void *p = (void *)GetProcAddress(crt, n); if (!p) { printf("missing %s\n", n); exit(2); } return p; }
#define W_D1(n) RUN_D1(#n, get(#n))
#define W_F1(n) RUN_F1(#n, get(#n))
#define W_D2(n) RUN_D2(#n, get(#n))
#define W_F2(n) RUN_F2(#n, get(#n))
static void all(const char *tag)
{
    D1(W_D1) F1(W_F1) D2(W_D2) F2(W_F2)
    printf("%sqsort10 %016llx\n%sqsort5000 %016llx\n", tag, (unsigned long long)sort_check((qsortf)get("qsort"), 10),
           tag, (unsigned long long)sort_check((qsortf)get("qsort"), 5000));
}
int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    crt = LoadLibraryW(L"ucrtbase.dll");
    if (!crt) { printf("no ucrtbase.dll\n"); return 2; }
    all("native ");
    SetEnvironmentVariableA("PES2021ONLINE_TEST_MATH", "1");
    if (!LoadLibraryW(L"dinput8.dll")) { printf("mod not loaded (%lu)\n", GetLastError()); return 2; }
    all("");
    /* the game links to api-ms-win-crt-math-l1-1-0.dll: that name must lead to the same hooked code */
    HMODULE apiset = LoadLibraryW(L"api-ms-win-crt-math-l1-1-0.dll");
    if (apiset) { crt = apiset; all("apiset "); }
    return 0;
}
#else
#define L_D1(n) double pesm_##n(double); RUN_D1(#n, pesm_##n)
#define L_F1(n) float pesm_##n(float); RUN_F1(#n, pesm_##n)
#define L_D2(n) double pesm_##n(double, double); RUN_D2(#n, pesm_##n)
#define L_F2(n) float pesm_##n(float, float); RUN_F2(#n, pesm_##n)
void pesm_qsort(void *, size_t, size_t, cmpf);
int main(void)
{
    const char *tag = "";
    D1(L_D1) F1(L_F1) D2(L_D2) F2(L_F2)
    printf("qsort10 %016llx\nqsort5000 %016llx\n", (unsigned long long)sort_check(pesm_qsort, 10),
           (unsigned long long)sort_check(pesm_qsort, 5000));
    return 0;
}
#endif
