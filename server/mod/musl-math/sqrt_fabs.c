/* The exact operations musl's code calls: one SSE2 instruction or a sign-bit mask, the same everywhere. */
#include <stdint.h>
#include <string.h>
double sqrt(double x) { __asm__("sqrtsd %1, %0" : "=x"(x) : "x"(x)); return x; }
float sqrtf(float x) { __asm__("sqrtss %1, %0" : "=x"(x) : "x"(x)); return x; }
double fabs(double x) { uint64_t u; memcpy(&u, &x, 8); u &= ~(1ULL << 63); memcpy(&x, &u, 8); return x; }
float fabsf(float x) { uint32_t u; memcpy(&u, &x, 4); u &= ~(1U << 31); memcpy(&x, &u, 4); return x; }
