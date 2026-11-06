# Same math on every PC

The match simulation runs on both players' PCs and must give the same numbers on both. The game
takes `sinf`, `powf`, `atan2f` and the like from the C runtime, which is Microsoft's on Windows and
Wine's own (accurate, but not bit-identical) under Proton / Steam Deck. The mod hands the game these
functions instead, the same code on every PC.

The `.c` files except `sqrt_fabs.c` and `pesm_qsort.c` are from [musl](https://musl.libc.org/) (the copy bundled with Zig 0.13.0)
(MIT, see `COPYRIGHT`), unchanged. `pesm_names.h` renames them (`sinf` → `pesm_sinf`, internal
`__sin` → `pesm___sin`) so they never mix with a C library's. Build flags that keep the results
identical everywhere: `-march=x86-64 -mno-fma -ffp-contract=off -fno-fast-math` (plain SSE2, no FMA:
musl's `log` has an FMA shortcut that a compiler whose default CPU has FMA would otherwise take).

`include/math.h` (ours) declares just what this code needs, the same for every build. `mingw/` has the two musl headers MinGW lacks (`features.h`, `endian.h`); Linux has its own.

`../test/mathcheck.c` checks that the DLL gives the game exactly these results (CI runs it on
Windows against a reference built on Linux).
