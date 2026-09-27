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
        static string _environment;
        static float _environmentAt = -10;
        static string _environmentState;

        internal static void Write(Utf8JsonWriter w, BaseGridMgr g, BaseMgr bm, MetaSaveData meta)
        {
            try
            {
                string state=bm.CurState.ToString();
                if (_environment == null || _environmentState!=state || Time.realtimeSinceStartup - _environmentAt >= 1f)
                {
                    using var ms = new MemoryStream();
                    using (var ew = new Utf8JsonWriter(ms))
                    {
                        ew.WriteStartObject();
                        ew.WriteBoolean("queries_start_in_colliders",Physics2D.queriesStartInColliders);
                        ew.WriteBoolean("queries_hit_triggers",Physics2D.queriesHitTriggers);
                        ew.WriteStartArray("walls");
                        var cols = UnityEngine.Object.FindObjectsOfType<Collider2D>();
                        int mask = ColMgr.kLayerMaskAllBaseObstacles;
                        if (cols != null) foreach (var col in cols)
                        {
                            if (col == null || !col.enabled || !col.gameObject.activeInHierarchy || col.isTrigger) continue;
                            if ((mask & (1 << col.gameObject.layer)) == 0) continue;
                            if (g.BuildingColDict != null && g.BuildingColDict.ContainsKey(col)) continue;
                            WriteCollider(ew, col);
                        }
                        ew.WriteEndArray();
                        ew.WriteStartArray("roads");
                        var chunks = meta.BaseChunks;
                        if (chunks != null && BuildingMgr.I != null)
                            for (int x = 0; x < chunks.Length; x++)
                                if (chunks[x] != null) for (int y = 0; y < chunks[x].Length; y++)
                                {
                                    var chunk = chunks[x][y];
                                    if (chunk == null || !chunk.IsPurchased) continue;
                                    var corner = BaseGridMgr.GetChunkBotLeft(chunk.X, chunk.Y);
                                    for (int cx = 0; cx < BaseGridMgr.kChunkWidth; cx++)
                                        for (int cy = 0; cy < BaseGridMgr.kChunkHeight; cy++)
                                        {
                                            float lx = corner.x + cx * BaseGridMgr.kSpaceWidth;
                                            float ly = corner.y + cy * BaseGridMgr.kSpaceHeight;
                                            if (BuildingMgr.I.GetTile(lx + BaseGridMgr.kSpaceWidth / 2,
                                                                      ly + BaseGridMgr.kSpaceHeight / 2) != BaseTileType.kStoneRoad) continue;
                                            ew.WriteStartArray();
                                            ew.WriteNumberValue(lx); ew.WriteNumberValue(ly);
                                            ew.WriteNumberValue(lx + BaseGridMgr.kSpaceWidth);
                                            ew.WriteNumberValue(ly + BaseGridMgr.kSpaceHeight);
                                            ew.WriteEndArray();
                                        }
                                }
                        ew.WriteEndArray();
                        var road = InfoDB.I.Buildings[(int)BuildingType.kStoneRoad];
                        ew.WriteNumber("road_speed_mult", 1.0 + road.GetStatBonusAmt(0) / 100.0);
                        ew.WriteEndObject();
                    }
                    _environment = Encoding.UTF8.GetString(ms.ToArray());
                    _environmentAt = Time.realtimeSinceStartup;
                    _environmentState=state;
                }
                w.WritePropertyName("environment"); w.WriteRawValue(_environment, true);
                w.WriteNumber("physics_contract", 2);
                w.WriteNumber("environment_age", Math.Round(Time.realtimeSinceStartup - _environmentAt, 3));
            }
            catch (Exception e) { w.WriteString("physics_error", e.GetType().Name); }
            try
            {
                if (TimeMgr.I != null)
                {
                    w.WriteNumber("game_time", TimeMgr.I.GetTime());
                    w.WriteNumber("physics_time", TimeMgr.I.GetPhysicsTime());
                    w.WriteNumber("game_speed", TimeMgr.I.GetGameSpeed());
                    w.WriteNumber("physics_step",TimeMgr.I.GetFixedDeltaTime());
                }
                if(WorldTimeMgr.I!=null)
                {
                    w.WriteNumber("world_tick_progress",WorldTimeMgr.I._timeProgress);
                    w.WriteNumber("world_tick_interval",WorldTimeMgr.I.GetTimeThreshold());
                }
            }
            catch { }
            try
            {
                using var ms = new MemoryStream();
                using (var tw = new Utf8JsonWriter(ms))
                {
                    tw.WriteStartArray();
                    var workers = bm.ActiveWorkers;
                    if (workers != null) for (int i = 0; i < workers.Count; i++)
                    {
                        var c = workers[i];
                        if (c == null) continue;
                        tw.WriteStartObject();
                        tw.WriteString("type", c.Type.ToString());
                        tw.WriteNumber("launch_index", i);
                        tw.WriteStartObject("upgrades");
                        foreach (HarvestUpgradeType type in Enum.GetValues(typeof(HarvestUpgradeType)))
                            if (type != HarvestUpgradeType.kNum) tw.WriteNumber(type.ToString(), c.GetHarvestUpgradeLvl(type));
                        tw.WriteEndObject();
                        tw.WriteStartObject("harvest_bonus");
                        foreach (HarvestUpgradeType type in Enum.GetValues(typeof(HarvestUpgradeType)))
                            if (type != HarvestUpgradeType.kNum) tw.WriteNumber(type.ToString(), c.GetHarvestUpgradeBonusAmt(type));
                        tw.WriteEndObject();
                        tw.WriteEndObject();
                    }
                    tw.WriteEndArray();
                }
                w.WritePropertyName("launch_team");w.WriteRawValue(Encoding.UTF8.GetString(ms.ToArray()), true);
            }
            catch (Exception e) { w.WriteString("team_error", e.GetType().Name); }
        }

        internal static void WriteBuilding(Utf8JsonWriter w, BuildingInst b)
        {
            w.WriteStartArray("observed_pose");w.WriteNumberValue(b.X);w.WriteNumberValue(b.Y);
            w.WriteNumberValue(b.Rotation);w.WriteEndArray();
            try { w.WriteBoolean("is_resource",BuildingUtl.IsResource(b.Type));
                  w.WriteNumber("resource_type",(int)BuildingUtl.GetResourceType(b.Type)); } catch { }
            try
            {
                var col=b.Obj?.Col;
                if (col!=null)
                {
                    bool active=col.enabled && col.gameObject.activeInHierarchy;
                    int layer=1<<col.gameObject.layer;
                    w.WriteBoolean("raycast_enabled",active && (ColMgr.kLayerMaskBounce & layer)!=0);
                    w.WriteBoolean("pickup_enabled",active && (ColMgr.kLayerMaskPickup & layer)!=0);
                }
            }
            catch { }
            try { w.WriteNumber("effect_value", b.GetStatBonusAmt()); } catch { }
            try
            {
                int level=b.UpgradeLvl+(b.CurState.ToString()=="kUpgrading"?1:0);
                w.WriteNumber("completion_effect_value",b.GetInfo().GetStatBonusAmt(level));
            }
            catch { }
            try { w.WriteBoolean("housing_effect_active", BuildingUtl.HasHousingUpgrade(b.Type)); } catch { }
            try { w.WriteNumber("hit_limit", b.GetBabyWorkerBounceLimit()); } catch { }
            try { w.WriteNumber("hits_this_harvest", b.NumBouncesThisHarvest); } catch { }
            try { w.WriteNumber("task_seconds", b.CurTaskSecs); w.WriteNumber("task_target_seconds", b.GetTaskTgtSecs()); } catch { }
            try
            {
                w.WriteBoolean("task_active",b.HasActiveTask());
                w.WriteBoolean("is_idle_harvester",BuildingUtl.IsIdleHarvester(b.Type));
                w.WriteNumber("production_resource",(int)BuildingUtl.GetWorkstationResource(b.Type));
                int level=b.UpgradeLvl+(b.CurState.ToString()=="kUpgrading"?1:0);
                w.WriteNumber("completion_capacity",BuildingUtl.GetResourceCapacity(b.Type,level));
            }
            catch { }
            try { w.WriteNumber("regen_bonus", b.GetSpeedImprovementAmtInRange()); } catch { }
            try { WriteRange(w,b.GetInfo(),b.GetTileSize(),b.Rotation); }
            catch (Exception e) { w.WriteNull("range_boxes");w.WriteString("range_error",e.GetType().Name); }
        }

        internal static void WriteRange(Utf8JsonWriter w,BuildingInfo info,Vector2Int size,int rotation)
        {
            try
            {
                // BuildingUtl.IsInRange(0x6B6560)의 대상 영역. 상대 좌표라 이동 후보에도 재사용한다.
                using var ms = new MemoryStream();
                using (var rw = new Utf8JsonWriter(ms))
                {
                    rw.WriteStartArray();
                    string shape = info.ColType.ToString();
                    if (shape == "kBox") RangeBox(rw, -size.x*.5f,-size.y*.5f,size.x*.5f,size.y*.5f);
                    else if (shape == "kPoly")
                    {
                        var grid = info.InnerGrid.TryCast<Il2CppSystem.Array>();
                        if (grid == null) throw new InvalidOperationException("InnerGrid");
                        for (int x=0; x<size.x; x++) for (int y=0; y<size.y; y++)
                        {
                            int ix=x,iy=y;
                            if (rotation==1) { ix=y; iy=size.x-1-x; }
                            else if (rotation==2) { ix=size.x-1-x; iy=size.y-1-y; }
                            else if (rotation==3) { ix=size.y-1-y; iy=x; }
                            if (!grid.GetValue(ix,iy).Unbox<bool>()) continue;
                            float cx=(x+.5f-size.x*.5f)*BaseGridMgr.kSpaceWidth;
                            float cy=(size.y*.5f-y-.5f)*BaseGridMgr.kSpaceWidth;
                            RangeBox(rw,cx-.3125f,cy-.3125f,cx+.3125f,cy+.3125f);
                        }
                    }
                    else if (shape == "kCircle") RangeBox(rw,0,0,0,0);
                    else throw new InvalidOperationException("ColliderType");
                    rw.WriteEndArray();
                }
                w.WritePropertyName("range_boxes");w.WriteRawValue(Encoding.UTF8.GetString(ms.ToArray()),true);
                w.WriteNumber("range_rotation",rotation);
            }
            catch (Exception e) { w.WriteNull("range_boxes");w.WriteString("range_error",e.GetType().Name); }
        }

        static void RangeBox(Utf8JsonWriter w,float x0,float y0,float x1,float y1)
        {
            w.WriteStartArray();w.WriteNumberValue(x0);w.WriteNumberValue(y0);
            w.WriteNumberValue(x1);w.WriteNumberValue(y1);w.WriteEndArray();
        }

        static void Point(Utf8JsonWriter w, Vector3 p)
        {
            w.WriteStartArray(); w.WriteNumberValue(p.x); w.WriteNumberValue(p.y); w.WriteEndArray();
        }

        static void WriteCollider(Utf8JsonWriter w, Collider2D col)
        {
            w.WriteStartObject();
            w.WriteNumber("instance_id", col.GetInstanceID());
            var t = col.transform;
            var box = col.TryCast<BoxCollider2D>();
            var circle = col.TryCast<CircleCollider2D>();
            var poly = col.TryCast<PolygonCollider2D>();
            var edge = col.TryCast<EdgeCollider2D>();
            if (circle != null)
            {
                w.WriteString("shape", "circle");
                w.WritePropertyName("c"); Point(w, t.TransformPoint(circle.offset));
                w.WriteNumber("r", circle.radius * Math.Max(Math.Abs(t.lossyScale.x), Math.Abs(t.lossyScale.y)));
            }
            else
            {
                w.WriteString("shape", edge != null ? "edge" : box != null ? "box" : "poly");
                w.WriteStartArray("paths");
                if (poly != null)
                {
                    for (int i = 0; i < poly.pathCount; i++)
                    {
                        w.WriteStartArray();
                        foreach (var p in poly.GetPath(i)) Point(w, t.TransformPoint(p + poly.offset));
                        w.WriteEndArray();
                    }
                }
                else
                {
                    w.WriteStartArray();
                    if (edge != null) foreach (var p in edge.points) Point(w, t.TransformPoint(p + edge.offset));
                    else if (box != null)
                    {
                        var o = box.offset; var h = box.size * .5f;
                        Point(w, t.TransformPoint(new Vector3(o.x-h.x,o.y-h.y,0)));
                        Point(w, t.TransformPoint(new Vector3(o.x+h.x,o.y-h.y,0)));
                        Point(w, t.TransformPoint(new Vector3(o.x+h.x,o.y+h.y,0)));
                        Point(w, t.TransformPoint(new Vector3(o.x-h.x,o.y+h.y,0)));
                    }
                    w.WriteEndArray();
                }
                w.WriteEndArray();
                if (box == null && poly == null && edge == null) w.WriteBoolean("unsupported", true);
            }
            w.WriteEndObject();
        }
    }
}
