// 게임의 물리·효과 상태를 읽기만 한다. 게임 값이나 세이브는 변경하지 않는다.
using System;
using System.IO;
using System.Text;
using System.Text.Json;
using UnityEngine;

namespace BallxPitBridge
{
    internal static class PhysicsSnapshot
    {
        // 환경 모양은 매 스냅샷 전부 탐색하지 않는다. 상태가 바뀌거나 1초가 지나면 갱신하고,
        // 시간·참가자·건물의 변하는 값은 별도로 읽는다. 캐시는 게임 객체를 변경하지 않는다.
        static string _environment;
        static float _environmentAt = -10;
        static string _environmentState;

        internal static void Write(Utf8JsonWriter writer, BaseGridMgr gridManager, BaseMgr baseManager, MetaSaveData saveData)
        {
            try
            {
                string state = baseManager.CurState.ToString();
                if (_environment == null || _environmentState != state || Time.realtimeSinceStartup - _environmentAt >= 1f)
                {
                    using var memoryStream = new MemoryStream();
                    using (var environmentWriter = new Utf8JsonWriter(memoryStream))
                    {
                        environmentWriter.WriteStartObject();
                        environmentWriter.WriteBoolean("queries_start_in_colliders", Physics2D.queriesStartInColliders);
                        environmentWriter.WriteBoolean("queries_hit_triggers", Physics2D.queriesHitTriggers);
                        environmentWriter.WriteStartArray("walls");
                        var colliders = UnityEngine.Object.FindObjectsOfType<Collider2D>();
                        int mask = ColMgr.kLayerMaskAllBaseObstacles;
                        if (colliders != null) foreach (var collider in colliders)
                        {
                            if (collider == null || !collider.enabled || !collider.gameObject.activeInHierarchy || collider.isTrigger) continue;
                            if ((mask & (1 << collider.gameObject.layer)) == 0) continue;
                            if (gridManager.BuildingColDict != null && gridManager.BuildingColDict.ContainsKey(collider)) continue;
                            WriteCollider(environmentWriter, collider);
                        }
                        environmentWriter.WriteEndArray();
                        environmentWriter.WriteStartArray("roads");
                        var chunks = saveData.BaseChunks;
                        if (chunks != null && BuildingMgr.I != null)
                            for (int x = 0; x < chunks.Length; x++)
                                if (chunks[x] != null) for (int y = 0; y < chunks[x].Length; y++)
                                {
                                    var chunk = chunks[x][y];
                                    if (chunk == null || !chunk.IsPurchased) continue;
                                    var corner = BaseGridMgr.GetChunkBotLeft(chunk.X, chunk.Y);
                                    for (int columnIndex = 0; columnIndex < BaseGridMgr.kChunkWidth; columnIndex++)
                                        for (int rowIndex = 0; rowIndex < BaseGridMgr.kChunkHeight; rowIndex++)
                                        {
                                            float logicalX = corner.x + columnIndex * BaseGridMgr.kSpaceWidth;
                                            float logicalY = corner.y + rowIndex * BaseGridMgr.kSpaceHeight;
                                            if (BuildingMgr.I.GetTile(logicalX + BaseGridMgr.kSpaceWidth / 2,
                                                                      logicalY + BaseGridMgr.kSpaceHeight / 2) != BaseTileType.kStoneRoad) continue;
                                            environmentWriter.WriteStartArray();
                                            environmentWriter.WriteNumberValue(logicalX); environmentWriter.WriteNumberValue(logicalY);
                                            environmentWriter.WriteNumberValue(logicalX + BaseGridMgr.kSpaceWidth);
                                            environmentWriter.WriteNumberValue(logicalY + BaseGridMgr.kSpaceHeight);
                                            environmentWriter.WriteEndArray();
                                        }
                                }
                        environmentWriter.WriteEndArray();
                        var road = InfoDB.I.Buildings[(int)BuildingType.kStoneRoad];
                        environmentWriter.WriteNumber("road_speed_mult", 1.0 + road.GetStatBonusAmt(0) / 100.0);
                        environmentWriter.WriteEndObject();
                    }
                    _environment = Encoding.UTF8.GetString(memoryStream.ToArray());
                    _environmentAt = Time.realtimeSinceStartup;
                    _environmentState = state;
                }
                writer.WritePropertyName("environment"); writer.WriteRawValue(_environment, true);
                writer.WriteNumber("physics_contract", 2);
                writer.WriteNumber("environment_age", Math.Round(Time.realtimeSinceStartup - _environmentAt, 3));
            }
            catch (Exception error) { writer.WriteString("physics_error", error.GetType().Name); }
            try
            {
                if (TimeMgr.I != null)
                {
                    writer.WriteNumber("game_time", TimeMgr.I.GetTime());
                    writer.WriteNumber("physics_time", TimeMgr.I.GetPhysicsTime());
                    writer.WriteNumber("game_speed", TimeMgr.I.GetGameSpeed());
                    writer.WriteNumber("physics_step", TimeMgr.I.GetFixedDeltaTime());
                }
                if (WorldTimeMgr.I != null)
                {
                    writer.WriteNumber("world_tick_progress", WorldTimeMgr.I._timeProgress);
                    writer.WriteNumber("world_tick_interval", WorldTimeMgr.I.GetTimeThreshold());
                }
            }
            catch { }
            try
            {
                using var teamBuffer = new MemoryStream();
                using (var teamWriter = new Utf8JsonWriter(teamBuffer))
                {
                    teamWriter.WriteStartArray();
                    var workers = baseManager.ActiveWorkers;
                    if (workers != null) for (int index = 0; index < workers.Count; index++)
                    {
                        var character = workers[index];
                        if (character == null) continue;
                        teamWriter.WriteStartObject();
                        teamWriter.WriteString("type", character.Type.ToString());
                        teamWriter.WriteNumber("launch_index", index);
                        teamWriter.WriteStartObject("upgrades");
                        foreach (HarvestUpgradeType type in Enum.GetValues(typeof(HarvestUpgradeType)))
                            if (type != HarvestUpgradeType.kNum) teamWriter.WriteNumber(type.ToString(), character.GetHarvestUpgradeLvl(type));
                        teamWriter.WriteEndObject();
                        teamWriter.WriteStartObject("harvest_bonus");
                        foreach (HarvestUpgradeType type in Enum.GetValues(typeof(HarvestUpgradeType)))
                            if (type != HarvestUpgradeType.kNum) teamWriter.WriteNumber(type.ToString(), character.GetHarvestUpgradeBonusAmt(type));
                        teamWriter.WriteEndObject();
                        teamWriter.WriteEndObject();
                    }
                    teamWriter.WriteEndArray();
                }
                writer.WritePropertyName("launch_team"); writer.WriteRawValue(Encoding.UTF8.GetString(teamBuffer.ToArray()), true);
            }
            catch (Exception errorE) { writer.WriteString("team_error", errorE.GetType().Name); }
        }

        internal static void WriteBuilding(Utf8JsonWriter writer, BuildingInst building)
        {
            writer.WriteStartArray("observed_pose"); writer.WriteNumberValue(building.X); writer.WriteNumberValue(building.Y);
            writer.WriteNumberValue(building.Rotation); writer.WriteEndArray();
            try
            {
                writer.WriteBoolean("is_resource", BuildingUtl.IsResource(building.Type));
                writer.WriteNumber("resource_type", (int)BuildingUtl.GetResourceType(building.Type));
            }
            catch { }
            try
            {
                var collider = building.Obj?.Col;
                if (collider != null)
                {
                    bool active = collider.enabled && collider.gameObject.activeInHierarchy;
                    int layer = 1 << collider.gameObject.layer;
                    writer.WriteBoolean("raycast_enabled", active && (ColMgr.kLayerMaskBounce & layer) != 0);
                    writer.WriteBoolean("pickup_enabled", active && (ColMgr.kLayerMaskPickup & layer) != 0);
                }
            }
            catch { }
            try { writer.WriteNumber("effect_value", building.GetStatBonusAmt()); } catch { }
            try
            {
                int level = building.UpgradeLvl + (building.CurState.ToString() == "kUpgrading" ? 1 : 0);
                writer.WriteNumber("completion_effect_value", building.GetInfo().GetStatBonusAmt(level));
            }
            catch { }
            try { writer.WriteBoolean("housing_effect_active", BuildingUtl.HasHousingUpgrade(building.Type)); } catch { }
            try { writer.WriteNumber("hit_limit", building.GetBabyWorkerBounceLimit()); } catch { }
            try { writer.WriteNumber("hits_this_harvest", building.NumBouncesThisHarvest); } catch { }
            try { writer.WriteNumber("task_seconds", building.CurTaskSecs); writer.WriteNumber("task_target_seconds", building.GetTaskTgtSecs()); } catch { }
            try
            {
                writer.WriteBoolean("task_active", building.HasActiveTask());
                writer.WriteBoolean("is_idle_harvester", BuildingUtl.IsIdleHarvester(building.Type));
                writer.WriteNumber("production_resource", (int)BuildingUtl.GetWorkstationResource(building.Type));
                int level = building.UpgradeLvl + (building.CurState.ToString() == "kUpgrading" ? 1 : 0);
                writer.WriteNumber("completion_capacity", BuildingUtl.GetResourceCapacity(building.Type, level));
            }
            catch { }
            try { writer.WriteNumber("regen_bonus", building.GetSpeedImprovementAmtInRange()); } catch { }
            try { WriteRange(writer, building.GetInfo(), building.GetTileSize(), building.Rotation); }
            catch (Exception error) { writer.WriteNull("range_boxes"); writer.WriteString("range_error", error.GetType().Name); }
        }

        internal static void WriteRange(Utf8JsonWriter writer, BuildingInfo info, Vector2Int size, int rotation)
        {
            try
            {
                // BuildingUtl.IsInRange(0x6B6560)의 대상 영역. 상대 좌표라 이동 후보에도 재사용한다.
                // 물리 충돌 상자와 효과 범위의 대상 상자는 다르다. 콜라이더 AABB로 대체하지 않는다.
                using var memoryStream = new MemoryStream();
                using (var rangeWriter = new Utf8JsonWriter(memoryStream))
                {
                    rangeWriter.WriteStartArray();
                    string shape = info.ColType.ToString();
                    if (shape == "kBox") RangeBox(rangeWriter, -size.x * .5f, -size.y * .5f, size.x * .5f, size.y * .5f);
                    else if (shape == "kPoly")
                    {
                        var grid = info.InnerGrid.TryCast<Il2CppSystem.Array>();
                        if (grid == null) throw new InvalidOperationException("InnerGrid");
                        for (int x = 0; x < size.x; x++) for (int y = 0; y < size.y; y++)
                        {
                            int ix = x, iy = y;
                            if (rotation == 1) { ix = y; iy = size.x - 1 - x; }
                            else if (rotation == 2) { ix = size.x - 1 - x; iy = size.y - 1 - y; }
                            else if (rotation == 3) { ix = size.y - 1 - y; iy = x; }
                            if (!grid.GetValue(ix, iy).Unbox<bool>()) continue;
                            float columnIndex = (x + .5f - size.x * .5f) * BaseGridMgr.kSpaceWidth;
                            float rowIndex = (size.y * .5f - y - .5f) * BaseGridMgr.kSpaceWidth;
                            RangeBox(rangeWriter, columnIndex - .3125f, rowIndex - .3125f, columnIndex + .3125f, rowIndex + .3125f);
                        }
                    }
                    else if (shape == "kCircle") RangeBox(rangeWriter, 0, 0, 0, 0);
                    else throw new InvalidOperationException("ColliderType");
                    rangeWriter.WriteEndArray();
                }
                writer.WritePropertyName("range_boxes"); writer.WriteRawValue(Encoding.UTF8.GetString(memoryStream.ToArray()), true);
                writer.WriteNumber("range_rotation", rotation);
            }
            catch (Exception error) { writer.WriteNull("range_boxes"); writer.WriteString("range_error", error.GetType().Name); }
        }

        static void RangeBox(Utf8JsonWriter writer, float x0, float y0, float x1, float y1)
        {
            writer.WriteStartArray(); writer.WriteNumberValue(x0); writer.WriteNumberValue(y0);
            writer.WriteNumberValue(x1); writer.WriteNumberValue(y1); writer.WriteEndArray();
        }

        static void Point(Utf8JsonWriter writer, Vector3 position)
        {
            writer.WriteStartArray(); writer.WriteNumberValue(position.x); writer.WriteNumberValue(position.y); writer.WriteEndArray();
        }

        static void WriteCollider(Utf8JsonWriter writer, Collider2D collider)
        {
            writer.WriteStartObject();
            writer.WriteNumber("instance_id", collider.GetInstanceID());
            var currentTransform = collider.transform;
            var boxCollider = collider.TryCast<BoxCollider2D>();
            var circle = collider.TryCast<CircleCollider2D>();
            var poly = collider.TryCast<PolygonCollider2D>();
            var edge = collider.TryCast<EdgeCollider2D>();
            if (circle != null)
            {
                writer.WriteString("shape", "circle");
                writer.WritePropertyName("c"); Point(writer, currentTransform.TransformPoint(circle.offset));
                writer.WriteNumber("r", circle.radius * Math.Max(Math.Abs(currentTransform.lossyScale.x), Math.Abs(currentTransform.lossyScale.y)));
            }
            else
            {
                writer.WriteString("shape", edge != null ? "edge" : boxCollider != null ? "box" : "poly");
                writer.WriteStartArray("paths");
                if (poly != null)
                {
                    for (int index = 0; index < poly.pathCount; index++)
                    {
                        writer.WriteStartArray();
                        foreach (var position in poly.GetPath(index)) Point(writer, currentTransform.TransformPoint(position + poly.offset));
                        writer.WriteEndArray();
                    }
                }
                else
                {
                    writer.WriteStartArray();
                    if (edge != null) foreach (var positionP in edge.points) Point(writer, currentTransform.TransformPoint(positionP + edge.offset));
                    else if (boxCollider != null)
                    {
                        var positionO = boxCollider.offset; var positionH = boxCollider.size * .5f;
                        Point(writer, currentTransform.TransformPoint(new Vector3(positionO.x - positionH.x, positionO.y - positionH.y, 0)));
                        Point(writer, currentTransform.TransformPoint(new Vector3(positionO.x + positionH.x, positionO.y - positionH.y, 0)));
                        Point(writer, currentTransform.TransformPoint(new Vector3(positionO.x + positionH.x, positionO.y + positionH.y, 0)));
                        Point(writer, currentTransform.TransformPoint(new Vector3(positionO.x - positionH.x, positionO.y + positionH.y, 0)));
                    }
                    writer.WriteEndArray();
                }
                writer.WriteEndArray();
                if (boxCollider == null && poly == null && edge == null) writer.WriteBoolean("unsupported", true);
            }
            writer.WriteEndObject();
        }
    }
}
