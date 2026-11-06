/* qsort for the game, the same on every PC. The C libraries' qsort can order equal elements
 * differently (Microsoft's vs Wine's), which would make two games' match simulations drift apart.
 * Insertion sort for short arrays, heapsort otherwise: in place, no recursion, no allocation. */
#include <stddef.h>

typedef int (*pesm_cmp)(const void *, const void *);

static void swapb(char *a, char *b, size_t n)
{
    while (n--) { char t = *a; *a++ = *b; *b++ = t; }
}

static void sift(char *base, size_t root, size_t n, size_t w, pesm_cmp cmp)
{
    for (;;) {
        size_t child = 2 * root + 1;
        if (child >= n) return;
        if (child + 1 < n && cmp(base + child * w, base + (child + 1) * w) < 0) child++;
        if (cmp(base + root * w, base + child * w) >= 0) return;
        swapb(base + root * w, base + child * w, w);
        root = child;
    }
}

void pesm_qsort(void *b, size_t n, size_t w, pesm_cmp cmp)
{
    char *base = b;
    if (n < 2 || !w) return;
    if (n <= 16) {
        for (size_t i = 1; i < n; i++)
            for (size_t j = i; j > 0 && cmp(base + (j - 1) * w, base + j * w) > 0; j--)
                swapb(base + (j - 1) * w, base + j * w, w);
        return;
    }
    for (size_t i = n / 2; i-- > 0;) sift(base, i, n, w, cmp);
    for (size_t end = n - 1; end > 0; end--) {
        swapb(base, base + end * w, w);
        sift(base, 0, end, w, cmp);
    }
}
