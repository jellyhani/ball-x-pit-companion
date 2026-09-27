// BALL x PIT 도우미 — 계산 전용 네이티브 모듈 (채집 궤적 · 배치 최적화).
//
// src/engine/harvest_sim.py 의 simulate_team 과 같은 계산을 같은 순서·같은 부동소수 연산으로 한다.
// 파이썬 쪽(src/engine/native.py)이 ctypes 로 부르고, 결과가 파이썬 구현과 같은지 tests/test_native.py 가
// 확인한다. 게임과는 관련 없는 순수 계산 (입력은 플러그인이 보낸 기지 모양·건물 값).
//
// Windows x64용이다. C 런타임·표준 라이브러리를 쓰지 않아 별도 CRT 설치에 의존하지 않는다.
// 메모리는 모두 호출하는 쪽이 넘겨준다. 제곱근은 SSE2 sqrtsd (IEEE 정확 반올림 — 파이썬 math.sqrt 와 같음).
// 입력/출력 배열의 길이·배치는 Python 래퍼와 ABI 계약이다. 배열을 재정렬하면 양쪽과 ABI를 함께 바꾼다.
//
// 빌드: native\build.ps1 (Visual Studio C++ 도구) → src\engine\bxp_native.dll
// emmintrin.h 는 C 런타임 헤더를 끌어오므로, 쓰는 SSE2 내장 함수만 컴파일러 선언 그대로 적는다.
typedef struct __declspec(intrin_type) __declspec(align(16)) __m128d
{
    double m128d_f64[2];
} __m128d;
extern "C"
{
    extern __m128d _mm_sqrt_sd(__m128d, __m128d);
    extern __m128d _mm_set_sd(double);
    extern double _mm_cvtsd_f64(__m128d);
}
#pragma intrinsic(_mm_sqrt_sd, _mm_set_sd, _mm_cvtsd_f64)

#define BXP_API extern "C" __declspec(dllexport)

extern "C" int _fltused = 0; // 부동소수를 쓰는 코드에 링커가 요구하는 기호 (CRT 없이 빌드할 때)

namespace
{

const double EPS = 1e-6;
const double SPEED_UP = 0.2;
const double MAX_SPEED = 100.0;

enum
{
    K_CIRCLE = 0,
    K_BOX = 1,
    K_POLY = 2,
    K_WALL = 3
}; // 미개방 구역은 관통되지 않는 벽
inline bool is_wall(int kind)
{
    return kind == K_WALL || (kind & 8);
}
enum
{
    F_WHEAT = 1,
    F_TILE = 2,
    F_BUILD = 4,
    F_RESOURCE = 8,
    F_NO_RAY = 16
}; // 자원 종류와 질의 계층을 구분
enum
{
    U_PIERCE_BUILDINGS = 1,
    U_PIERCE_STONE = 2,
    U_PIERCE_WOOD = 4
}; // 작업자 채집 강화

inline double fabs_(double x)
{
    return x < 0 ? -x : x;
}
inline double sqrt_(double x)
{
    return _mm_cvtsd_f64(_mm_sqrt_sd(_mm_set_sd(x), _mm_set_sd(x)));
}
inline double min_(double a, double b)
{
    return b < a ? b : a;
} // 같으면 a (파이썬 min 과 같음)
inline double max_(double a, double b)
{
    return a < b ? b : a;
}

bool ray_segment(double ox, double oy, double dx, double dy, double ax, double ay, double bx, double by,
                 double &t, double &nx, double &ny)
{
    double ex = bx - ax, ey = by - ay;
    double den = dx * ey - dy * ex;
    if (fabs_(den) < EPS)
        return false;
    double tt = ((ax - ox) * ey - (ay - oy) * ex) / den;
    double u = ((ax - ox) * dy - (ay - oy) * dx) / den;
    if (tt <= EPS || u < -EPS || u > 1 + EPS)
        return false;
    double n1 = -ey, n2 = ex;
    if (n1 * dx + n2 * dy > 0)
    {
        n1 = -n1;
        n2 = -n2;
    }
    t = tt;
    nx = n1;
    ny = n2;
    return true;
}

bool misses_box(const double *bb, double ox, double oy, double dx, double dy, double r)
{
    double lo[2] = {bb[0] - r, bb[1] - r}, hi[2] = {bb[2] + r, bb[3] + r};
    double o[2] = {ox, oy}, d[2] = {dx, dy};
    double tmin = -1e18, tmax = 1e18;
    for (int k = 0; k < 2; k++)
    {
        if (fabs_(d[k]) < EPS)
        {
            if (o[k] < lo[k] || o[k] > hi[k])
                return true;
            continue;
        }
        double t1 = (lo[k] - o[k]) / d[k], t2 = (hi[k] - o[k]) / d[k];
        if (t1 > t2)
        {
            double s = t1;
            t1 = t2;
            t2 = s;
        }
        tmin = max_(tmin, t1);
        tmax = min_(tmax, t2);
        if (tmin > tmax)
            return true;
    }
    return tmax <= EPS;
}

struct Geo
{
    const int *kind;
    const int *pt_off;
    const int *pt_cnt;
    const double *pts;
    const double *circ;
    const double *bb; // 경계 상자 4개씩 (파이썬이 계산해 넘김 — Shape.bb 와 같은 값)
};

bool inside_shape(const Geo &g, int i, double x, double y, double r);

bool hit_shape(const Geo &g, int i, double ox, double oy, double dx, double dy, double r, double &t,
               double &nx, double &ny)
{
    const double *bb = g.bb + 4 * i;
    if (misses_box(bb, ox, oy, dx, dy, r + 1e-3))
        return false;
    if (inside_shape(g, i, ox, oy, r))
        return false; // 게임 QueriesStartInColliders=false
    if ((g.kind[i] & 7) == K_CIRCLE)
    {
        double cx = g.circ[3 * i], cy = g.circ[3 * i + 1];
        double R = g.circ[3 * i + 2] + r;
        double fx = ox - cx, fy = oy - cy;
        double b = fx * dx + fy * dy;
        double c = fx * fx + fy * fy - R * R;
        double disc = b * b - c;
        if (disc < 0)
            return false;
        double tt = -b - sqrt_(disc);
        if (tt <= EPS)
            return false;
        t = tt;
        nx = ox + dx * tt - cx;
        ny = oy + dy * tt - cy;
        return true;
    }
    const double *p = g.pts + 2 * g.pt_off[i];
    int n = g.pt_cnt[i];
    if (((g.kind[i] & 7) == K_BOX || g.kind[i] == K_WALL) && r > 0)
    {
        double x0 = bb[0], y0 = bb[1], x1 = bb[2], y1 = bb[3];
        double edges[4][4] = {{x0, y0 - r, x1, y0 - r},
                              {x1 + r, y0, x1 + r, y1},
                              {x1, y1 + r, x0, y1 + r},
                              {x0 - r, y1, x0 - r, y0}};
        bool have = false;
        for (int j = 0; j < 4; j++)
        {
            double ht, hx, hy;
            const double *a = edges[j];
            if (ray_segment(ox, oy, dx, dy, a[0], a[1], a[2], a[3], ht, hx, hy) && (!have || ht < t))
            {
                have = true;
                t = ht;
                nx = hx;
                ny = hy;
            }
        }
        double corners[4][4] = {{x0, y0, -1, -1}, {x1, y0, 1, -1}, {x1, y1, 1, 1}, {x0, y1, -1, 1}};
        for (int j = 0; j < 4; j++)
        {
            const double *a = corners[j];
            double fx = ox - a[0], fy = oy - a[1];
            double b = fx * dx + fy * dy, disc = b * b - (fx * fx + fy * fy - r * r);
            if (disc < 0)
                continue;
            double ht = -b - sqrt_(disc), hx = ox + dx * ht - a[0], hy = oy + dy * ht - a[1];
            if (ht > EPS && a[2] * hx >= -EPS && a[3] * hy >= -EPS && (!have || ht < t))
            {
                have = true;
                t = ht;
                nx = hx;
                ny = hy;
            }
        }
        return have;
    }
    bool have = false;
    for (int k = 0; k < n - ((g.kind[i] & 7) == 4 ? 1 : 0); k++)
    {
        int j = (k + 1) % n;
        double ht, hx, hy;
        if (ray_segment(ox, oy, dx, dy, p[2 * k], p[2 * k + 1], p[2 * j], p[2 * j + 1], ht, hx, hy) &&
            (!have || ht < t))
        {
            have = true;
            t = ht;
            nx = hx;
            ny = hy;
        }
    }
    return have;
}

bool inside_shape(const Geo &g, int i, double x, double y, double r)
{
    if ((g.kind[i] & 7) == 4)
        return false;
    if ((g.kind[i] & 7) == K_CIRCLE)
    {
        double dx = x - g.circ[3 * i], dy = y - g.circ[3 * i + 1], rr = g.circ[3 * i + 2] + r;
        return dx * dx + dy * dy < rr * rr;
    }
    if ((g.kind[i] & 7) == K_BOX || g.kind[i] == K_WALL)
    {
        const double *bb = g.bb + 4 * i;
        if (r > 0)
        {
            double dx = max_(max_(bb[0] - x, 0.), x - bb[2]), dy = max_(max_(bb[1] - y, 0.), y - bb[3]);
            return dx * dx + dy * dy <= r * r;
        }
        return bb[0] - r < x && x < bb[2] + r && bb[1] - r < y && y < bb[3] + r;
    }
    const double *p = g.pts + 2 * g.pt_off[i];
    bool inside = false;
    for (int k = 0; k < g.pt_cnt[i]; k++)
    {
        int j = (k + 1) % g.pt_cnt[i];
        double ax = p[2 * k], ay = p[2 * k + 1], bx = p[2 * j], by = p[2 * j + 1];
        if ((ay > y) != (by > y) && x < (bx - ax) * (y - ay) / (by - ay) + ax)
            inside = !inside;
    }
    return inside;
}

} // namespace

BXP_API int bxp_version()
{
    return 9;
}

// 여러 작업자를 시간 순서로 함께 돌린다 (harvest_sim.simulate_team 과 같음).
//   world: left, right, bottom, top, radius
//   모양 n_shapes 개: kind, slot(건물 슬롯), bid, pt_off/pt_cnt (pts 의 x,y 쌍), circ (cx, cy, r), bb (x0,
//   y0, x1, y1) 건물 슬롯: flags (F_WHEAT/F_TILE), rtype (없으면 -1), res (남은 자원 — 계산하며 바뀜, 호출한
//   쪽 사본) 작업자 n_workers 개: wk (x, y, dx, dy, speed, t — 계산하며 바뀜, 마지막 상태가 남음), ups (U_*
//   비트) 작업 공간: event_values (4 * n_workers), event_kinds (2 * n_workers)
// 결과: out_total[4], out_gain[4*n_workers], out_counts[슬롯] (부딪힌 횟수),
//       out_path: (작업자, x, y, t) 4개씩 path_cap 개까지. 돌려주는 값 = 경로 점 수 (넘치면 -1).
BXP_API int bxp_simulate_team(const double *world, int n_shapes, const int *kind, const int *slot,
                              const int *bid, const int *pt_off, const int *pt_cnt, const double *pts,
                              const double *circ, const double *bb, const int *flags, const int *rtype,
                              int *res, int n_workers, double *wk, const int *ups, double duration,
                              int max_events, int *out_total, int *out_gain, int *out_counts,
                              double *out_path, int path_cap, double *event_values, int *event_kinds,
                              const int *build_bonus, int *out_build_points, const int *harvest_amount,
                              const int *clock_bonus, const double *pickup_radius, int *clock_counts,
                              int *out_collected, int n_roads, const double *roads, double road_mult,
                              int n_slots, int *touching, int *just_bounced)
{
    const double left = world[0], right = world[1], bottom = world[2], top = world[3], r = world[4];
    Geo g{kind, pt_off, pt_cnt, pts, circ, bb};
    int npath = 0;
    bool overflow = false;
    auto path_point = [&](int i) {
        if (npath >= path_cap)
        {
            overflow = true;
            return;
        }
        double *q = out_path + 4 * npath++;
        q[0] = i;
        q[1] = wk[6 * i];
        q[2] = wk[6 * i + 1];
        q[3] = wk[6 * i + 5];
    };
    for (int i = 0; i < n_workers; i++)
        path_point(i);
    const double walls[4][4] = {{left + r, -1e3, left + r, 1e3},
                                {right - r, -1e3, right - r, 1e3},
                                {-1e3, top - r, 1e3, top - r},
                                {-1e3, bottom + r, 1e3, bottom + r}};
    auto moving_speed = [&](const double *w) {
        double x = w[0] + w[2] * 1e-7, y = w[1] + w[3] * 1e-7;
        for (int i = 0; i < n_roads; i++)
        {
            const double *box = roads + i * 4;
            if (box[0] <= x && x < box[2] && box[1] <= y && y < box[3])
                return w[4] * road_mult;
        }
        return w[4];
    };
    auto blocks = [&](int sl, int wi) {
        const int f = flags[sl], wu = ups[wi];
        if ((f & F_WHEAT) || ((wu & U_PIERCE_BUILDINGS) && !(f & F_RESOURCE)))
            return false;
        if (f & F_TILE)
        {
            if (res[sl] <= 0)
                return false;
        }
        int k = rtype[sl];
        if ((f & F_RESOURCE) && ((k == 3 && (wu & U_PIERCE_STONE)) || (k == 2 && (wu & U_PIERCE_WOOD))))
            return false;
        return true;
    };
    // 사건 값: 시각, 거리, 법선 x/y. 종류: 충돌 모양(-2 없음/-1 벽), 반사 여부.
    auto next_event = [&](int wi) {
        double *w = wk + 6 * wi;
        double *e = event_values + 4 * wi;
        int *ek = event_kinds + 2 * wi;
        ek[0] = -2;
        if (w[5] >= duration || w[4] <= 0)
            return;
        bool have = false;
        double bt = 0, bnx = 0, bny = 0;
        int bshape = -1, solid = 1;
        for (int i = 0; i < (world[5] ? 4 : 0); i++)
        {
            const double *a = walls[i];
            bool inside = (i == 0)   ? w[0] >= a[0] - 1e-6
                          : (i == 1) ? w[0] <= a[0] + 1e-6
                          : (i == 2) ? w[1] <= a[1] + 1e-6
                                     : w[1] >= a[1] - 1e-6;
            bool toward = (i == 0) ? w[2] < 0 : (i == 1) ? w[2] > 0 : (i == 2) ? w[3] > 0 : w[3] < 0;
            double t, nx, ny;
            if (inside && toward && ray_segment(w[0], w[1], w[2], w[3], a[0], a[1], a[2], a[3], t, nx, ny) &&
                (!have || t < bt))
            {
                have = true;
                bt = t;
                bnx = nx;
                bny = ny;
                bshape = -1;
                solid = 1;
            }
        }
        for (int i = 0; i < n_shapes; i++)
        {
            if (!is_wall(kind[i]) && (flags[slot[i]] & F_NO_RAY))
                continue;
            bool blocking = is_wall(kind[i]) || blocks(slot[i], wi);
            bool pickup = (flags[slot[i]] & F_WHEAT) != 0;
            double pickup_r = pickup ? pickup_radius[wi] : r;
            int ci = wi * n_slots + slot[i];
            if (!blocking && (flags[slot[i]] & F_TILE) && res[slot[i]] <= 0)
            {
                touching[ci] = 0;
                continue;
            }
            bool inside = inside_shape(g, i, w[0], w[1], pickup_r);
            double t, nx, ny;
            bool hit = false;
            if (pickup && inside)
            {
                if (touching[ci] && !just_bounced[wi])
                    continue;
                t = 0;
                nx = -w[2];
                ny = -w[3];
                hit = true;
            }
            else
            {
                if (!inside)
                    touching[ci] = 0;
                if (!blocking && inside)
                    continue;
                hit = hit_shape(g, i, w[0], w[1], w[2], w[3], pickup_r, t, nx, ny);
            }
            if (hit && (!have || t < bt))
            {
                have = true;
                bt = t;
                bnx = nx;
                bny = ny;
                bshape = i;
                solid = blocking ? 1 : 0;
            }
        }
        for (int i = 0; i < n_roads; i++)
        {
            const double *b = roads + 4 * i;
            double edges[4][4] = {{b[0], b[1], b[2], b[1]},
                                  {b[2], b[1], b[2], b[3]},
                                  {b[2], b[3], b[0], b[3]},
                                  {b[0], b[3], b[0], b[1]}};
            for (int j = 0; j < 4; j++)
            {
                double t, nx, ny;
                const double *a = edges[j];
                if (ray_segment(w[0], w[1], w[2], w[3], a[0], a[1], a[2], a[3], t, nx, ny) &&
                    (!have || t < bt))
                {
                    have = true;
                    bt = t;
                    bnx = nx;
                    bny = ny;
                    bshape = -3;
                    solid = 0;
                }
            }
        }
        if (!have || w[5] + bt / moving_speed(w) >= duration)
            return;
        e[0] = w[5] + bt / moving_speed(w);
        e[1] = bt;
        e[2] = bnx;
        e[3] = bny;
        ek[0] = bshape;
        ek[1] = solid;
    };
    for (int i = 0; i < n_workers; i++)
        next_event(i);
    for (int ev = 0; ev < max_events; ev++)
    {
        int wi = -1;
        for (int i = 0; i < n_workers; i++)
            if (event_kinds[2 * i] != -2 && (wi < 0 || event_values[4 * i] < event_values[4 * wi]))
                wi = i;
        if (wi < 0)
        {
            for (int i = 0; i < n_workers; i++)
            {
                double *q = wk + 6 * i;
                if (q[5] >= duration)
                    continue;
                double distance = (duration - q[5]) * moving_speed(q);
                q[0] = q[0] + q[2] * distance;
                q[1] = q[1] + q[3] * distance;
                q[5] = duration;
                path_point(i);
            }
            break;
        }
        double *w = wk + 6 * wi;
        const double *e = event_values + 4 * wi;
        double when = e[0], distance = e[1], nx = e[2], ny = e[3];
        int shape = event_kinds[2 * wi], solid = event_kinds[2 * wi + 1];
        w[0] = w[0] + w[2] * distance;
        w[1] = w[1] + w[3] * distance;
        w[5] = when;
        bool changed = false;
        if (shape >= 0 && !is_wall(kind[shape]))
        {
            int sl = slot[shape], n = res[sl], kd = rtype[sl];
            if (flags[sl] & F_WHEAT)
            {
                touching[wi * n_slots + sl] = 1;
                just_bounced[wi] = 0;
            }
            changed = n > 0;
            if (n > 0 && kd >= 0)
            {
                const int idx = 4 * wi + kd;
                if (n > harvest_amount[idx])
                    n = harvest_amount[idx];
                res[sl] -= n;
                out_gain[4 * wi + kd] += n;
                out_total[kd] += n;
                out_collected[sl] += n; // 반사하지 않는 관통 채집도 별도로 기록한다.
                // 게임 BaseMgr.IncreaseHarvestClock: 캐릭터·자원마다 최대 20회.
                if (clock_bonus[idx] > 0 && clock_counts[idx] < 20)
                {
                    duration += clock_bonus[idx] * 0.2;
                    clock_counts[idx]++;
                }
            }
            if (!(flags[sl] & F_WHEAT))
                out_counts[sl] += 1;
            if ((flags[sl] & F_BUILD) && !(flags[sl] & F_WHEAT))
                out_build_points[sl] += 1 + build_bonus[wi];
        }
        if (solid)
        {
            path_point(wi);
            const double norm2 = nx * nx + ny * ny;
            if (norm2 > 0)
            {
                double scale = 2.0 * (w[2] * nx + w[3] * ny) / norm2;
                double rx = w[2] - scale * nx, ry = w[3] - scale * ny;
                double len = sqrt_(rx * rx + ry * ry);
                if (len > 0)
                {
                    w[2] = rx / len;
                    w[3] = ry / len;
                }
            }
            w[4] = min_(MAX_SPEED, w[4] + SPEED_UP);
            just_bounced[wi] = 1;
        }
        else if (shape == -3)
            path_point(wi);
        w[0] = w[0] + w[2] * 1e-4;
        w[1] = w[1] + w[3] * 1e-4;
        if (changed)
        {
            // 자원이 바뀐 시각까지 동료를 진행한 뒤 그 이후 사건을 새로 계산한다.
            for (int j = 0; j < n_workers; j++)
            {
                double *q = wk + 6 * j;
                double *old = event_values + 4 * j;
                int *old_kind = event_kinds + 2 * j;
                int target = old_kind[0];
                bool simultaneous = j != wi && target != -2 && old[0] == when;
                bool blocking =
                    simultaneous &&
                    (target == -1 || (target >= 0 && (is_wall(kind[target]) || blocks(slot[target], j))));
                bool keep = simultaneous &&
                            (target == -3 || blocking ||
                             (target >= 0 && (!(flags[slot[target]] & F_TILE) || res[slot[target]] > 0)));
                if (j != wi && q[5] < when)
                {
                    double d = (when - q[5]) * moving_speed(q);
                    q[0] = q[0] + q[2] * d;
                    q[1] = q[1] + q[3] * d;
                    q[5] = when;
                }
                if (keep)
                {
                    old[1] = 0;
                    old_kind[1] = blocking ? 1 : 0;
                }
                else
                    next_event(j);
            }
        }
        else
            next_event(wi);
    }
    return overflow ? -1 : npath;
}

// ================================================================================================
// 배치 최적화 (src/engine/layout_opt.py 의 Scorer.score · anneal · polish 와 같은 규칙)
//
// 파이썬이 점수표를 배열로 만들어 넘긴다 (효과마다 대상과 대상별 값 — 가중치·효율·자원 가중·용량을 곱한 값).
// 점수 계산은 파이썬과 같은 값(합 순서만 달라 1e-9 안쪽), 담금질은 난수가 달라 결과 배치는 다르다 —
// tests/test_native.py 가 점수가 같은지, 같은 시간에 찾은 배치가 파이썬보다 나쁘지 않은지 확인한다.
// ================================================================================================
extern "C" unsigned __int64 __rdtsc(void);
#pragma intrinsic(__rdtsc)

namespace
{

enum
{
    M_COUNT = 0,
    M_REGEN = 1,
    M_HARVEST = 2
};

struct Model
{
    // 격자: 타일 (c, r) → 칸 (r - gy0) * gw + (c - gx0)
    int gx0, gy0, gw, gh;
    const unsigned char *tile; // 산 땅이면 1
    double ox, oy, size;
    int square; // 범위 모양: 1 사각형 (게임 표시), 0 원
    // 건물 n 개
    int n;
    const int *pw;
    const int *ph;
    const int *movable;
    const int *rel_off;
    const int *rel_cnt;
    const int *rel;     // 실제로 차지하는 칸 (dx, dy)
    const int *origin0; // 처음 자리 (c, r) — 옮긴 수 벌점
    // 발사대 앞 구역
    const double *lane;      // 칸별 값 (없으면 nullptr)
    const int *lane_idle;    // 치여도 얻는 게 없는 건물
    const double *lane_tile; // 자원 타일이면 자원 가중 × 용량, 아니면 0
    double lane_w, lane_tile_w;
    // 공략 프리셋 (금광 U자)
    int n_spots;
    const int *spots;
    const int *is_preset;
    double preset_w, preset_clear;
    // 효과 m 개
    int m;
    const int *eff_piece;
    const double *eff_rr;
    const double *eff_r2;
    const int *eff_mode;
    const int *eff_group;
    const int *eff_off;
    const int *eff_cnt;
    const int *tgt;
    const double *tgt_val;
    int harvest_cap, n_groups;
    // 담금질 '관련 자리' 후보: 건물마다 (상대 건물, 범위)
    const int *part_off;
    const int *part_cnt;
    const int *part_piece;
    const double *part_r;
    // 크기 (w, h) 별 가능한 왼쪽 아래 자리: key = w * 32 + h
    const int *sz_off;
    const int *sz_cnt;
    const int *sz_org;
    const int *range_off;
    const int *range_cnt;
    const double *range_boxes;
    double range_pad;
};

struct Work
{
    int *occ; // gw * gh, 비어 있으면 -1
    double *cx;
    double *cy;               // n
    double *regen;            // n_groups * n
    unsigned char *regen_set; // n_groups * n
    double *h1;
    double *h2;           // n (채집 건물 값 1·2위)
    unsigned char *h_set; // n
    int *stamp;           // n (영역 안 건물 중복 제거)
    int *tmp_ids;         // 2 * n
    int *undo;            // 3 * n (건물, 이전 c, 이전 r)
    int *best_org;        // 2 * n
    int *cand;            // 2 * gw * gh (관련 자리 후보)
    unsigned __int64 rng;
    int stamp_gen;
};

inline double floor_(double x)
{
    double t = (double)(__int64)x;
    return t > x ? t - 1 : t;
}

// e^x (x 가 매우 작으면 0). 받아들일 확률 계산용이라 상대 오차 1e-12 정도면 충분
double exp_(double x)
{
    if (x < -700)
        return 0.0;
    if (x > 700)
        x = 700;
    const double LN2 = 0.6931471805599453;
    double k = floor_(x / LN2 + 0.5);
    double r = x - k * LN2;
    double term = 1, sum = 1;
    for (int i = 1; i < 18; i++)
    {
        term *= r / i;
        sum += term;
    }
    __int64 ki = (__int64)k;
    union {
        double d;
        unsigned __int64 u;
    } v;
    v.u = (unsigned __int64)(ki + 1023) << 52;
    return sum * v.d;
}

inline double rnd(Work &W)
{ // [0, 1)
    W.rng ^= W.rng >> 12;
    W.rng ^= W.rng << 25;
    W.rng ^= W.rng >> 27;
    unsigned __int64 x = W.rng * 2685821657736338717ULL;
    return (double)(x >> 11) * (1.0 / 9007199254740992.0);
}
inline int rint_(Work &W, int n)
{
    int k = (int)(rnd(W) * n);
    return k < n ? k : n - 1;
}

inline bool in_grid(const Model &M, int c, int r)
{
    return c >= M.gx0 && r >= M.gy0 && c < M.gx0 + M.gw && r < M.gy0 + M.gh;
}
inline bool is_tile(const Model &M, int c, int r)
{
    return in_grid(M, c, r) && M.tile[(r - M.gy0) * M.gw + (c - M.gx0)];
}
inline int &occ_at(const Model &M, Work &W, int c, int r)
{
    return W.occ[(r - M.gy0) * M.gw + (c - M.gx0)];
}

inline bool in_range(const Model &M, double dx, double dy, double r, double r2)
{
    if (M.square)
        return fabs_(dx) <= r + 1e-6 && fabs_(dy) <= r + 1e-6;
    return dx * dx + dy * dy <= r2 + 1e-6;
}

inline bool target_in_range(const Model &M, int target, double dx, double dy, double radius)
{
    if (M.range_cnt[target] < 0)
        return in_range(M, dx, dy, radius, radius * radius);
    double r = radius - M.range_pad;
    for (int k = 0; k < M.range_cnt[target]; k++)
    {
        const double *b = M.range_boxes + 4 * (M.range_off[target] + k);
        if (dx + b[0] <= r && dx + b[2] >= -r && dy + b[1] <= r && dy + b[3] >= -r)
            return true;
    }
    return false;
}

inline double center_x(const Model &M, int c0, int w)
{
    return M.ox + (c0 + w / 2.0) * M.size;
}
inline double center_y(const Model &M, int r0, int h)
{
    return M.oy + (r0 + h / 2.0) * M.size;
}

inline int cell_c(const Model &M, const int *org, int i, int q)
{
    return org[2 * i] + M.rel[2 * (M.rel_off[i] + q)];
}
inline int cell_r(const Model &M, const int *org, int i, int q)
{
    return org[2 * i + 1] + M.rel[2 * (M.rel_off[i] + q) + 1];
}

void build_occ(const Model &M, Work &W, const int *org)
{
    for (int k = 0; k < M.gw * M.gh; k++)
        W.occ[k] = -1;
    for (int i = 0; i < M.n; i++)
        for (int q = 0; q < M.rel_cnt[i]; q++)
        {
            int c = cell_c(M, org, i, q), r = cell_r(M, org, i, q);
            if (in_grid(M, c, r))
                occ_at(M, W, c, r) = i;
        }
}

// Scorer.score (범위 효과 + 발사대 앞 구역 + 프리셋)
double score(const Model &M, Work &W, const int *org)
{
    for (int i = 0; i < M.n; i++)
    {
        W.cx[i] = center_x(M, org[2 * i], M.pw[i]);
        W.cy[i] = center_y(M, org[2 * i + 1], M.ph[i]);
        W.h_set[i] = 0;
    }
    for (int k = 0; k < M.n_groups * M.n; k++)
        W.regen_set[k] = 0;
    double total = 0;
    for (int e = 0; e < M.m; e++)
    {
        int p = M.eff_piece[e], mode = M.eff_mode[e];
        double ex = W.cx[p], ey = W.cy[p], rr = M.eff_rr[e], r2 = M.eff_r2[e];
        int cnt = 0;
        for (int k = M.eff_off[e]; k < M.eff_off[e] + M.eff_cnt[e]; k++)
        {
            int t = M.tgt[k];
            if (t == p)
                continue;
            if (!target_in_range(M, t, W.cx[t] - ex, W.cy[t] - ey, rr))
                continue;
            cnt++;
            if (mode == M_HARVEST && cnt > M.harvest_cap)
                continue;
            double v = M.tgt_val[k];
            if (mode == M_REGEN)
            {
                int g = M.eff_group[e] * M.n + t;
                if (!W.regen_set[g] || v > W.regen[g])
                {
                    W.regen[g] = v;
                    W.regen_set[g] = 1;
                }
            }
            else if (mode == M_HARVEST)
            {
                if (!W.h_set[t])
                {
                    W.h1[t] = v;
                    W.h2[t] = 0;
                    W.h_set[t] = 1;
                }
                else if (v > W.h1[t])
                {
                    W.h2[t] = W.h1[t];
                    W.h1[t] = v;
                }
                else if (v > W.h2[t])
                    W.h2[t] = v;
            }
            else
            {
                total += v;
            }
        }
    }
    for (int k = 0; k < M.n_groups * M.n; k++)
        if (W.regen_set[k])
            total += W.regen[k];
    for (int i = 0; i < M.n; i++)
        if (W.h_set[i])
            total += W.h1[i] + 0.5 * W.h2[i];
    if (M.lane)
    {
        double blocked = 0, front = 0;
        for (int i = 0; i < M.n; i++)
        {
            bool idle = M.lane_idle[i] != 0, tile_ = M.lane_tile[i] > 0 && !W.h_set[i];
            if (!idle && !tile_)
                continue;
            for (int q = 0; q < M.rel_cnt[i]; q++)
            {
                int c = cell_c(M, org, i, q), r = cell_r(M, org, i, q);
                if (!in_grid(M, c, r))
                    continue;
                double lv = M.lane[(r - M.gy0) * M.gw + (c - M.gx0)];
                if (idle)
                    blocked += lv;
                if (tile_)
                    front += lv * M.lane_tile[i];
            }
        }
        total -= M.lane_w * blocked;
        total += M.lane_tile_w * front;
    }
    if (M.n_spots)
    {
        int filled = 0;
        for (int s = 0; s < M.n_spots; s++)
        {
            int sc = M.spots[2 * s], sr = M.spots[2 * s + 1];
            bool f = false;
            for (int i = 0; i < M.n && !f; i++)
                if (M.is_preset[i] && org[2 * i] == sc && org[2 * i + 1] == sr)
                    f = true;
            if (f)
            {
                filled++;
                continue;
            }
            int occd = 0;
            for (int dx = 0; dx < 2; dx++)
                for (int dy = 0; dy < 2; dy++)
                    if (in_grid(M, sc + dx, sr + dy) && occ_at(M, W, sc + dx, sr + dy) >= 0)
                        occd++;
            total -= M.preset_clear * occd;
        }
        total += M.preset_w * filled;
    }
    return total;
}

double objective(const Model &M, Work &W, const int *org, double cost)
{
    int moved = 0;
    for (int i = 0; i < M.n; i++)
        if (org[2 * i] != M.origin0[2 * i] || org[2 * i + 1] != M.origin0[2 * i + 1])
            moved++;
    return score(M, W, org) - cost * moved;
}

void lift(const Model &M, Work &W, const int *org, int i)
{
    for (int q = 0; q < M.rel_cnt[i]; q++)
    {
        int c = cell_c(M, org, i, q), r = cell_r(M, org, i, q);
        if (in_grid(M, c, r) && occ_at(M, W, c, r) == i)
            occ_at(M, W, c, r) = -1;
    }
}

void place(const Model &M, Work &W, const int *org, int i)
{
    for (int q = 0; q < M.rel_cnt[i]; q++)
    {
        int c = cell_c(M, org, i, q), r = cell_r(M, org, i, q);
        if (in_grid(M, c, r))
            occ_at(M, W, c, r) = i;
    }
}

// 같은 크기 두 영역의 내용 맞바꾸기 (Layout.swap_regions). 성공하면 되돌리기 항목 수, 아니면 -1
int swap_regions(const Model &M, Work &W, int *org, int ac, int ar, int bc, int br, int w, int h)
{
    if ((ac - bc < w && bc - ac < w) && (ar - br < h && br - ar < h))
        return -1; // 겹치는 영역
    int na = 0, nb = 0;
    int *ids = W.tmp_ids;
    for (int side = 0; side < 2; side++)
    {
        int c0 = side ? bc : ac, r0 = side ? br : ar;
        int gen = ++W.stamp_gen;
        int start = side ? na : 0, cnt = 0;
        for (int dx = 0; dx < w; dx++)
            for (int dy = 0; dy < h; dy++)
            {
                if (!in_grid(M, c0 + dx, r0 + dy))
                    continue;
                int i = occ_at(M, W, c0 + dx, r0 + dy);
                if (i < 0 || W.stamp[i] == gen)
                    continue;
                W.stamp[i] = gen;
                int oc = org[2 * i], orr = org[2 * i + 1];
                if (!M.movable[i] || oc < c0 || orr < r0 || oc + M.pw[i] > c0 + w || orr + M.ph[i] > r0 + h)
                    return -1;
                ids[start + cnt++] = i;
            }
        if (side)
            nb = cnt;
        else
            na = cnt;
    }
    if (na + nb == 0)
        return -1;
    for (int k = 0; k < na + nb; k++)
    { // 새 자리가 모두 산 땅 안인가
        int i = ids[k];
        int sc = k < na ? bc - ac : ac - bc, sr = k < na ? br - ar : ar - br;
        for (int q = 0; q < M.rel_cnt[i]; q++)
            if (!is_tile(M, cell_c(M, org, i, q) + sc, cell_r(M, org, i, q) + sr))
                return -1;
    }
    for (int k = 0; k < na + nb; k++)
    { // Layout.apply: 모두 지운 뒤 다시 놓는다
        int i = ids[k];
        W.undo[3 * k] = i;
        W.undo[3 * k + 1] = org[2 * i];
        W.undo[3 * k + 2] = org[2 * i + 1];
        lift(M, W, org, i);
    }
    for (int k = 0; k < na + nb; k++)
    {
        int i = ids[k];
        org[2 * i] += k < na ? bc - ac : ac - bc;
        org[2 * i + 1] += k < na ? br - ar : ar - br;
        place(M, W, org, i);
    }
    return na + nb;
}

void undo_swap(const Model &M, Work &W, int *org, int nu)
{
    for (int k = 0; k < nu; k++)
        lift(M, W, org, W.undo[3 * k]);
    for (int k = 0; k < nu; k++)
    {
        int i = W.undo[3 * k];
        org[2 * i] = W.undo[3 * k + 1];
        org[2 * i + 1] = W.undo[3 * k + 2];
        place(M, W, org, i);
    }
}

inline int size_key(int w, int h)
{
    return (w < 32 && h < 32) ? w * 32 + h : -1;
}

} // namespace

BXP_API unsigned __int64 bxp_ticks()
{
    return __rdtsc();
}

BXP_API double bxp_layout_score(const Model *M, Work *W, const int *org)
{
    build_occ(*M, *W, org);
    return score(*M, *W, org);
}

// 담금질 (layout_opt.anneal). org: 시작 배치 → 가장 좋았던 배치. 돌려주는 값: 그 배치의 목표값(벌점 포함)
BXP_API double bxp_layout_anneal(const Model *Mp, Work *Wp, int *org, double cost, double t0, double ln_ratio,
                                 unsigned __int64 ticks, unsigned __int64 seed, int *out_iters)
{
    const Model &M = *Mp;
    Work &W = *Wp;
    W.rng = seed ? seed : 0x9E3779B97F4A7C15ULL;
    W.stamp_gen = 0;
    for (int i = 0; i < M.n; i++)
        W.stamp[i] = 0;
    build_occ(M, W, org);
    int nmov = 0;
    for (int i = 0; i < M.n; i++)
        if (M.movable[i])
            nmov++;
    double cur = objective(M, W, org, cost);
    double best = cur;
    for (int k = 0; k < 2 * M.n; k++)
        W.best_org[k] = org[k];
    if (!nmov)
    {
        *out_iters = 0;
        return best;
    }
    unsigned __int64 start = __rdtsc();
    double temp = t0;
    int it = 0;
    for (;;)
    {
        it++;
        if (it % 64 == 0)
        {
            double el = (double)(__rdtsc() - start) / (double)ticks;
            if (el >= 1)
                break;
            temp = t0 * exp_(ln_ratio * el);
        }
        int pick = rint_(W, nmov), i = -1;
        for (int k = 0; k < M.n; k++)
            if (M.movable[k] && pick-- == 0)
            {
                i = k;
                break;
            }
        int w = M.pw[i], h = M.ph[i];
        if (rnd(W) < 0.25)
        {
            w += rint_(W, 3);
            h += rint_(W, 3);
        } // 가끔 더 큰 묶음 영역
        int key = size_key(w, h);
        if (key < 0 || M.sz_cnt[key] <= 0)
            continue;
        const int *cand = M.sz_org + 2 * M.sz_off[key];
        int ncand = M.sz_cnt[key];
        int ac = org[2 * i], ar = org[2 * i + 1];
        if (w != M.pw[i] || h != M.ph[i])
        {
            ac -= rint_(W, w - M.pw[i] + 1);
            ar -= rint_(W, h - M.ph[i] + 1);
            bool ok = true;
            for (int dx = 0; dx < w && ok; dx++)
                for (int dy = 0; dy < h && ok; dy++)
                    ok = is_tile(M, ac + dx, ar + dy);
            if (!ok)
                continue;
        }
        int bc, br;
        if (rnd(W) < 0.6)
        {
            // 관련 자리 (_near_spots): 프리셋 자리 → 상대 건물의 범위 안 → 아무 자리
            int nc = 0;
            if (M.n_spots && M.is_preset[i])
            {
                for (int q = 0; q < ncand; q++)
                    for (int s = 0; s < M.n_spots; s++)
                        if (cand[2 * q] == M.spots[2 * s] && cand[2 * q + 1] == M.spots[2 * s + 1])
                        {
                            W.cand[2 * nc] = cand[2 * q];
                            W.cand[2 * nc + 1] = cand[2 * q + 1];
                            nc++;
                        }
            }
            if (!nc && M.part_cnt[i] > 0)
            {
                int pk = M.part_off[i] + rint_(W, M.part_cnt[i]);
                int pp = M.part_piece[pk];
                double px = center_x(M, org[2 * pp], M.pw[pp]);
                double py = center_y(M, org[2 * pp + 1], M.ph[pp]);
                double rr = M.part_r[pk];
                for (int q = 0; q < ncand; q++)
                    if (in_range(M, center_x(M, cand[2 * q], w) - px, center_y(M, cand[2 * q + 1], h) - py,
                                 rr, rr * rr))
                    {
                        W.cand[2 * nc] = cand[2 * q];
                        W.cand[2 * nc + 1] = cand[2 * q + 1];
                        nc++;
                    }
            }
            if (nc)
            {
                int q = rint_(W, nc);
                bc = W.cand[2 * q];
                br = W.cand[2 * q + 1];
            }
            else
            {
                int q = rint_(W, ncand);
                bc = cand[2 * q];
                br = cand[2 * q + 1];
            }
        }
        else
        {
            int q = rint_(W, ncand);
            bc = cand[2 * q];
            br = cand[2 * q + 1];
        }
        int nu = swap_regions(M, W, org, ac, ar, bc, br, w, h);
        if (nu < 0)
            continue;
        double s = objective(M, W, org, cost);
        double d = s - cur;
        if (d >= 0 || rnd(W) < exp_(d / (temp > 1e-6 ? temp : 1e-6)))
        {
            cur = s;
            if (s > best + 1e-9)
            {
                best = s;
                for (int k = 0; k < 2 * M.n; k++)
                    W.best_org[k] = org[k];
            }
        }
        else
        {
            undo_swap(M, W, org, nu);
        }
    }
    for (int k = 0; k < 2 * M.n; k++)
        org[k] = W.best_org[k];
    *out_iters = it;
    return best;
}

// 마무리 (layout_opt.polish): 건물마다 같은 크기의 모든 자리와 맞바꿔 보고 가장 좋은 것을 받아들인다
BXP_API double bxp_layout_polish(const Model *Mp, Work *Wp, int *org, double cost, unsigned __int64 ticks)
{
    const Model &M = *Mp;
    Work &W = *Wp;
    W.stamp_gen = 0;
    for (int i = 0; i < M.n; i++)
        W.stamp[i] = 0;
    build_occ(M, W, org);
    double cur = objective(M, W, org, cost);
    unsigned __int64 start = __rdtsc();
    bool improved = true;
    while (improved && __rdtsc() - start < ticks)
    {
        improved = false;
        for (int i = 0; i < M.n; i++)
        {
            if (!M.movable[i])
                continue;
            int key = size_key(M.pw[i], M.ph[i]);
            if (key < 0 || M.sz_cnt[key] <= 0)
                continue;
            const int *cand = M.sz_org + 2 * M.sz_off[key];
            double bv = cur;
            int bq = -1;
            for (int q = 0; q < M.sz_cnt[key]; q++)
            {
                int nu = swap_regions(M, W, org, org[2 * i], org[2 * i + 1], cand[2 * q], cand[2 * q + 1],
                                      M.pw[i], M.ph[i]);
                if (nu < 0)
                    continue;
                double v = objective(M, W, org, cost);
                if (v > bv + 1e-9)
                {
                    bv = v;
                    bq = q;
                }
                undo_swap(M, W, org, nu);
            }
            if (bq >= 0)
            {
                swap_regions(M, W, org, org[2 * i], org[2 * i + 1], cand[2 * bq], cand[2 * bq + 1], M.pw[i],
                             M.ph[i]);
                cur = bv;
                improved = true;
            }
        }
    }
    return cur;
}
