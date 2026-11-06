/* The parts of <math.h> musl's code here uses, the same for every build. (The system's own math.h
 * would be renamed by pesm_names.h too, which glibc's macros don't survive.) x86-64 evaluates float
 * and double in their own precision (SSE, FLT_EVAL_METHOD 0). */
#ifndef PESM_MATH_H
#define PESM_MATH_H
typedef double double_t;
typedef float float_t;
#define NAN (__builtin_nanf(""))
#define INFINITY (__builtin_inff())
#define HUGE_VAL (__builtin_huge_val())
#define HUGE_VALF (__builtin_huge_valf())
#define FP_NAN 0
#define FP_INFINITE 1
#define FP_ZERO 2
#define FP_SUBNORMAL 3
#define FP_NORMAL 4
#define fpclassify(x) __builtin_fpclassify(FP_NAN, FP_INFINITE, FP_NORMAL, FP_SUBNORMAL, FP_ZERO, x)
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)
#define isfinite(x) __builtin_isfinite(x)
#define signbit(x) __builtin_signbit(x)
#define M_PI_2 1.57079632679489661923   /* pi/2, as in musl and glibc */
double acos(double); float acosf(float); double asin(double); float asinf(float);
double atan(double); float atanf(float); double atan2(double, double); float atan2f(float, float);
float cbrtf(float); double cos(double); float cosf(float); double cosh(double);
double exp(double); float expf(float); double expm1(double); double floor(double);
double log(double); float logf(float); double log10(double); float log10f(float);
double pow(double, double); float powf(float, float); double scalbn(double, int);
double sin(double); float sinf(float); double sinh(double); double tan(double); float tanf(float);
double tanh(double); double sqrt(double); float sqrtf(float); double fabs(double); float fabsf(float);
#endif
