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

inline double fabs_(double value)
{
    return value < 0 ? -value : value;
}
inline double sqrt_(double value)
{
    return _mm_cvtsd_f64(_mm_sqrt_sd(_mm_set_sd(value), _mm_set_sd(value)));
}
inline double min_(double first_value, double second_value)
{
    return second_value < first_value ? second_value : first_value;
} // 같으면 a (파이썬 min 과 같음)
inline double max_(double first_value, double second_value)
{
    return first_value < second_value ? second_value : first_value;
}

bool ray_segment(double origin_x, double origin_y, double direction_x, double direction_y, double start_x,
                 double start_y, double end_x, double end_y, double &distance, double &normal_x,
                 double &normal_y)
{
    double edge_x = end_x - start_x, edge_y = end_y - start_y;
    double denominator = direction_x * edge_y - direction_y * edge_x;
    if (fabs_(denominator) < EPS)
        return false;
    double intersection_distance =
        ((start_x - origin_x) * edge_y - (start_y - origin_y) * edge_x) / denominator;
    double segment_fraction =
        ((start_x - origin_x) * direction_y - (start_y - origin_y) * direction_x) / denominator;
    if (intersection_distance <= EPS || segment_fraction < -EPS || segment_fraction > 1 + EPS)
        return false;
    double perpendicular_x = -edge_y, perpendicular_y = edge_x;
    if (perpendicular_x * direction_x + perpendicular_y * direction_y > 0)
    {
        perpendicular_x = -perpendicular_x;
        perpendicular_y = -perpendicular_y;
    }
    distance = intersection_distance;
    normal_x = perpendicular_x;
    normal_y = perpendicular_y;
    return true;
}

bool misses_box(const double *bounds, double origin_x, double origin_y, double delta_x, double delta_y,
                double radius)
{
    double lower_bounds[2] = {bounds[0] - radius, bounds[1] - radius},
           upper_bounds[2] = {bounds[2] + radius, bounds[3] + radius};
    double origin[2] = {origin_x, origin_y}, direction[2] = {delta_x, delta_y};
    double tmin = -1e18, tmax = 1e18;
    for (int axis = 0; axis < 2; axis++)
    {
        if (fabs_(direction[axis]) < EPS)
        {
            if (origin[axis] < lower_bounds[axis] || origin[axis] > upper_bounds[axis])
                return true;
            continue;
        }
        double entry_distance = (lower_bounds[axis] - origin[axis]) / direction[axis],
               exit_distance = (upper_bounds[axis] - origin[axis]) / direction[axis];
        if (entry_distance > exit_distance)
        {
            double temporary_distance = entry_distance;
            entry_distance = exit_distance;
            exit_distance = temporary_distance;
        }
        tmin = max_(tmin, entry_distance);
        tmax = min_(tmax, exit_distance);
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

bool inside_shape(const Geo &geometry, int shape_index, double x, double y, double radius);

bool hit_shape(const Geo &geometry, int shape_index, double origin_x, double origin_y, double direction_x,
               double direction_y, double radius, double &distance, double &normal_x, double &normal_y)
{
    const double *bounds = geometry.bb + 4 * shape_index;
    if (misses_box(bounds, origin_x, origin_y, direction_x, direction_y, radius + 1e-3))
        return false;
    if (inside_shape(geometry, shape_index, origin_x, origin_y, radius))
        return false; // 게임 QueriesStartInColliders=false
    if ((geometry.kind[shape_index] & 7) == K_CIRCLE)
    {
        double center_x = geometry.circ[3 * shape_index], center_y = geometry.circ[3 * shape_index + 1];
        double expanded_radius = geometry.circ[3 * shape_index + 2] + radius;
        double offset_x = origin_x - center_x, offset_y = origin_y - center_y;
        double projection = offset_x * direction_x + offset_y * direction_y;
        double squared_offset = offset_x * offset_x + offset_y * offset_y - expanded_radius * expanded_radius;
        double discriminant = projection * projection - squared_offset;
        if (discriminant < 0)
            return false;
        double intersection_distance = -projection - sqrt_(discriminant);
        if (intersection_distance <= EPS)
            return false;
        distance = intersection_distance;
        normal_x = origin_x + direction_x * intersection_distance - center_x;
        normal_y = origin_y + direction_y * intersection_distance - center_y;
        return true;
    }
    const double *vertices = geometry.pts + 2 * geometry.pt_off[shape_index];
    int vertex_count = geometry.pt_cnt[shape_index];
    if (((geometry.kind[shape_index] & 7) == K_BOX || geometry.kind[shape_index] == K_WALL) && radius > 0)
    {
        double left_x = bounds[0], bottom_y = bounds[1], right_x = bounds[2], top_y = bounds[3];
        double edges[4][4] = {{left_x, bottom_y - radius, right_x, bottom_y - radius},
                              {right_x + radius, bottom_y, right_x + radius, top_y},
                              {right_x, top_y + radius, left_x, top_y + radius},
                              {left_x - radius, top_y, left_x - radius, bottom_y}};
        bool have = false;
        for (int next_index = 0; next_index < 4; next_index++)
        {
            double hit_distance, hit_normal_x, hit_normal_y;
            const double *boundary = edges[next_index];
            if (ray_segment(origin_x, origin_y, direction_x, direction_y, boundary[0], boundary[1],
                            boundary[2], boundary[3], hit_distance, hit_normal_x, hit_normal_y) &&
                (!have || hit_distance < distance))
            {
                have = true;
                distance = hit_distance;
                normal_x = hit_normal_x;
                normal_y = hit_normal_y;
            }
        }
        double corners[4][4] = {{left_x, bottom_y, -1, -1},
                                {right_x, bottom_y, 1, -1},
                                {right_x, top_y, 1, 1},
                                {left_x, top_y, -1, 1}};
        for (int next_index = 0; next_index < 4; next_index++)
        {
            const double *boundary = corners[next_index];
            double offset_x = origin_x - boundary[0], offset_y = origin_y - boundary[1];
            double projection = offset_x * direction_x + offset_y * direction_y,
                   discriminant = projection * projection -
                                  (offset_x * offset_x + offset_y * offset_y - radius * radius);
            if (discriminant < 0)
                continue;
            double hit_distance = -projection - sqrt_(discriminant),
                   hit_normal_x = origin_x + direction_x * hit_distance - boundary[0],
                   hit_normal_y = origin_y + direction_y * hit_distance - boundary[1];
            if (hit_distance > EPS && boundary[2] * hit_normal_x >= -EPS &&
                boundary[3] * hit_normal_y >= -EPS && (!have || hit_distance < distance))
            {
                have = true;
                distance = hit_distance;
                normal_x = hit_normal_x;
                normal_y = hit_normal_y;
            }
        }
        return have;
    }
    bool have = false;
    for (int entry_index = 0; entry_index < vertex_count - ((geometry.kind[shape_index] & 7) == 4 ? 1 : 0);
         entry_index++)
    {
        int next_index = (entry_index + 1) % vertex_count;
        double hit_distance, hit_normal_x, hit_normal_y;
        if (ray_segment(origin_x, origin_y, direction_x, direction_y, vertices[2 * entry_index],
                        vertices[2 * entry_index + 1], vertices[2 * next_index], vertices[2 * next_index + 1],
                        hit_distance, hit_normal_x, hit_normal_y) &&
            (!have || hit_distance < distance))
        {
            have = true;
            distance = hit_distance;
            normal_x = hit_normal_x;
            normal_y = hit_normal_y;
        }
    }
    return have;
}

bool inside_shape(const Geo &geometry, int shape_index, double x, double y, double radius)
{
    if ((geometry.kind[shape_index] & 7) == 4)
        return false;
    if ((geometry.kind[shape_index] & 7) == K_CIRCLE)
    {
        double delta_x = x - geometry.circ[3 * shape_index], delta_y = y - geometry.circ[3 * shape_index + 1],
               expanded_radius = geometry.circ[3 * shape_index + 2] + radius;
        return delta_x * delta_x + delta_y * delta_y < expanded_radius * expanded_radius;
    }
    if ((geometry.kind[shape_index] & 7) == K_BOX || geometry.kind[shape_index] == K_WALL)
    {
        const double *bounds = geometry.bb + 4 * shape_index;
        if (radius > 0)
        {
            double delta_x = max_(max_(bounds[0] - x, 0.), x - bounds[2]),
                   delta_y = max_(max_(bounds[1] - y, 0.), y - bounds[3]);
            return delta_x * delta_x + delta_y * delta_y <= radius * radius;
        }
        return bounds[0] - radius < x && x < bounds[2] + radius && bounds[1] - radius < y &&
               y < bounds[3] + radius;
    }
    const double *vertices = geometry.pts + 2 * geometry.pt_off[shape_index];
    bool inside = false;
    for (int vertex_index = 0; vertex_index < geometry.pt_cnt[shape_index]; vertex_index++)
    {
        int next_vertex = (vertex_index + 1) % geometry.pt_cnt[shape_index];
        double start_x = vertices[2 * vertex_index], start_y = vertices[2 * vertex_index + 1],
               end_x = vertices[2 * next_vertex], end_y = vertices[2 * next_vertex + 1];
        if ((start_y > y) != (end_y > y) &&
            x < (end_x - start_x) * (y - start_y) / (end_y - start_y) + start_x)
            inside = !inside;
    }
    return inside;
}

} // namespace

BXP_API int bxp_version()
{
    return 10;
}

// 동적 채집의 충돌 질의만 가속한다. 재생·공사·추가 작업자의 시간순 처리는 Python이 맡는다.
// 결과: 거리, 법선 x/y, 반사 여부, 픽업 여부. 반환값은 모양 번호, -1이면 교차 없음.
BXP_API int bxp_next_shape(int shape_count, const int *kind, const int *slot,
                          const int *point_offsets, const int *point_counts,
                          const double *points, const double *circles, const double *bounds,
                          const int *flags, const int *resource_types, const int *resource_stocks,
                          const double *worker, int upgrades, double ball_radius, double pickup_radius,
                          int *touching, int just_bounced, double *result)
{
    Geo geometry{kind, point_offsets, point_counts, points, circles, bounds};
    int closest = -1;
    for (int index = 0; index < shape_count; ++index)
    {
        int building = slot[index], building_flags = flags[building];
        bool wall = is_wall(kind[index]);
        bool pickup = (building_flags & F_WHEAT) != 0;
        if (!wall && !pickup && (building_flags & F_NO_RAY))
            continue;
        bool solid = wall || (!pickup && !(building_flags & F_NO_RAY) &&
            !((upgrades & U_PIERCE_BUILDINGS) && !(building_flags & F_RESOURCE)) &&
            !((building_flags & F_TILE) && resource_stocks[building] <= 0) &&
            !((building_flags & F_RESOURCE) &&
              ((resource_types[building] == 3 && (upgrades & U_PIERCE_STONE)) ||
               (resource_types[building] == 2 && (upgrades & U_PIERCE_WOOD)))));
        if (!solid && (building_flags & F_TILE) && resource_stocks[building] <= 0)
        {
            touching[building] = 0;
            continue;
        }
        double radius = pickup ? pickup_radius : ball_radius;
        bool inside = inside_shape(geometry, index, worker[0], worker[1], radius);
        double distance, normal_x, normal_y;
        if (pickup && inside)
        {
            if (touching[building] && !just_bounced)
                continue;
            distance = 0;
            normal_x = -worker[2];
            normal_y = -worker[3];
        }
        else
        {
            if (!inside)
                touching[building] = 0;
            if (!solid && inside)
                continue;
            if (!hit_shape(geometry, index, worker[0], worker[1], worker[2], worker[3],
                           radius, distance, normal_x, normal_y))
                continue;
        }
        if (closest < 0 || distance < result[0])
        {
            closest = index;
            result[0] = distance;
            result[1] = normal_x;
            result[2] = normal_y;
            result[3] = solid ? 1 : 0;
            result[4] = pickup ? 1 : 0;
        }
    }
    return closest;
}

// 여러 작업자를 시간 순서로 함께 돌린다 (harvest_sim.simulate_team 과 같음).
//   world: left, right, bottom, top, radius
//   모양 n_shapes 개: kind, slot(건물 슬롯), bid, pt_off/pt_cnt (pts 의 x,y 쌍), circ (cx, cy, r), bb (x0,
//   y0, x1, y1) 건물 슬롯: flags (F_WHEAT/F_TILE), rtype (없으면 -1), res (남은 자원 — 계산하며 바뀜, 호출한
//   쪽 사본) 작업자 n_workers 개: wk (x, y, dx, dy, speed, t — 계산하며 바뀜, 마지막 상태가 남음), ups (U_*
//   비트) 작업 공간: event_values (4 * n_workers), event_kinds (2 * n_workers)
// 결과: out_total[4], out_gain[4*n_workers], out_counts[슬롯] (부딪힌 횟수),
//       out_path: (작업자, x, y, t) 4개씩 path_cap 개까지. 돌려주는 값 = 경로 점 수 (넘치면 -1).
BXP_API int bxp_simulate_team(const double *world, int shape_count, const int *kind, const int *slot,
                              const int *building_ids, const int *point_offsets, const int *point_counts,
                              const double *points, const double *circles, const double *bounds,
                              const int *flags, const int *resource_types, int *resource_stocks,
                              int worker_count, double *worker_states, const int *upgrade_flags,
                              double duration, int max_events, int *out_total, int *out_gain, int *out_counts,
                              double *out_path, int path_capacity, double *event_values, int *event_kinds,
                              const int *build_bonus, int *out_build_points, const int *harvest_amount,
                              const int *clock_bonus, const double *pickup_radius, int *clock_counts,
                              int *out_collected, int road_count, const double *roads, double road_mult,
                              int slot_count, int *touching, int *just_bounced)
{
    const double left = world[0], right = world[1], bottom = world[2], top = world[3], ball_radius = world[4];
    Geo geometry{kind, point_offsets, point_counts, points, circles, bounds};
    int path_point_count = 0;
    bool overflow = false;
    auto path_point = [&](int index) {
        if (path_point_count >= path_capacity)
        {
            overflow = true;
            return;
        }
        double *other_worker_state = out_path + 4 * path_point_count++;
        other_worker_state[0] = index;
        other_worker_state[1] = worker_states[6 * index];
        other_worker_state[2] = worker_states[6 * index + 1];
        other_worker_state[3] = worker_states[6 * index + 5];
    };
    for (int index = 0; index < worker_count; index++)
        path_point(index);
    const double walls[4][4] = {{left + ball_radius, -1e3, left + ball_radius, 1e3},
                                {right - ball_radius, -1e3, right - ball_radius, 1e3},
                                {-1e3, top - ball_radius, 1e3, top - ball_radius},
                                {-1e3, bottom + ball_radius, 1e3, bottom + ball_radius}};
    auto moving_speed = [&](const double *worker_state) {
        double x = worker_state[0] + worker_state[2] * 1e-7, y = worker_state[1] + worker_state[3] * 1e-7;
        for (int index = 0; index < road_count; index++)
        {
            const double *box = roads + index * 4;
            if (box[0] <= x && x < box[2] && box[1] <= y && y < box[3])
                return worker_state[4] * road_mult;
        }
        return worker_state[4];
    };
    auto blocks = [&](int slot_index, int worker_index) {
        const int building_flags = flags[slot_index], worker_upgrades = upgrade_flags[worker_index];
        if ((building_flags & F_WHEAT) ||
            ((worker_upgrades & U_PIERCE_BUILDINGS) && !(building_flags & F_RESOURCE)))
            return false;
        if (building_flags & F_TILE)
        {
            if (resource_stocks[slot_index] <= 0)
                return false;
        }
        int resource_type = resource_types[slot_index];
        if ((building_flags & F_RESOURCE) && ((resource_type == 3 && (worker_upgrades & U_PIERCE_STONE)) ||
                                              (resource_type == 2 && (worker_upgrades & U_PIERCE_WOOD))))
            return false;
        return true;
    };
    // 사건 값: 시각, 거리, 법선 x/y. 종류: 충돌 모양(-2 없음/-1 벽), 반사 여부.
    auto next_event = [&](int worker_index) {
        double *worker_state = worker_states + 6 * worker_index;
        double *event = event_values + 4 * worker_index;
        int *event_kind = event_kinds + 2 * worker_index;
        event_kind[0] = -2;
        if (worker_state[5] >= duration || worker_state[4] <= 0)
            return;
        bool have = false;
        double closest_distance = 0, closest_normal_x = 0, closest_normal_y = 0;
        int bshape = -1, solid = 1;
        for (int index = 0; index < (world[5] ? 4 : 0); index++)
        {
            const double *edge = walls[index];
            bool inside = (index == 0)   ? worker_state[0] >= edge[0] - 1e-6
                          : (index == 1) ? worker_state[0] <= edge[0] + 1e-6
                          : (index == 2) ? worker_state[1] <= edge[1] + 1e-6
                                         : worker_state[1] >= edge[1] - 1e-6;
            bool toward = (index == 0)   ? worker_state[2] < 0
                          : (index == 1) ? worker_state[2] > 0
                          : (index == 2) ? worker_state[3] > 0
                                         : worker_state[3] < 0;
            double distance, normal_x, normal_y;
            if (inside && toward &&
                ray_segment(worker_state[0], worker_state[1], worker_state[2], worker_state[3], edge[0],
                            edge[1], edge[2], edge[3], distance, normal_x, normal_y) &&
                (!have || distance < closest_distance))
            {
                have = true;
                closest_distance = distance;
                closest_normal_x = normal_x;
                closest_normal_y = normal_y;
                bshape = -1;
                solid = 1;
            }
        }
        for (int index = 0; index < shape_count; index++)
        {
            if (!is_wall(kind[index]) && (flags[slot[index]] & F_NO_RAY))
                continue;
            bool blocking = is_wall(kind[index]) || blocks(slot[index], worker_index);
            bool pickup = (flags[slot[index]] & F_WHEAT) != 0;
            double pickup_radius_value = pickup ? pickup_radius[worker_index] : ball_radius;
            int contact_index = worker_index * slot_count + slot[index];
            if (!blocking && (flags[slot[index]] & F_TILE) && resource_stocks[slot[index]] <= 0)
            {
                touching[contact_index] = 0;
                continue;
            }
            bool inside =
                inside_shape(geometry, index, worker_state[0], worker_state[1], pickup_radius_value);
            double distance, normal_x, normal_y;
            bool hit = false;
            if (pickup && inside)
            {
                if (touching[contact_index] && !just_bounced[worker_index])
                    continue;
                distance = 0;
                normal_x = -worker_state[2];
                normal_y = -worker_state[3];
                hit = true;
            }
            else
            {
                if (!inside)
                    touching[contact_index] = 0;
                if (!blocking && inside)
                    continue;
                hit = hit_shape(geometry, index, worker_state[0], worker_state[1], worker_state[2],
                                worker_state[3], pickup_radius_value, distance, normal_x, normal_y);
            }
            if (hit && (!have || distance < closest_distance))
            {
                have = true;
                closest_distance = distance;
                closest_normal_x = normal_x;
                closest_normal_y = normal_y;
                bshape = index;
                solid = blocking ? 1 : 0;
            }
        }
        for (int index = 0; index < road_count; index++)
        {
            const double *road_bounds = roads + 4 * index;
            double edges[4][4] = {{road_bounds[0], road_bounds[1], road_bounds[2], road_bounds[1]},
                                  {road_bounds[2], road_bounds[1], road_bounds[2], road_bounds[3]},
                                  {road_bounds[2], road_bounds[3], road_bounds[0], road_bounds[3]},
                                  {road_bounds[0], road_bounds[3], road_bounds[0], road_bounds[1]}};
            for (int next_index = 0; next_index < 4; next_index++)
            {
                double distance, normal_x, normal_y;
                const double *edge = edges[next_index];
                if (ray_segment(worker_state[0], worker_state[1], worker_state[2], worker_state[3], edge[0],
                                edge[1], edge[2], edge[3], distance, normal_x, normal_y) &&
                    (!have || distance < closest_distance))
                {
                    have = true;
                    closest_distance = distance;
                    closest_normal_x = normal_x;
                    closest_normal_y = normal_y;
                    bshape = -3;
                    solid = 0;
                }
            }
        }
        if (!have || worker_state[5] + closest_distance / moving_speed(worker_state) >= duration)
            return;
        event[0] = worker_state[5] + closest_distance / moving_speed(worker_state);
        event[1] = closest_distance;
        event[2] = closest_normal_x;
        event[3] = closest_normal_y;
        event_kind[0] = bshape;
        event_kind[1] = solid;
    };
    for (int index = 0; index < worker_count; index++)
        next_event(index);
    for (int event_index = 0; event_index < max_events; event_index++)
    {
        int worker_index = -1;
        for (int index = 0; index < worker_count; index++)
            if (event_kinds[2 * index] != -2 &&
                (worker_index < 0 || event_values[4 * index] < event_values[4 * worker_index]))
                worker_index = index;
        if (worker_index < 0)
        {
            for (int index = 0; index < worker_count; index++)
            {
                double *other_worker_state = worker_states + 6 * index;
                if (other_worker_state[5] >= duration)
                    continue;
                double distance = (duration - other_worker_state[5]) * moving_speed(other_worker_state);
                other_worker_state[0] = other_worker_state[0] + other_worker_state[2] * distance;
                other_worker_state[1] = other_worker_state[1] + other_worker_state[3] * distance;
                other_worker_state[5] = duration;
                path_point(index);
            }
            break;
        }
        double *worker_state = worker_states + 6 * worker_index;
        const double *event = event_values + 4 * worker_index;
        double when = event[0], distance = event[1], normal_x = event[2], normal_y = event[3];
        int shape = event_kinds[2 * worker_index], solid = event_kinds[2 * worker_index + 1];
        worker_state[0] = worker_state[0] + worker_state[2] * distance;
        worker_state[1] = worker_state[1] + worker_state[3] * distance;
        worker_state[5] = when;
        bool changed = false;
        if (shape >= 0 && !is_wall(kind[shape]))
        {
            int slot_index = slot[shape], remaining_stock = resource_stocks[slot_index],
                resource_kind = resource_types[slot_index];
            if (flags[slot_index] & F_WHEAT)
            {
                touching[worker_index * slot_count + slot_index] = 1;
                just_bounced[worker_index] = 0;
            }
            changed = remaining_stock > 0;
            if (remaining_stock > 0 && resource_kind >= 0)
            {
                const int resource_index = 4 * worker_index + resource_kind;
                if (remaining_stock > harvest_amount[resource_index])
                    remaining_stock = harvest_amount[resource_index];
                resource_stocks[slot_index] -= remaining_stock;
                out_gain[4 * worker_index + resource_kind] += remaining_stock;
                out_total[resource_kind] += remaining_stock;
                out_collected[slot_index] += remaining_stock; // 반사하지 않는 관통 채집도 별도로 기록한다.
                // 게임 BaseMgr.IncreaseHarvestClock: 캐릭터·자원마다 최대 20회.
                if (clock_bonus[resource_index] > 0 && clock_counts[resource_index] < 20)
                {
                    duration += clock_bonus[resource_index] * 0.2;
                    clock_counts[resource_index]++;
                }
            }
            if (!(flags[slot_index] & F_WHEAT))
                out_counts[slot_index] += 1;
            if ((flags[slot_index] & F_BUILD) && !(flags[slot_index] & F_WHEAT))
                out_build_points[slot_index] += 1 + build_bonus[worker_index];
        }
        if (solid)
        {
            path_point(worker_index);
            const double norm2 = normal_x * normal_x + normal_y * normal_y;
            if (norm2 > 0)
            {
                double scale = 2.0 * (worker_state[2] * normal_x + worker_state[3] * normal_y) / norm2;
                double reflected_x = worker_state[2] - scale * normal_x,
                       reflected_y = worker_state[3] - scale * normal_y;
                double length = sqrt_(reflected_x * reflected_x + reflected_y * reflected_y);
                if (length > 0)
                {
                    worker_state[2] = reflected_x / length;
                    worker_state[3] = reflected_y / length;
                }
            }
            worker_state[4] = min_(MAX_SPEED, worker_state[4] + SPEED_UP);
            just_bounced[worker_index] = 1;
        }
        else if (shape == -3)
            path_point(worker_index);
        worker_state[0] = worker_state[0] + worker_state[2] * 1e-4;
        worker_state[1] = worker_state[1] + worker_state[3] * 1e-4;
        if (changed)
        {
            // 자원이 바뀐 시각까지 동료를 진행한 뒤 그 이후 사건을 새로 계산한다.
            for (int next_index = 0; next_index < worker_count; next_index++)
            {
                double *other_worker_state = worker_states + 6 * next_index;
                double *old = event_values + 4 * next_index;
                int *old_kind = event_kinds + 2 * next_index;
                int target = old_kind[0];
                bool simultaneous = next_index != worker_index && target != -2 && old[0] == when;
                bool blocking =
                    simultaneous && (target == -1 || (target >= 0 && (is_wall(kind[target]) ||
                                                                      blocks(slot[target], next_index))));
                bool keep =
                    simultaneous &&
                    (target == -3 || blocking ||
                     (target >= 0 && (!(flags[slot[target]] & F_TILE) || resource_stocks[slot[target]] > 0)));
                if (next_index != worker_index && other_worker_state[5] < when)
                {
                    double distance = (when - other_worker_state[5]) * moving_speed(other_worker_state);
                    other_worker_state[0] = other_worker_state[0] + other_worker_state[2] * distance;
                    other_worker_state[1] = other_worker_state[1] + other_worker_state[3] * distance;
                    other_worker_state[5] = when;
                }
                if (keep)
                {
                    old[1] = 0;
                    old_kind[1] = blocking ? 1 : 0;
                }
                else
                    next_event(next_index);
            }
        }
        else
            next_event(worker_index);
    }
    return overflow ? -1 : path_point_count;
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
    int grid_column_start, grid_row_start, grid_width, grid_height;
    const unsigned char *purchased_tiles; // 산 땅이면 1
    double world_origin_x, world_origin_y, cell_size;
    int square_range; // 범위 모양: 1 사각형 (게임 표시), 0 원
    // 건물 n 개
    int piece_count;
    const int *piece_widths;
    const int *piece_heights;
    const int *movable;
    const int *cell_offsets;
    const int *cell_counts;
    const int *relative_cells;  // 실제로 차지하는 칸 (dx, dy)
    const int *initial_origins; // 처음 자리 (c, r) — 옮긴 수 벌점
    // 발사대 앞 구역
    const double *lane_values;          // 칸별 값 (없으면 nullptr)
    const int *nonproductive_pieces;    // 치여도 얻는 게 없는 건물
    const double *resource_tile_values; // 자원 타일이면 자원 가중 × 용량, 아니면 0
    double lane_weight, resource_lane_weight;
    // 공략 프리셋 (금광 U자)
    int preset_spot_count;
    const int *preset_spots;
    const int *is_preset;
    double preset_weight, preset_clear_weight;
    // 효과 m 개
    int effect_count;
    const int *effect_pieces;
    const double *effect_ranges;
    const double *effect_ranges_squared;
    const int *effect_modes;
    const int *effect_groups;
    const int *effect_target_offsets;
    const int *effect_target_counts;
    const int *effect_targets;
    const double *effect_target_values;
    int harvest_limit, regeneration_group_count;
    // 담금질 '관련 자리' 후보: 건물마다 (상대 건물, 범위)
    const int *partner_offsets;
    const int *partner_counts;
    const int *partner_pieces;
    const double *partner_ranges;
    // 크기 (w, h) 별 가능한 왼쪽 아래 자리: key = w * 32 + h
    const int *size_origin_offsets;
    const int *size_origin_counts;
    const int *size_origins;
    const int *range_box_offsets;
    const int *range_box_counts;
    const double *range_boxes;
    double range_padding;
};

struct Work
{
    int *occupancy; // gw * gh, 비어 있으면 -1
    double *centers_x;
    double *centers_y;               // n
    double *regeneration;            // n_groups * n
    unsigned char *regeneration_set; // n_groups * n
    double *highest_harvest_values;
    double *second_harvest_values; // n (채집 건물 값 1·2위)
    unsigned char *harvest_set;    // n
    int *stamp;                    // n (영역 안 건물 중복 제거)
    int *temporary_piece_ids;      // 2 * n
    int *undo;                     // 3 * n (건물, 이전 c, 이전 r)
    int *best_origins;             // 2 * n
    int *candidate_origins;        // 2 * gw * gh (관련 자리 후보)
    unsigned __int64 random_state;
    int stamp_generation;
};

inline double floor_(double value)
{
    double truncated_value = (double)(__int64)value;
    return truncated_value > value ? truncated_value - 1 : truncated_value;
}

// e^x (x 가 매우 작으면 0). 받아들일 확률 계산용이라 상대 오차 1e-12 정도면 충분
double exp_(double exponent)
{
    if (exponent < -700)
        return 0.0;
    if (exponent > 700)
        exponent = 700;
    const double LN2 = 0.6931471805599453;
    double binary_exponent = floor_(exponent / LN2 + 0.5);
    double remainder = exponent - binary_exponent * LN2;
    double term = 1, sum = 1;
    for (int index = 1; index < 18; index++)
    {
        term *= remainder / index;
        sum += term;
    }
    __int64 integer_exponent = (__int64)binary_exponent;
    union {
        double d;
        unsigned __int64 u;
    } power_of_two;
    power_of_two.u = (unsigned __int64)(integer_exponent + 1023) << 52;
    return sum * power_of_two.d;
}

inline double rnd(Work &workspace)
{ // [0, 1)
    workspace.random_state ^= workspace.random_state >> 12;
    workspace.random_state ^= workspace.random_state << 25;
    workspace.random_state ^= workspace.random_state >> 27;
    unsigned __int64 random_bits = workspace.random_state * 2685821657736338717ULL;
    return (double)(random_bits >> 11) * (1.0 / 9007199254740992.0);
}
inline int rint_(Work &workspace, int upper_limit)
{
    int sample = (int)(rnd(workspace) * upper_limit);
    return sample < upper_limit ? sample : upper_limit - 1;
}

inline bool in_grid(const Model &model, int column, int row)
{
    return column >= model.grid_column_start && row >= model.grid_row_start &&
           column < model.grid_column_start + model.grid_width &&
           row < model.grid_row_start + model.grid_height;
}
inline bool is_tile(const Model &model, int column, int row)
{
    return in_grid(model, column, row) &&
           model.purchased_tiles[(row - model.grid_row_start) * model.grid_width +
                                 (column - model.grid_column_start)];
}
inline int &occ_at(const Model &model, Work &workspace, int column, int row)
{
    return workspace
        .occupancy[(row - model.grid_row_start) * model.grid_width + (column - model.grid_column_start)];
}

inline bool in_range(const Model &model, double delta_x, double delta_y, double radius, double radius_squared)
{
    if (model.square_range)
        return fabs_(delta_x) <= radius + 1e-6 && fabs_(delta_y) <= radius + 1e-6;
    return delta_x * delta_x + delta_y * delta_y <= radius_squared + 1e-6;
}

inline bool target_in_range(const Model &model, int target, double delta_x, double delta_y, double radius)
{
    if (model.range_box_counts[target] < 0)
        return in_range(model, delta_x, delta_y, radius, radius * radius);
    double radius_without_padding = radius - model.range_padding;
    for (int entry_index = 0; entry_index < model.range_box_counts[target]; entry_index++)
    {
        const double *target_bounds = model.range_boxes + 4 * (model.range_box_offsets[target] + entry_index);
        if (delta_x + target_bounds[0] <= radius_without_padding &&
            delta_x + target_bounds[2] >= -radius_without_padding &&
            delta_y + target_bounds[1] <= radius_without_padding &&
            delta_y + target_bounds[3] >= -radius_without_padding)
            return true;
    }
    return false;
}

inline double center_x(const Model &model, int origin_column, int width)
{
    return model.world_origin_x + (origin_column + width / 2.0) * model.cell_size;
}
inline double center_y(const Model &model, int origin_row, int height)
{
    return model.world_origin_y + (origin_row + height / 2.0) * model.cell_size;
}

inline int cell_c(const Model &model, const int *origins, int index, int cell_index)
{
    return origins[2 * index] + model.relative_cells[2 * (model.cell_offsets[index] + cell_index)];
}
inline int cell_r(const Model &model, const int *origins, int index, int cell_index)
{
    return origins[2 * index + 1] + model.relative_cells[2 * (model.cell_offsets[index] + cell_index) + 1];
}

void build_occ(const Model &model, Work &workspace, const int *origins)
{
    for (int entry_index = 0; entry_index < model.grid_width * model.grid_height; entry_index++)
        workspace.occupancy[entry_index] = -1;
    for (int index = 0; index < model.piece_count; index++)
        for (int cell_index = 0; cell_index < model.cell_counts[index]; cell_index++)
        {
            int column = cell_c(model, origins, index, cell_index),
                row = cell_r(model, origins, index, cell_index);
            if (in_grid(model, column, row))
                occ_at(model, workspace, column, row) = index;
        }
}

// Scorer.score (범위 효과 + 발사대 앞 구역 + 프리셋)
double score(const Model &model, Work &workspace, const int *origins)
{
    for (int index = 0; index < model.piece_count; index++)
    {
        workspace.centers_x[index] = center_x(model, origins[2 * index], model.piece_widths[index]);
        workspace.centers_y[index] = center_y(model, origins[2 * index + 1], model.piece_heights[index]);
        workspace.harvest_set[index] = 0;
    }
    for (int entry_index = 0; entry_index < model.regeneration_group_count * model.piece_count; entry_index++)
        workspace.regeneration_set[entry_index] = 0;
    double total = 0;
    for (int effect_index = 0; effect_index < model.effect_count; effect_index++)
    {
        int effect_piece = model.effect_pieces[effect_index], mode = model.effect_modes[effect_index];
        double effect_x = workspace.centers_x[effect_piece], effect_y = workspace.centers_y[effect_piece],
               expanded_radius = model.effect_ranges[effect_index],
               radius_squared = model.effect_ranges_squared[effect_index];
        int count = 0;
        for (int entry_index = model.effect_target_offsets[effect_index];
             entry_index <
             model.effect_target_offsets[effect_index] + model.effect_target_counts[effect_index];
             entry_index++)
        {
            int target_index = model.effect_targets[entry_index];
            if (target_index == effect_piece)
                continue;
            if (!target_in_range(model, target_index, workspace.centers_x[target_index] - effect_x,
                                 workspace.centers_y[target_index] - effect_y, expanded_radius))
                continue;
            count++;
            if (mode == M_HARVEST && count > model.harvest_limit)
                continue;
            double target_value = model.effect_target_values[entry_index];
            if (mode == M_REGEN)
            {
                int group_index = model.effect_groups[effect_index] * model.piece_count + target_index;
                if (!workspace.regeneration_set[group_index] ||
                    target_value > workspace.regeneration[group_index])
                {
                    workspace.regeneration[group_index] = target_value;
                    workspace.regeneration_set[group_index] = 1;
                }
            }
            else if (mode == M_HARVEST)
            {
                if (!workspace.harvest_set[target_index])
                {
                    workspace.highest_harvest_values[target_index] = target_value;
                    workspace.second_harvest_values[target_index] = 0;
                    workspace.harvest_set[target_index] = 1;
                }
                else if (target_value > workspace.highest_harvest_values[target_index])
                {
                    workspace.second_harvest_values[target_index] =
                        workspace.highest_harvest_values[target_index];
                    workspace.highest_harvest_values[target_index] = target_value;
                }
                else if (target_value > workspace.second_harvest_values[target_index])
                    workspace.second_harvest_values[target_index] = target_value;
            }
            else
            {
                total += target_value;
            }
        }
    }
    for (int entry_index = 0; entry_index < model.regeneration_group_count * model.piece_count; entry_index++)
        if (workspace.regeneration_set[entry_index])
            total += workspace.regeneration[entry_index];
    for (int index = 0; index < model.piece_count; index++)
        if (workspace.harvest_set[index])
            total += workspace.highest_harvest_values[index] + 0.5 * workspace.second_harvest_values[index];
    if (model.lane_values)
    {
        double blocked = 0, front = 0;
        for (int index = 0; index < model.piece_count; index++)
        {
            bool idle = model.nonproductive_pieces[index] != 0,
                 tile_ = model.resource_tile_values[index] > 0 && !workspace.harvest_set[index];
            if (!idle && !tile_)
                continue;
            for (int cell_index = 0; cell_index < model.cell_counts[index]; cell_index++)
            {
                int column = cell_c(model, origins, index, cell_index),
                    row = cell_r(model, origins, index, cell_index);
                if (!in_grid(model, column, row))
                    continue;
                double lane_value = model.lane_values[(row - model.grid_row_start) * model.grid_width +
                                                      (column - model.grid_column_start)];
                if (idle)
                    blocked += lane_value;
                if (tile_)
                    front += lane_value * model.resource_tile_values[index];
            }
        }
        total -= model.lane_weight * blocked;
        total += model.resource_lane_weight * front;
    }
    if (model.preset_spot_count)
    {
        int filled = 0;
        for (int spot_index = 0; spot_index < model.preset_spot_count; spot_index++)
        {
            int shift_column = model.preset_spots[2 * spot_index],
                shift_row = model.preset_spots[2 * spot_index + 1];
            bool blocked = false;
            for (int index = 0; index < model.piece_count && !blocked; index++)
                if (model.is_preset[index] && origins[2 * index] == shift_column &&
                    origins[2 * index + 1] == shift_row)
                    blocked = true;
            if (blocked)
            {
                filled++;
                continue;
            }
            int occd = 0;
            for (int delta_x = 0; delta_x < 2; delta_x++)
                for (int delta_y = 0; delta_y < 2; delta_y++)
                    if (in_grid(model, shift_column + delta_x, shift_row + delta_y) &&
                        occ_at(model, workspace, shift_column + delta_x, shift_row + delta_y) >= 0)
                        occd++;
            total -= model.preset_clear_weight * occd;
        }
        total += model.preset_weight * filled;
    }
    return total;
}

double objective(const Model &model, Work &workspace, const int *origins, double cost)
{
    int moved = 0;
    for (int index = 0; index < model.piece_count; index++)
        if (origins[2 * index] != model.initial_origins[2 * index] ||
            origins[2 * index + 1] != model.initial_origins[2 * index + 1])
            moved++;
    return score(model, workspace, origins) - cost * moved;
}

void lift(const Model &model, Work &workspace, const int *origins, int index)
{
    for (int cell_index = 0; cell_index < model.cell_counts[index]; cell_index++)
    {
        int column = cell_c(model, origins, index, cell_index),
            row = cell_r(model, origins, index, cell_index);
        if (in_grid(model, column, row) && occ_at(model, workspace, column, row) == index)
            occ_at(model, workspace, column, row) = -1;
    }
}

void place(const Model &model, Work &workspace, const int *origins, int index)
{
    for (int cell_index = 0; cell_index < model.cell_counts[index]; cell_index++)
    {
        int column = cell_c(model, origins, index, cell_index),
            row = cell_r(model, origins, index, cell_index);
        if (in_grid(model, column, row))
            occ_at(model, workspace, column, row) = index;
    }
}

// 같은 크기 두 영역의 내용 맞바꾸기 (Layout.swap_regions). 성공하면 되돌리기 항목 수, 아니면 -1
int swap_regions(const Model &model, Work &workspace, int *origins, int first_column, int first_row,
                 int second_column, int second_row, int width, int height)
{
    if ((first_column - second_column < width && second_column - first_column < width) &&
        (first_row - second_row < height && second_row - first_row < height))
        return -1; // 겹치는 영역
    int first_count = 0, second_count = 0;
    int *ids = workspace.temporary_piece_ids;
    for (int side = 0; side < 2; side++)
    {
        int origin_column = side ? second_column : first_column, origin_row = side ? second_row : first_row;
        int generation = ++workspace.stamp_generation;
        int start = side ? first_count : 0, count = 0;
        for (int delta_x = 0; delta_x < width; delta_x++)
            for (int delta_y = 0; delta_y < height; delta_y++)
            {
                if (!in_grid(model, origin_column + delta_x, origin_row + delta_y))
                    continue;
                int index = occ_at(model, workspace, origin_column + delta_x, origin_row + delta_y);
                if (index < 0 || workspace.stamp[index] == generation)
                    continue;
                workspace.stamp[index] = generation;
                int original_column = origins[2 * index], original_row = origins[2 * index + 1];
                if (!model.movable[index] || original_column < origin_column || original_row < origin_row ||
                    original_column + model.piece_widths[index] > origin_column + width ||
                    original_row + model.piece_heights[index] > origin_row + height)
                    return -1;
                ids[start + count++] = index;
            }
        if (side)
            second_count = count;
        else
            first_count = count;
    }
    if (first_count + second_count == 0)
        return -1;
    for (int entry_index = 0; entry_index < first_count + second_count; entry_index++)
    { // 새 자리가 모두 산 땅 안인가
        int index = ids[entry_index];
        int shift_column =
                entry_index < first_count ? second_column - first_column : first_column - second_column,
            shift_row = entry_index < first_count ? second_row - first_row : first_row - second_row;
        for (int cell_index = 0; cell_index < model.cell_counts[index]; cell_index++)
            if (!is_tile(model, cell_c(model, origins, index, cell_index) + shift_column,
                         cell_r(model, origins, index, cell_index) + shift_row))
                return -1;
    }
    for (int entry_index = 0; entry_index < first_count + second_count; entry_index++)
    { // Layout.apply: 모두 지운 뒤 다시 놓는다
        int index = ids[entry_index];
        workspace.undo[3 * entry_index] = index;
        workspace.undo[3 * entry_index + 1] = origins[2 * index];
        workspace.undo[3 * entry_index + 2] = origins[2 * index + 1];
        lift(model, workspace, origins, index);
    }
    for (int entry_index = 0; entry_index < first_count + second_count; entry_index++)
    {
        int index = ids[entry_index];
        origins[2 * index] +=
            entry_index < first_count ? second_column - first_column : first_column - second_column;
        origins[2 * index + 1] += entry_index < first_count ? second_row - first_row : first_row - second_row;
        place(model, workspace, origins, index);
    }
    return first_count + second_count;
}

void undo_swap(const Model &model, Work &workspace, int *origins, int undo_count)
{
    for (int entry_index = 0; entry_index < undo_count; entry_index++)
        lift(model, workspace, origins, workspace.undo[3 * entry_index]);
    for (int entry_index = 0; entry_index < undo_count; entry_index++)
    {
        int index = workspace.undo[3 * entry_index];
        origins[2 * index] = workspace.undo[3 * entry_index + 1];
        origins[2 * index + 1] = workspace.undo[3 * entry_index + 2];
        place(model, workspace, origins, index);
    }
}

inline int size_key(int width, int height)
{
    return (width < 32 && height < 32) ? width * 32 + height : -1;
}

} // namespace

BXP_API unsigned __int64 bxp_ticks()
{
    return __rdtsc();
}

BXP_API double bxp_layout_score(const Model *model, Work *workspace, const int *origins)
{
    build_occ(*model, *workspace, origins);
    return score(*model, *workspace, origins);
}

// 담금질 (layout_opt.anneal). org: 시작 배치 → 가장 좋았던 배치. 돌려주는 값: 그 배치의 목표값(벌점 포함)
BXP_API double bxp_layout_anneal(const Model *model_pointer, Work *workspace_pointer, int *origins,
                                 double cost, double initial_temperature, double log_temperature_ratio,
                                 unsigned __int64 ticks, unsigned __int64 seed, int *output_iterations)
{
    const Model &model = *model_pointer;
    Work &workspace = *workspace_pointer;
    workspace.random_state = seed ? seed : 0x9E3779B97F4A7C15ULL;
    workspace.stamp_generation = 0;
    for (int index = 0; index < model.piece_count; index++)
        workspace.stamp[index] = 0;
    build_occ(model, workspace, origins);
    int nmov = 0;
    for (int index = 0; index < model.piece_count; index++)
        if (model.movable[index])
            nmov++;
    double current_score = objective(model, workspace, origins, cost);
    double best = current_score;
    for (int entry_index = 0; entry_index < 2 * model.piece_count; entry_index++)
        workspace.best_origins[entry_index] = origins[entry_index];
    if (!nmov)
    {
        *output_iterations = 0;
        return best;
    }
    unsigned __int64 start = __rdtsc();
    double temp = initial_temperature;
    int iteration = 0;
    for (;;)
    {
        iteration++;
        if (iteration % 64 == 0)
        {
            double elapsed_seconds = (double)(__rdtsc() - start) / (double)ticks;
            if (elapsed_seconds >= 1)
                break;
            temp = initial_temperature * exp_(log_temperature_ratio * elapsed_seconds);
        }
        int pick = rint_(workspace, nmov), index = -1;
        for (int entry_index = 0; entry_index < model.piece_count; entry_index++)
            if (model.movable[entry_index] && pick-- == 0)
            {
                index = entry_index;
                break;
            }
        int width = model.piece_widths[index], height = model.piece_heights[index];
        if (rnd(workspace) < 0.25)
        {
            width += rint_(workspace, 3);
            height += rint_(workspace, 3);
        } // 가끔 더 큰 묶음 영역
        int key = size_key(width, height);
        if (key < 0 || model.size_origin_counts[key] <= 0)
            continue;
        const int *cand = model.size_origins + 2 * model.size_origin_offsets[key];
        int candidate_count = model.size_origin_counts[key];
        int first_column = origins[2 * index], first_row = origins[2 * index + 1];
        if (width != model.piece_widths[index] || height != model.piece_heights[index])
        {
            first_column -= rint_(workspace, width - model.piece_widths[index] + 1);
            first_row -= rint_(workspace, height - model.piece_heights[index] + 1);
            bool ok = true;
            for (int delta_x = 0; delta_x < width && ok; delta_x++)
                for (int delta_y = 0; delta_y < height && ok; delta_y++)
                    ok = is_tile(model, first_column + delta_x, first_row + delta_y);
            if (!ok)
                continue;
        }
        int second_column, second_row;
        if (rnd(workspace) < 0.6)
        {
            // 관련 자리 (_near_spots): 프리셋 자리 → 상대 건물의 범위 안 → 아무 자리
            int nearby_count = 0;
            if (model.preset_spot_count && model.is_preset[index])
            {
                for (int cell_index = 0; cell_index < candidate_count; cell_index++)
                    for (int candidate_score = 0; candidate_score < model.preset_spot_count;
                         candidate_score++)
                        if (cand[2 * cell_index] == model.preset_spots[2 * candidate_score] &&
                            cand[2 * cell_index + 1] == model.preset_spots[2 * candidate_score + 1])
                        {
                            workspace.candidate_origins[2 * nearby_count] = cand[2 * cell_index];
                            workspace.candidate_origins[2 * nearby_count + 1] = cand[2 * cell_index + 1];
                            nearby_count++;
                        }
            }
            if (!nearby_count && model.partner_counts[index] > 0)
            {
                int partner_index =
                    model.partner_offsets[index] + rint_(workspace, model.partner_counts[index]);
                int partner_piece = model.partner_pieces[partner_index];
                double partner_x =
                    center_x(model, origins[2 * partner_piece], model.piece_widths[partner_piece]);
                double partner_y =
                    center_y(model, origins[2 * partner_piece + 1], model.piece_heights[partner_piece]);
                double partner_radius = model.partner_ranges[partner_index];
                for (int cell_index = 0; cell_index < candidate_count; cell_index++)
                    if (in_range(model, center_x(model, cand[2 * cell_index], width) - partner_x,
                                 center_y(model, cand[2 * cell_index + 1], height) - partner_y,
                                 partner_radius, partner_radius * partner_radius))
                    {
                        workspace.candidate_origins[2 * nearby_count] = cand[2 * cell_index];
                        workspace.candidate_origins[2 * nearby_count + 1] = cand[2 * cell_index + 1];
                        nearby_count++;
                    }
            }
            if (nearby_count)
            {
                int cell_index = rint_(workspace, nearby_count);
                second_column = workspace.candidate_origins[2 * cell_index];
                second_row = workspace.candidate_origins[2 * cell_index + 1];
            }
            else
            {
                int cell_index = rint_(workspace, candidate_count);
                second_column = cand[2 * cell_index];
                second_row = cand[2 * cell_index + 1];
            }
        }
        else
        {
            int cell_index = rint_(workspace, candidate_count);
            second_column = cand[2 * cell_index];
            second_row = cand[2 * cell_index + 1];
        }
        int undo_count = swap_regions(model, workspace, origins, first_column, first_row, second_column,
                                      second_row, width, height);
        if (undo_count < 0)
            continue;
        double candidate_score = objective(model, workspace, origins, cost);
        double score_delta = candidate_score - current_score;
        if (score_delta >= 0 || rnd(workspace) < exp_(score_delta / (temp > 1e-6 ? temp : 1e-6)))
        {
            current_score = candidate_score;
            if (candidate_score > best + 1e-9)
            {
                best = candidate_score;
                for (int entry_index = 0; entry_index < 2 * model.piece_count; entry_index++)
                    workspace.best_origins[entry_index] = origins[entry_index];
            }
        }
        else
        {
            undo_swap(model, workspace, origins, undo_count);
        }
    }
    for (int entry_index = 0; entry_index < 2 * model.piece_count; entry_index++)
        origins[entry_index] = workspace.best_origins[entry_index];
    *output_iterations = iteration;
    return best;
}

// 마무리 (layout_opt.polish): 건물마다 같은 크기의 모든 자리와 맞바꿔 보고 가장 좋은 것을 받아들인다
BXP_API double bxp_layout_polish(const Model *model_pointer, Work *workspace_pointer, int *origins,
                                 double cost, unsigned __int64 ticks)
{
    const Model &model = *model_pointer;
    Work &workspace = *workspace_pointer;
    workspace.stamp_generation = 0;
    for (int index = 0; index < model.piece_count; index++)
        workspace.stamp[index] = 0;
    build_occ(model, workspace, origins);
    double current_score = objective(model, workspace, origins, cost);
    unsigned __int64 start = __rdtsc();
    bool improved = true;
    while (improved && __rdtsc() - start < ticks)
    {
        improved = false;
        for (int index = 0; index < model.piece_count; index++)
        {
            if (!model.movable[index])
                continue;
            int key = size_key(model.piece_widths[index], model.piece_heights[index]);
            if (key < 0 || model.size_origin_counts[key] <= 0)
                continue;
            const int *cand = model.size_origins + 2 * model.size_origin_offsets[key];
            double best_score = current_score;
            int best_candidate_index = -1;
            for (int cell_index = 0; cell_index < model.size_origin_counts[key]; cell_index++)
            {
                int undo_count =
                    swap_regions(model, workspace, origins, origins[2 * index], origins[2 * index + 1],
                                 cand[2 * cell_index], cand[2 * cell_index + 1], model.piece_widths[index],
                                 model.piece_heights[index]);
                if (undo_count < 0)
                    continue;
                double candidate_score = objective(model, workspace, origins, cost);
                if (candidate_score > best_score + 1e-9)
                {
                    best_score = candidate_score;
                    best_candidate_index = cell_index;
                }
                undo_swap(model, workspace, origins, undo_count);
            }
            if (best_candidate_index >= 0)
            {
                swap_regions(model, workspace, origins, origins[2 * index], origins[2 * index + 1],
                             cand[2 * best_candidate_index], cand[2 * best_candidate_index + 1],
                             model.piece_widths[index], model.piece_heights[index]);
                current_score = best_score;
                improved = true;
            }
        }
    }
    return current_score;
}
