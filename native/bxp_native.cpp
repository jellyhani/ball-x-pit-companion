// BALL x PIT 도우미 — 계산 전용 네이티브 모듈 (채집 궤적).
//
// src/engine/harvest_sim.py 의 simulate_team 과 같은 계산을 같은 순서·같은 부동소수 연산으로 한다.
// 파이썬 쪽(src/engine/native.py)이 ctypes 로 부르고, 결과가 파이썬 구현과 같은지 tests/test_native.py 가 확인한다.
// 게임과는 관련 없는 순수 계산 (입력은 플러그인이 보낸 기지 모양·건물 값).
//
// C 런타임·표준 라이브러리를 쓰지 않는다 (의존성 없는 DLL — 어느 PC 에서나, Windows SDK 없이 빌드).
// 메모리는 모두 호출하는 쪽이 넘겨준다. 제곱근은 SSE2 sqrtsd (IEEE 정확 반올림 — 파이썬 math.sqrt 와 같음).
//
// 빌드: native\build.ps1 (Visual Studio C++ 도구) → src\engine\bxp_native.dll
// emmintrin.h 는 C 런타임 헤더를 끌어오므로, 쓰는 SSE2 내장 함수만 컴파일러 선언 그대로 적는다.
typedef struct __declspec(intrin_type) __declspec(align(16)) __m128d {
    double m128d_f64[2];
} __m128d;
extern "C" {
extern __m128d _mm_sqrt_sd(__m128d, __m128d);
extern __m128d _mm_set_sd(double);
extern double _mm_cvtsd_f64(__m128d);
}
#pragma intrinsic(_mm_sqrt_sd, _mm_set_sd, _mm_cvtsd_f64)

#define BXP_API extern "C" __declspec(dllexport)

extern "C" int _fltused = 0;   // 부동소수를 쓰는 코드에 링커가 요구하는 기호 (CRT 없이 빌드할 때)

namespace {

const double EPS = 1e-6;
const double SPEED_UP = 0.2;

enum { K_CIRCLE = 0, K_BOX = 1, K_POLY = 2 };      // 모양 종류 (파이썬 Shape.kind)
enum { F_WHEAT = 1, F_TILE = 2 };                   // 건물 성질
enum { U_PIERCE_BUILDINGS = 1, U_PIERCE_STONE = 2, U_PIERCE_WOOD = 4 };   // 작업자 채집 강화

inline double fabs_(double x) { return x < 0 ? -x : x; }
inline double sqrt_(double x) { return _mm_cvtsd_f64(_mm_sqrt_sd(_mm_set_sd(x), _mm_set_sd(x))); }
inline double min_(double a, double b) { return b < a ? b : a; }    // 같으면 a (파이썬 min 과 같음)
inline double max_(double a, double b) { return a < b ? b : a; }

bool ray_segment(double ox, double oy, double dx, double dy, double ax, double ay, double bx, double by,
                 double& t, double& nx, double& ny) {
    double ex = bx - ax, ey = by - ay;
    double den = dx * ey - dy * ex;
    if (fabs_(den) < EPS) return false;
    double tt = ((ax - ox) * ey - (ay - oy) * ex) / den;
    double u = ((ax - ox) * dy - (ay - oy) * dx) / den;
    if (tt <= EPS || u < -EPS || u > 1 + EPS) return false;
    double n1 = -ey, n2 = ex;
    if (n1 * dx + n2 * dy > 0) { n1 = -n1; n2 = -n2; }
    t = tt; nx = n1; ny = n2;
    return true;
}

bool misses_box(const double* bb, double ox, double oy, double dx, double dy, double r) {
    double lo[2] = {bb[0] - r, bb[1] - r}, hi[2] = {bb[2] + r, bb[3] + r};
    double o[2] = {ox, oy}, d[2] = {dx, dy};
    double tmin = -1e18, tmax = 1e18;
    for (int k = 0; k < 2; k++) {
        if (fabs_(d[k]) < EPS) {
            if (o[k] < lo[k] || o[k] > hi[k]) return true;
            continue;
        }
        double t1 = (lo[k] - o[k]) / d[k], t2 = (hi[k] - o[k]) / d[k];
        if (t1 > t2) { double s = t1; t1 = t2; t2 = s; }
        tmin = max_(tmin, t1);
        tmax = min_(tmax, t2);
        if (tmin > tmax) return true;
    }
    return tmax <= EPS;
}

struct Geo {
    const int* kind;
    const int* pt_off;
    const int* pt_cnt;
    const double* pts;
    const double* circ;
    const double* bb;      // 경계 상자 4개씩 (파이썬이 계산해 넘김 — Shape.bb 와 같은 값)
};

bool hit_shape(const Geo& g, int i, double ox, double oy, double dx, double dy, double r,
               double& t, double& nx, double& ny) {
    const double* bb = g.bb + 4 * i;
    if (misses_box(bb, ox, oy, dx, dy, r + 1e-3)) return false;
    if (g.kind[i] == K_CIRCLE) {
        double cx = g.circ[3 * i], cy = g.circ[3 * i + 1];
        double R = g.circ[3 * i + 2] + r;
        double fx = ox - cx, fy = oy - cy;
        double b = fx * dx + fy * dy;
        double c = fx * fx + fy * fy - R * R;
        double disc = b * b - c;
        if (disc < 0) return false;
        double tt = -b - sqrt_(disc);
        if (tt <= EPS) return false;
        t = tt; nx = ox + dx * tt - cx; ny = oy + dy * tt - cy;
        return true;
    }
    double box[8];
    const double* p = g.pts + 2 * g.pt_off[i];
    int n = g.pt_cnt[i];
    if (g.kind[i] == K_BOX && r > 0) {
        double x0 = bb[0], y0 = bb[1], x1 = bb[2], y1 = bb[3];
        box[0] = x0 - r; box[1] = y0 - r; box[2] = x1 + r; box[3] = y0 - r;
        box[4] = x1 + r; box[5] = y1 + r; box[6] = x0 - r; box[7] = y1 + r;
        p = box; n = 4;
    }
    bool have = false;
    for (int k = 0; k < n; k++) {
        int j = (k + 1) % n;
        double ht, hx, hy;
        if (ray_segment(ox, oy, dx, dy, p[2 * k], p[2 * k + 1], p[2 * j], p[2 * j + 1], ht, hx, hy) &&
            (!have || ht < t)) {
            have = true; t = ht; nx = hx; ny = hy;
        }
    }
    return have;
}

}  // namespace

BXP_API int bxp_version() { return 1; }

// 여러 작업자를 시간 순서로 함께 돌린다 (harvest_sim.simulate_team 과 같음).
//   world: left, right, bottom, top, radius
//   모양 n_shapes 개: kind, slot(건물 슬롯), bid, pt_off/pt_cnt (pts 의 x,y 쌍), circ (cx, cy, r), bb (x0, y0, x1, y1)
//   건물 슬롯: flags (F_WHEAT/F_TILE), rtype (없으면 -1), res (남은 자원 — 계산하며 바뀜, 호출한 쪽 사본)
//   작업자 n_workers 개: wk (x, y, dx, dy, speed, t — 계산하며 바뀜, 마지막 상태가 남음), ups (U_* 비트)
//   작업 공간: tmp_t (n_shapes), tmp_i (2 * n_shapes)
// 결과: out_total[4], out_gain[4*n_workers], out_counts[슬롯] (부딪힌 횟수),
//       out_path: (작업자, x, y, t) 4개씩 path_cap 개까지. 돌려주는 값 = 경로 점 수 (넘치면 -1).
BXP_API int bxp_simulate_team(const double* world, int n_shapes, const int* kind, const int* slot, const int* bid,
                              const int* pt_off, const int* pt_cnt, const double* pts, const double* circ,
                              const double* bb, const int* flags, const int* rtype, int* res,
                              int n_workers, double* wk, const int* ups,
                              double duration, int max_events,
                              int* out_total, int* out_gain, int* out_counts,
                              double* out_path, int path_cap, double* tmp_t, int* tmp_i) {
    const double left = world[0], right = world[1], bottom = world[2], top = world[3], r = world[4];
    Geo g{kind, pt_off, pt_cnt, pts, circ, bb};
    int npath = 0;
    bool overflow = false;
    for (int i = 0; i < n_workers; i++) {
        if (npath >= path_cap) { overflow = true; break; }
        double* q = out_path + 4 * npath++;
        q[0] = i; q[1] = wk[6 * i]; q[2] = wk[6 * i + 1]; q[3] = wk[6 * i + 5];
    }
    // 벽: 왼·오른·위·아래 (파이썬과 같은 끝점)
    const double walls[4][4] = {{left + r, -1e3, left + r, 1e3}, {right - r, -1e3, right - r, 1e3},
                                {-1e3, top - r, 1e3, top - r}, {-1e3, bottom + r, 1e3, bottom + r}};
    int* p_bid = tmp_i;
    int* p_slot = tmp_i + n_shapes;

    for (int ev = 0; ev < max_events; ev++) {
        int wi = -1;
        for (int i = 0; i < n_workers; i++)
            if (wk[6 * i + 5] < duration && (wi < 0 || wk[6 * i + 5] < wk[6 * wi + 5])) wi = i;
        if (wi < 0) break;
        double* w = wk + 6 * wi;          // x, y, dx, dy, speed, t
        const int wu = ups[wi];
        bool have = false;
        double bt = 0, bnx = 0, bny = 0;
        int bshape = -1;
        for (int i = 0; i < 4; i++) {
            const double* a = walls[i];
            bool inside = (i == 0) ? w[0] >= a[0] - 1e-6 : (i == 1) ? w[0] <= a[0] + 1e-6
                        : (i == 2) ? w[1] <= a[1] + 1e-6 : w[1] >= a[1] - 1e-6;
            bool toward = (i == 0) ? w[2] < 0 : (i == 1) ? w[2] > 0 : (i == 2) ? w[3] > 0 : w[3] < 0;
            if (inside && toward) {
                double t, nx, ny;
                if (ray_segment(w[0], w[1], w[2], w[3], a[0], a[1], a[2], a[3], t, nx, ny) && (!have || t < bt)) {
                    have = true; bt = t; bnx = nx; bny = ny; bshape = -1;
                }
            }
        }
        int np = 0;
        for (int i = 0; i < n_shapes; i++) {
            double t, nx, ny;
            if (!hit_shape(g, i, w[0], w[1], w[2], w[3], r, t, nx, ny)) continue;
            int sl = slot[i], f = flags[sl];
            bool blocks = true;
            if ((f & F_WHEAT) || (wu & U_PIERCE_BUILDINGS)) blocks = false;
            else if (f & F_TILE) {
                int k = rtype[sl];
                if (res[sl] <= 0) blocks = false;
                else if ((k == 3 && (wu & U_PIERCE_STONE)) || (k == 2 && (wu & U_PIERCE_WOOD))) blocks = false;
            }
            if (blocks) {
                if (!have || t < bt) { have = true; bt = t; bnx = nx; bny = ny; bshape = i; }
            } else {
                // 통과한 건물: (거리, id) 순서로 넣어 둔다 (삽입 정렬 — 파이썬 sorted 와 같은 순서)
                int k = np++;
                while (k > 0 && (tmp_t[k - 1] > t || (tmp_t[k - 1] == t && p_bid[k - 1] > bid[i]))) {
                    tmp_t[k] = tmp_t[k - 1]; p_bid[k] = p_bid[k - 1]; p_slot[k] = p_slot[k - 1]; k--;
                }
                tmp_t[k] = t; p_bid[k] = bid[i]; p_slot[k] = sl;
            }
        }
        if (!have) {
            w[5] = duration;
            break;                        // 파이썬과 같음: 아무것에도 닿지 않으면 계산을 끝낸다
        }
        double step = min_(bt, w[4] * 0.5);
        for (int k = 0; k < np; k++) {
            if (tmp_t[k] > step) continue;
            int sl = p_slot[k], n = res[sl], kd = rtype[sl];
            if (n > 0 && kd >= 0) { res[sl] = 0; out_gain[4 * wi + kd] += n; out_total[kd] += n; }
        }
        double end_t = w[5] + step / w[4];
        if (end_t >= duration) {
            double rest = (duration - w[5]) * w[4];
            w[0] = w[0] + w[2] * rest; w[1] = w[1] + w[3] * rest; w[5] = duration;
        } else {
            w[0] = w[0] + w[2] * step; w[1] = w[1] + w[3] * step; w[5] = end_t;
            if (step < bt) continue;
        }
        if (npath >= path_cap) overflow = true;
        else { double* q = out_path + 4 * npath++; q[0] = wi; q[1] = w[0]; q[2] = w[1]; q[3] = w[5]; }
        if (end_t >= duration) continue;
        if (bshape >= 0) {
            int sl = slot[bshape], n = res[sl], kd = rtype[sl];
            if (n > 0 && kd >= 0) { res[sl] = 0; out_gain[4 * wi + kd] += n; out_total[kd] += n; }
            out_counts[sl] += 1;
        }
        if (fabs_(bnx) >= fabs_(bny)) w[2] = -w[2]; else w[3] = -w[3];
        w[0] = w[0] + w[2] * 1e-4; w[1] = w[1] + w[3] * 1e-4;
        w[4] += SPEED_UP;
    }
    return overflow ? -1 : npath;
}
