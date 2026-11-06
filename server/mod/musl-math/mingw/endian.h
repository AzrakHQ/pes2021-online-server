/* musl's libm.h wants <endian.h>; MinGW has none. x86-64 is little-endian. */
#define __LITTLE_ENDIAN 1234
#define __BIG_ENDIAN 4321
#define __BYTE_ORDER __LITTLE_ENDIAN
