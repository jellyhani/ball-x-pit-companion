// BALL x PIT 선택 도우미용 읽기 전용 상태 브리지.
//
// 하는 일: 게임 메인 스레드에서 0.2초마다 현재 런 상태(캐릭터, 보유 볼·패시브와 레벨, 골드, 체력,
//          강화 선택창의 선택지와 화면 위치)를 읽어, 바뀌었을 때만 JSON 한 줄로 named pipe 에 보낸다.
// 하지 않는 일: 게임 값·세이브·RNG를 바꾸지 않는다. Harmony 패치도 쓰지 않는다.
// 통신: \\.\pipe\ballxpit-bridge-<사용자> , 현재 사용자만 접근. 한 줄 = JSON 하나 (UTF-8).
// 메시지 종류: catalog(연결 직후 1번: 레시피·레벨별 수치), 상태(0.2초마다 바뀌면), meta(기지·누적 기록, 5초마다 바뀌면).
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Threading;
using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using UnityEngine;

namespace BallxPitBridge
{
    [BepInPlugin("dev.ballxpit.bridge", "BALL x PIT Bridge", Plugin.Version)]
    public class Plugin : BasePlugin
    {
        public const string Version = "1.18.2";   // 도우미 앱이 이 값으로 설치된 플러그인이 최신인지 확인한다
        internal static ManualLogSource L;

        public override void Load()
        {
            L = Log;
            PipeServer.Start();
            AddComponent<BridgeBehaviour>();
            L.LogInfo("브리지 시작 (읽기 전용)");
        }

        public override bool Unload()
        {
            PipeServer.Stop();
            return true;
        }
    }

    /// <summary>파이프 서버. 게임 스레드는 최신 메시지만 넘기고 기다리지 않는다.</summary>
    internal static class PipeServer
    {
        public const int ProtocolVersion = 1;
        static readonly object Gate = new object();
        static string _latest;
        static string _meta;
        public static volatile string Catalog;   // 연결 직후 한 번 보내는 레시피 표
        static long _latestSeq;
        static long _metaSeq;
        static readonly AutoResetEvent Signal = new AutoResetEvent(false);
        static Thread _thread;
        static volatile bool _running;
        public static volatile bool Connected;

        public static string PipeName => "ballxpit-bridge-" + Environment.UserName;

        public static void Start()
        {
            _running = true;
            _thread = new Thread(Run) { IsBackground = true, Name = "BallxPitBridgePipe" };
            _thread.Start();
        }

        public static void Stop()
        {
            _running = false;
            Signal.Set();
        }

        public static void Publish(string json)
        {
            lock (Gate)
            {
                _latest = json;
                _latestSeq++;
            }
            Signal.Set();
        }

        public static void PublishMeta(string json)
        {
            lock (Gate)
            {
                _meta = json;
                _metaSeq++;
            }
            Signal.Set();
        }

        static void Send(NamedPipeServerStream server, string msg)
        {
            var bytes = Encoding.UTF8.GetBytes(msg + "\n");
            server.Write(bytes, 0, bytes.Length);
            server.Flush();
        }

        static NamedPipeServerStream Create()
        {
            try
            {
                var sec = new PipeSecurity();
                sec.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
                    PipeAccessRights.FullControl, AccessControlType.Allow));
                return NamedPipeServerStreamAcl.Create(PipeName, PipeDirection.Out, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous, 0, 0, sec);
            }
            catch (Exception error)
            {
                Plugin.L?.LogWarning("파이프 권한 지정 실패, 기본 권한 사용: " + error.Message);
                return new NamedPipeServerStream(PipeName, PipeDirection.Out, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous);
            }
        }

        static void Run()
        {
            while (_running)
            {
                try
                {
                    using var server = Create();
                    server.WaitForConnection();
                    Connected = true;
                    Plugin.L?.LogInfo("도우미 앱 연결됨");
                    for (int waited = 0; Catalog == null && waited < 50 && _running; waited++) Thread.Sleep(100);
                    if (Catalog != null) Send(server, Catalog);
                    long sent = -1, metaSent = -1;
                    while (_running && server.IsConnected)
                    {
                        string msg, meta;
                        long seq, mseq;
                        lock (Gate)
                        {
                            msg = _latest;
                            seq = _latestSeq;
                            meta = _meta;
                            mseq = _metaSeq;
                        }
                        if (meta != null && mseq != metaSent)
                        {
                            Send(server, meta);
                            metaSent = mseq;
                        }
                        if (msg != null && seq != sent)
                        {
                            Send(server, msg);
                            sent = seq;
                        }
                        Signal.WaitOne(2000);
                    }
                }
                catch (IOException)
                {
                    // 앱이 연결을 끊음 → 다시 기다린다
                }
                catch (Exception error)
                {
                    Plugin.L?.LogWarning("파이프 오류: " + error.Message);
                    Thread.Sleep(1000);
                }
                finally
                {
                    if (Connected) Plugin.L?.LogInfo("도우미 앱 연결 끊김");
                    Connected = false;
                }
            }
        }
    }

    public class BridgeBehaviour : MonoBehaviour
    {
        public BridgeBehaviour(IntPtr ptr) : base(ptr) { }

        float _nextRead;
        float _nextHeartbeat;
        float _nextMeta;
        readonly System.Diagnostics.Stopwatch _sw = new System.Diagnostics.Stopwatch();
        double _costMs;
        string _lastBody = "";
        string _lastMeta = "";
        long _seq;
        readonly HashSet<string> _loggedErrors = new HashSet<string>();

        void Update()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _nextRead) return;
            // 채집 조준 중에는 조준선이 부드럽게 따라오도록 초당 10번, 그 밖에는 5번 (건물 모양은 바뀔 때만 다시 읽음)
            _nextRead = now + (Snapshot.Aiming ? 0.1f : 0.2f);
            if (!PipeServer.Connected) { _lastBody = ""; _lastMeta = ""; return; }
            if (PipeServer.Catalog == null)
            {
                try { PipeServer.Catalog = Snapshot.BuildCatalog(); }
                catch (Exception error) { if (_loggedErrors.Add("catalog" + error.Message)) Plugin.L.LogWarning("레시피 표 실패: " + error); }
            }
            if (now >= _nextMeta)
            {
                _nextMeta = now + 5f;
                try
                {
                    string meta = Meta.Build();
                    if (meta != null && meta != _lastMeta)
                    {
                        _lastMeta = meta;
                        PipeServer.PublishMeta(meta);
                    }
                }
                catch (Exception errorE)
                {
                    if (_loggedErrors.Add("meta" + errorE.GetType().Name + errorE.Message)) Plugin.L.LogWarning("기지 정보 읽기 실패: " + errorE);
                }
            }
            try
            {
                _sw.Restart();
                string body = Snapshot.Build();
                _sw.Stop();
                // 게임 프레임에서 읽기에 쓴 시간 (지수 평균, 밀리초) — 앱 진단 화면에 보여 준다
                _costMs = _costMs * 0.9 + _sw.Elapsed.TotalMilliseconds * 0.1;
                bool heartbeat = now >= _nextHeartbeat;
                if (body != _lastBody || heartbeat)
                {
                    _lastBody = body;
                    _nextHeartbeat = now + 2f;
                    _seq++;
                    PipeServer.Publish("{\"v\":" + PipeServer.ProtocolVersion + ",\"plugin\":\"" + Plugin.Version + "\",\"seq\":" + _seq + ",\"t\":" +
                                       now.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture) +
                                       ",\"cost_ms\":" + _costMs.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture) +
                                       "," + body + "}");
                }
            }
            catch (Exception errorE)
            {
                if (_loggedErrors.Add(errorE.GetType().Name + errorE.Message))
                    Plugin.L.LogWarning("상태 읽기 실패: " + errorE);
            }
        }
    }

    /// <summary>게임 객체에서 값을 읽어 JSON 본문(중괄호 없는 필드들)을 만든다. 게임 메인 스레드 전용.</summary>
    internal static class Snapshot
    {
        public static string Build()
        {
            using var memoryStream = new MemoryStream();
            using (var writer = new Utf8JsonWriter(memoryStream))
            {
                writer.WriteStartObject();
                writer.WriteString("game_version", Application.version);
                writer.WriteNumber("screen_w", Screen.width);
                writer.WriteNumber("screen_h", Screen.height);
                WriteGameState(writer);
                WriteBattle(writer);
                WriteLevelUp(writer);
                WriteGameOver(writer);
                WriteField(writer);
                WriteBase(writer);
                WriteUiAvoid(writer);
                writer.WriteEndObject();
            }
            var json = Encoding.UTF8.GetString(memoryStream.ToArray());
            return json.Substring(1, json.Length - 2);   // 바깥 중괄호 제거 (머리 필드와 합치기 위해)
        }

        static void WriteGameState(Utf8JsonWriter writer)
        {
            var gm = GameMgr.I;
            writer.WriteString("game_state", gm != null ? gm.CurState.ToString() : null);
        }

        /// <summary>런이 끝난 화면(보스 격퇴 후 '원정 계속' 버튼 포함). 계속/복귀 판단용.</summary>
        static void WriteGameOver(Utf8JsonWriter writer)
        {
            var ui = GameUIMgr.I != null ? GameUIMgr.I.GameOver : null;
            bool open = ui != null && ui.gameObject.activeInHierarchy && ui.IsActiveOverlay();
            if (!open)
            {
                writer.WriteNull("game_over");
                return;
            }
            writer.WriteStartObject("game_over");
            var battleData = BattleSaveData.I;
            if (battleData != null) writer.WriteBoolean("completed", battleData.CompletedLevel);
            try { writer.WriteBoolean("endless_btn", ui.BtnEndless != null && ui.BtnEndless.gameObject.activeInHierarchy); } catch { }
            try { writer.WriteBoolean("endless_unlocked", BuildingMgr.I != null && BuildingMgr.I.EndlessModeUnlocked); } catch { }
            writer.WriteEndObject();
        }

        static void WriteBattle(Utf8JsonWriter writer)
        {
            var battleData = BattleSaveData.I;
            if (battleData == null)
            {
                writer.WriteNull("battle");
                return;
            }
            writer.WriteStartObject("battle");
            var character = battleData.CurChar;
            if (character != null)
            {
                writer.WriteString("char", character.Type.ToString());
                var extra = character.CombinedTypes;
                writer.WriteStartArray("chars_combined");
                if (extra != null)
                    for (int index = 0; index < extra.Count; index++) writer.WriteStringValue(extra[index].ToString());
                writer.WriteEndArray();
            }
            writer.WriteString("level", battleData.CurLevel.ToString());
            writer.WriteNumber("turn", battleData.CurTurn);
            var currentCost = battleData.NumResources;
            if (currentCost != null)
            {
                writer.WriteNumber("gold", currentCost.GetTotalAmount());
                writer.WriteString("resources", currentCost.ToString());
            }
            writer.WriteNumber("health", battleData.CurHealth);
            writer.WriteNumber("upgrade_lvl", battleData.UpgradeLvl);
            writer.WriteNumber("level_ups_avail", battleData.NumLevelUpsAvail);
            writer.WriteNumber("free_rerolls", battleData.NumFreeRerolls);
            writer.WriteNumber("banishes", battleData.NumBanishes);
            writer.WriteNumber("rerolls", battleData.NumLvlUpRerolls);
            // 진행 상황 (판단 근거용)
            try { if (UpgradeMgr.I != null) writer.WriteNumber("max_health", UpgradeMgr.I.MaxHealth); } catch { }
            writer.WriteNumber("final_boss_turn", battleData.FinalBossTurn);
            writer.WriteNumber("difficulty", battleData.CurDifficulty);
            writer.WriteNumber("ng_plus", battleData.CurNGPlusLvl);
            writer.WriteBoolean("endless", battleData.IsEndless);
            writer.WriteNumber("endless_start_turn", battleData.EndlessStartTurn);
            writer.WriteBoolean("completed_level", battleData.CompletedLevel);
            writer.WriteNumber("revives", battleData.NumRevives);
            writer.WriteNumber("kills", battleData.NumKills);
            writer.WriteNumber("elapsed", Math.Round(battleData.ElapsedTime, 1));
            writer.WriteNumber("evos_fused", battleData.NumEvosFused);
            writer.WriteNumber("combos_fused", battleData.NumCombosFused);
            writer.WriteNumber("baby_dmg", battleData.BabyDamageDealt);
            try { if (BuildingMgr.I != null) writer.WriteNumber("revives_max", BuildingMgr.I.NumRevives); } catch { }
            WriteBattleExtra(writer, battleData);
            try { writer.WriteNumber("max_balls", StatUtl.GetMaxHeroes()); } catch { }
            try { writer.WriteNumber("max_passives", StatUtl.GetMaxPassives()); } catch { }
            writer.WriteStartArray("banished");
            var banished = battleData.BanishedItems;
            if (banished != null)
                for (int loopIndex = 0; loopIndex < banished.Count; loopIndex++) WriteInfoTypeValue(writer, banished[loopIndex]);
            writer.WriteEndArray();

            writer.WriteStartArray("balls");
            var heroes = battleData.Heroes;
            if (heroes != null)
            {
                for (int loopIndex = 0; loopIndex < heroes.Count; loopIndex++)
                {
                    var h = heroes[loopIndex];
                    if (h == null) continue;
                    writer.WriteStartObject();
                    writer.WriteNumber("idx", loopIndex);
                    writer.WriteString("type", h.Type.ToString());
                    writer.WriteNumber("lvl", h.Lvl);
                    writer.WriteBoolean("max", h.IsAtMaxSolo());
                    WriteHeroStats(writer, h);
                    var combo = h.CombinedHeroes;
                    if (combo != null && combo.Count > 0)
                    {
                        writer.WriteStartArray("combined");
                        for (int otherIndex = 0; otherIndex < combo.Count; otherIndex++)
                        {
                            writer.WriteStringValue(combo[otherIndex].ToString());
                        }
                        writer.WriteEndArray();
                    }
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();

            writer.WriteStartArray("passives");
            var passives = battleData.Passives;
            if (passives != null)
            {
                for (int loopIndex = 0; loopIndex < passives.Count; loopIndex++)
                {
                    var p = passives[loopIndex];
                    if (p == null) continue;
                    writer.WriteStartObject();
                    writer.WriteNumber("idx", loopIndex);
                    writer.WriteString("type", p.Type.ToString());
                    writer.WriteNumber("lvl", p.Lvl);
                    try { writer.WriteBoolean("max", p.IsAtMaxSolo()); } catch { }
                    try
                    {
                        var sd = p.StatData;
                        long bonus = 0;
                        if (sd != null) for (int componentIndex = 0; componentIndex < sd.Count; componentIndex++) if (sd[componentIndex] != null) bonus += sd[componentIndex].BonusDamage;
                        writer.WriteNumber("dmg", bonus);
                    }
                    catch { }
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        /// <summary>게임 월드 좌표 → 게임 클라이언트 화면 좌표(왼쪽 위 원점). 카메라가 없으면 false.</summary>
        static bool ToScreen(Vector3 world, out float x, out float y)
        {
            x = y = 0;
            var camera = Camera.main;
            if (camera == null) return false;
            var currentPosition = camera.WorldToScreenPoint(world);
            if (currentPosition.z < 0) return false;
            x = currentPosition.x;
            y = Screen.height - currentPosition.y;
            return true;
        }

        /// <summary>전투 필드: 적 위치(화면 좌표, 가까운 순), 플레이어 위치, 적이 공격하는 선. 읽기만 한다.</summary>
        static void WriteField(Utf8JsonWriter writer)
        {
            var gm = GridMgr.I;
            if (gm == null || BattleSaveData.I == null || GameMgr.I == null)
            {
                writer.WriteNull("field");
                return;
            }
            writer.WriteStartObject("field");
            try
            {
                writer.WriteNumber("attack_y", Math.Round(gm.AttackY, 2));
                writer.WriteNumber("front_enemy_y", Math.Round(gm.FrontEnemyY, 2));
                writer.WriteNumber("bottom_y", Math.Round(gm.BottomBorderY, 2));
                writer.WriteNumber("top_y", Math.Round(gm.TopBorderY, 2));
                writer.WriteNumber("left_x", Math.Round(gm.LeftBorderX, 2));
                writer.WriteNumber("right_x", Math.Round(gm.RightBorderX, 2));
            }
            catch { }
            try
            {
                var pl = Player.I;
                if (pl != null)
                {
                    var currentPosition = pl.transform.position;
                    writer.WriteStartArray("player");
                    if (ToScreen(currentPosition, out var screenX, out var screenY)) { writer.WriteNumberValue((int)screenX); writer.WriteNumberValue((int)screenY); }
                    else { writer.WriteNumberValue(-1); writer.WriteNumberValue(-1); }
                    writer.WriteNumberValue(currentPosition.x);
                    writer.WriteNumberValue(currentPosition.y);
                    writer.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var list = new List<(float wy, float wx, float sx, float sy, int hp, int max, bool boss)>();
                var dict = gm.PieceColDict;
                if (dict != null)
                    foreach (var colliders in dict)
                    {
                        var obj = colliders.Value;
                        if (obj == null || !obj.IsActive) continue;
                        var inst = obj.Inst;
                        if (inst == null || inst.CurHealth <= 0) continue;
                        var currentPositionPos = obj.transform.position;
                        if (!ToScreen(currentPositionPos, out var screenXSx, out var screenYSy)) continue;
                        bool boss = false;
                        try { boss = StatUtl.IsBoss(inst.Type); } catch { }
                        list.Add((currentPositionPos.y, currentPositionPos.x, screenXSx, screenYSy, inst.CurHealth, inst.MaxHealth, boss));
                    }
                list.Sort((a, b) => a.wy.CompareTo(b.wy));
                writer.WriteStartArray("enemies");   // [화면x, 화면y, 월드y, 체력, 최대, 보스] — 월드 y 가 작을수록 플레이어에 가깝다
                for (int index = 0; index < list.Count && index < 40; index++)
                {
                    var e = list[index];
                    writer.WriteStartArray();
                    writer.WriteNumberValue((int)e.sx);
                    writer.WriteNumberValue((int)e.sy);
                    writer.WriteNumberValue(Math.Round(e.wy, 2));
                    writer.WriteNumberValue(e.hp);
                    writer.WriteNumberValue(e.max);
                    writer.WriteNumberValue(e.boss ? 1 : 0);
                    writer.WriteEndArray();
                }
                writer.WriteEndArray();
            }
            catch { }
            writer.WriteEndObject();
        }

        /// <summary>기지 화면: 상태, 수확 남은 시간, 건물 화면 위치·보관 자원·작업자, 조준 방향. 읽기만 한다.</summary>
        internal static bool Aiming;             // 채집 조준 중 (읽기 주기를 줄인다)
        static string _colJson = "";              // 건물 충돌 모양 JSON (배치가 바뀔 때만 다시 만든다)
        static long _colSig = long.MinValue;
        static float _colAt = -99f;

        // 범위 효과 건물 → 게임이 '근처'로 세는 자원 타일 종류 (앱의 범위 판정을 게임 값으로 검증)
        static readonly Dictionary<string, string[]> RangeTargets = new Dictionary<string, string[]>
        {
            { "kIdleFarm", new[] { "kWheatField", "kDenseWheat" } },
            { "kSingleFamilyHome", new[] { "kWheatField", "kDenseWheat" } },
            { "kVilla", new[] { "kWheatField", "kDenseWheat" } },
            { "kIdleLumberyard", new[] { "kForest", "kGrandTree" } },
            { "kCozyHome", new[] { "kForest", "kGrandTree" } },
            { "kCampground", new[] { "kForest", "kGrandTree" } },
            { "kIdleStoneMine", new[] { "kBoulder", "kGraniteSlab" } },
            { "kHovel", new[] { "kBoulder", "kGraniteSlab" } },
            { "kRockyHill", new[] { "kBoulder", "kGraniteSlab" } },
        };
        static readonly Dictionary<int, string> _rangeJson = new Dictionary<int, string>();
        static readonly Dictionary<int, string> _rangeIdsJson = new Dictionary<int, string>();
        static long _rangeSig = long.MinValue;
        static float _rangeAt = -10f;

        static void WriteInRange(Utf8JsonWriter writer, BuildingInst building)
        {
            string key = building.Type.ToString();
            if (!RangeTargets.TryGetValue(key, out var targets))
            {
                if (key != "kBrickHouse" && key != "kVeteranHut" && key != "kCaptainQuarters" && key != "kMansion") return;
                targets = Array.Empty<string>();
            }
            if (_rangeSig != _colSig || Time.realtimeSinceStartup - _rangeAt >= 1f)
            { _rangeJson.Clear(); _rangeIdsJson.Clear(); _rangeSig = _colSig; _rangeAt = Time.realtimeSinceStartup; }
            if (!_rangeJson.TryGetValue(building.Id, out var currentJson))
            {
                using var memoryStream = new MemoryStream();
                using (var rangeWriter = new Utf8JsonWriter(memoryStream))
                {
                    rangeWriter.WriteStartObject();
                    float r = building.GetRange();
                    foreach (var typeName in targets)
                    {
                        try
                        {
                            var bt = (BuildingType)Enum.Parse(typeof(BuildingType), typeName);
                            rangeWriter.WriteNumber(typeName, building.GetNumBuildingsInRange(bt, r));
                        }
                        catch { }
                    }
                    rangeWriter.WriteEndObject();
                }
                currentJson = Encoding.UTF8.GetString(memoryStream.ToArray());
                _rangeJson[building.Id] = currentJson;
            }
            writer.WritePropertyName("in_range");
            writer.WriteRawValue(currentJson, true);
            if (!_rangeIdsJson.TryGetValue(building.Id, out var observed))
            {
                using var rangeBuffer = new MemoryStream();
                using (var rangeWriter = new Utf8JsonWriter(rangeBuffer))
                {
                    rangeWriter.WriteStartArray();
                    var buildings = MetaSaveData.I?.Buildings;
                    if (buildings != null) for (int index = 0; index < buildings.Count; index++)
                    {
                        var target = buildings[index];
                        if (target != null && target.Id != building.Id && building.IsInRange(target)) rangeWriter.WriteNumberValue(target.Id);
                    }
                    rangeWriter.WriteEndArray();
                }
                observed = Encoding.UTF8.GetString(rangeBuffer.ToArray()); _rangeIdsJson[building.Id] = observed;
            }
            writer.WritePropertyName("in_range_ids"); writer.WriteRawValue(observed, true);
        }

        static void WriteBase(Utf8JsonWriter writer)
        {
            var baseManager = BaseMgr.I;
            var saveData = MetaSaveData.I;
            if (baseManager == null || saveData == null || !baseManager.gameObject.activeInHierarchy)
            {
                Aiming = false;
                writer.WriteNull("base");
                return;
            }
            writer.WriteStartObject("base");
            try { var st = baseManager.CurState.ToString(); Aiming = st == "kAimWorkers"; writer.WriteString("state", st); } catch { }
            // BaseMgr.LaunchWorkers가 확인하는 실제 입력 허용 상태. 추정 충돌이나 색으로 대신하지 않는다.
            try
            {
                var preview = BallPreview.I;
                if (Aiming && preview != null)
                {
                    bool allowed = preview.IsInputEnabled();
                    var direction = preview.GetAimDir();
                    writer.WriteBoolean("launch_allowed", allowed);
                    writer.WriteStartArray("launch_aim");
                    writer.WriteNumberValue(direction.x);
                    writer.WriteNumberValue(direction.y);
                    writer.WriteEndArray();
                }
            }
            catch { } // 확인 실패를 발사 가능으로 간주하지 않는다.
            // 알선소로 2명 원정: 로드아웃 화면의 두 캐릭터 패널이 지금까지 고른 캐릭터를 담고 있다
            // (kSelectingChar 로 캐릭터 고르는 화면이 열려 있는 동안에도 로드아웃 화면은 뒤에 그대로 있다).
            try
            {
                var loadout = LoadoutUI.I;
                if (loadout != null && loadout.gameObject.activeInHierarchy)
                {
                    writer.WriteStartObject("loadout");
                    try { writer.WriteString("char1", loadout.CharPanel?._tgtChar?.Type.ToString()); } catch { }
                    try { writer.WriteString("char2", loadout.Char2Panel?._tgtChar?.Type.ToString()); } catch { }
                    writer.WriteEndObject();
                }
            }
            catch { }
            try { writer.WriteNumber("harvest_secs_left", Math.Round(baseManager.RemainingHarvestSecs, 1)); } catch { }
            try { writer.WriteBoolean("harvested_today", saveData.DidHarvestToday); } catch { }
            // 스파(목욕탕): 골드를 내고 바로 한 번 더 채집 — 비용·오늘 쓴 횟수 (앱이 지난 채집량과 비교해 손익을 보여 준다)
            try
            {
                var buildingManager = BuildingMgr.I;
                if (buildingManager != null)
                {
                    writer.WriteStartObject("spa");
                    try { writer.WriteNumber("cost", buildingManager.GetMasseuseCost()); } catch { }
                    try { writer.WriteNumber("lvl", buildingManager.MasseuseLvl); } catch { }
                    try { writer.WriteNumber("used_today", saveData.NumMasseuseToday); } catch { }
                    try { writer.WriteNumber("harvests", saveData.NumHarvests); } catch { }
                    try { writer.WriteBoolean("built", buildingManager.IsBuildingBuilt(BuildingType.kMasseuse)); } catch { }
                    writer.WriteEndObject();
                }
            }
            catch { }
            try { writer.WriteNumber("day", saveData.CurDay); } catch { }
            try
            {
                var bp = BasePlayer.I;
                if (bp != null)
                {
                    writer.WriteStartArray("player");
                    if (ToScreen(bp.transform.position, out var screenX, out var screenY)) { writer.WriteNumberValue((int)screenX); writer.WriteNumberValue((int)screenY); }
                    else { writer.WriteNumberValue(-1); writer.WriteNumberValue(-1); }
                    var currentPosition = bp.GetAimDir();
                    writer.WriteNumberValue(currentPosition.x);
                    writer.WriteNumberValue(currentPosition.y);
                    writer.WriteEndArray();
                }
            }
            catch { }
            WriteBaseGeometry(writer, baseManager, saveData);
            writer.WriteStartArray("buildings");
            var list = saveData.Buildings;
            if (list != null)
                for (int index = 0; index < list.Count; index++)
                {
                    var building = list[index];
                    if (building == null) continue;
                    writer.WriteStartObject();
                    writer.WriteNumber("id", building.Id);
                    writer.WriteString("type", building.Type.ToString());
                    writer.WriteNumber("lvl", building.UpgradeLvl);
                    writer.WriteNumber("x", building.X);
                    writer.WriteNumber("y", building.Y);
                    try
                    {
                        if (building.Obj != null && ToScreen(building.Obj.transform.position, out var screenXSx, out var screenYSy))
                        {
                            writer.WriteNumber("sx", (int)screenXSx);
                            writer.WriteNumber("sy", (int)screenYSy);
                        }
                    }
                    catch { }
                    try { writer.WriteNumber("res", building.GetNumResources()); } catch { }
                    try { writer.WriteNumber("cap", building.GetResourceCapacity()); } catch { }
                    try { writer.WriteBoolean("can_harvest", building.CanHarvest()); } catch { }
                    try { if (building.HeldResources != null) { writer.WriteStartArray("held"); for (int componentIndex = 0; componentIndex < building.HeldResources.Num.Length; componentIndex++) writer.WriteNumberValue(building.HeldResources.Num[componentIndex]); writer.WriteEndArray(); } } catch { }
                    try { writer.WriteNumber("worker", building.WorkerChar); } catch { }
                    try { if (building.HasActiveTask()) writer.WriteNumber("task", Math.Round(building.GetTaskProgress(), 2)); } catch { }
                    try { writer.WriteNumber("upgrade_pct", Math.Round(building.GetUpgradePct(), 2)); } catch { }
                    // 미완성(공사장 kScaffold·강화 공사 kUpgrading): 작업자가 맞힐 때마다 UpgradePts 가 쌓여 목표에 닿으면 완성
                    try { writer.WriteString("state", building.CurState.ToString()); } catch { }
                    try { writer.WriteNumber("upg_pts", building.UpgradePts); writer.WriteNumber("upg_tgt", building.GetUpgradeTgt()); } catch { }
                    try { writer.WriteNumber("range", building.GetRange()); } catch { }
                    try { WriteInRange(writer, building); } catch { }
                    writer.WriteNumber("rot", building.Rotation);
                    PhysicsSnapshot.WriteBuilding(writer, building);
                    try
                    {
                        var info = building.GetInfo();
                        if (info != null)
                        {
                            writer.WriteNumber("tw", info.TileSize.x);
                            writer.WriteNumber("th", info.TileSize.y);
                            writer.WriteString("col", info.ColType.ToString());
                            try { writer.WriteString("stat", info.GetStatBonus().ToString()); } catch { }   // 능력치 보너스 건물이면 그 능력치
                        }
                    }
                    catch { }
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        static void Pt(Utf8JsonWriter writer, Vector3 currentPosition)
        {
            writer.WriteStartArray();
            writer.WriteNumberValue(currentPosition.x);
            writer.WriteNumberValue(currentPosition.y);
            writer.WriteEndArray();
        }

        /// <summary>기지 물리 모양: 벽, 청크, 발사대, 작업자 속도, 건물 충돌 모양(월드 좌표), 화면 대응점, 날아가는 작업자.
        /// 채집 궤적·배치 계산용. 읽기만 한다 (물리 질의도 하지 않는다).</summary>
        static void WriteBaseGeometry(Utf8JsonWriter writer, BaseMgr baseManager, MetaSaveData saveData)
        {
            var gridManager = BaseGridMgr.I;
            if (gridManager == null) return;
            writer.WriteStartObject("geo");
            PhysicsSnapshot.Write(writer, gridManager, baseManager, saveData);
            try
            {
                writer.WriteNumber("left", gridManager.LeftBorderX);
                writer.WriteNumber("right", gridManager.RightBorderX);
                writer.WriteNumber("top", gridManager.TopBorderY);
                writer.WriteNumber("bottom", gridManager.BottomBorderY);
                writer.WriteNumber("player_y", gridManager.PlayerY);
                writer.WriteNumber("space_w", BaseGridMgr.kSpaceWidth);
                writer.WriteNumber("space_h", BaseGridMgr.kSpaceHeight);
                writer.WriteNumber("chunk_w", BaseGridMgr.kChunkWidth);
                writer.WriteNumber("chunk_h", BaseGridMgr.kChunkHeight);
                writer.WriteNumber("chunk_world_w", BaseGridMgr.kChunkWorldWidth);
                writer.WriteNumber("chunk_world_h", BaseGridMgr.kChunkWorldHeight);
                writer.WriteNumber("chunk_cols", BaseGridMgr.kChunkCols);
                writer.WriteNumber("chunk_rows", BaseGridMgr.kChunkRows);
            }
            catch { }
            try
            {
                writer.WriteStartArray("chunks");
                var ch = saveData.BaseChunks;
                if (ch != null)
                    for (int x = 0; x < ch.Length; x++)
                        if (ch[x] != null)
                            for (int y = 0; y < ch[x].Length; y++)
                            {
                                var c = ch[x][y];
                                if (c != null && c.IsPurchased) { writer.WriteStartArray(); writer.WriteNumberValue(c.X); writer.WriteNumberValue(c.Y); writer.WriteEndArray(); }
                            }
                writer.WriteEndArray();
            }
            catch { }
            // 게임의 입구 청크 좌표를 읽는다. IsEntrance 는 타일이 아닌 청크 좌표를 받는다.
            try
            {
                int ex = gridManager.GetEntranceX(), ey = gridManager.GetEntranceY();
                if (gridManager.IsEntrance(ex, ey))
                {
                    writer.WriteStartArray("entrance_chunk");
                    writer.WriteNumberValue(ex);
                    writer.WriteNumberValue(ey);
                    writer.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var bmgr = BuildingMgr.I;
                if (bmgr != null)
                {
                    writer.WriteNumber("worker_speed", bmgr.WorkerMoveSpeed);
                    writer.WriteNumber("worker_speed_mult", bmgr.WorkerMoveSpeedMult);
                    writer.WriteNumber("harvest_len", bmgr.HarvestLength);
                }
                writer.WriteNumber("ball_time_dist", BaseMgr.kBallTimeDist);
            }
            catch { }
            try
            {
                var bp = BasePlayer.I;
                if (bp != null) { writer.WritePropertyName("launcher"); Pt(writer, bp.transform.position); }
            }
            catch { }
            // 화면 대응점: 기지 네 모서리의 화면 좌표 (앱이 월드 ↔ 화면 변환을 만든다)
            try
            {
                writer.WriteStartArray("proj");
                foreach (var (x, y) in new[] { (gridManager.LeftBorderX, gridManager.BottomBorderY), (gridManager.RightBorderX, gridManager.BottomBorderY),
                                               (gridManager.LeftBorderX, gridManager.TopBorderY), (gridManager.RightBorderX, gridManager.TopBorderY),
                                               ((gridManager.LeftBorderX + gridManager.RightBorderX) / 2, (gridManager.BottomBorderY + gridManager.TopBorderY) / 2) })
                {
                    if (!ToScreen(new Vector3(x, y, 0), out var screenX, out var screenY)) continue;
                    writer.WriteStartArray();
                    writer.WriteNumberValue(Math.Round(x, 3)); writer.WriteNumberValue(Math.Round(y, 3));
                    writer.WriteNumberValue((int)screenX); writer.WriteNumberValue((int)screenY);
                    writer.WriteEndArray();
                }
                writer.WriteEndArray();
            }
            catch { }
            // 건물 충돌 모양 (월드 좌표). 60여 개 모양을 매번 읽으면 게임 프레임 시간을 먹으므로
            // 배치 지문(건물 id·위치·방향·상태)이 같으면 지난 결과를 쓰고, 1초마다 한 번은 새로 읽는다.
            try
            {
                long signature = 17;
                var buildings = saveData.Buildings;
                if (buildings != null)
                    for (int index = 0; index < buildings.Count; index++)
                    {
                        var building = buildings[index];
                        if (building == null) continue;
                        signature = signature * 31 + building.Id;
                        signature = signature * 31 + (long)Math.Round(building.X * 100);
                        signature = signature * 31 + (long)Math.Round(building.Y * 100);
                        signature = signature * 31 + building.Rotation;
                        signature = signature * 31 + building.UpgradeLvl;
                        try { signature = signature * 31 + (int)building.CurState; } catch { }
                    }
                float t = Time.realtimeSinceStartup;
                if (signature != _colSig || t - _colAt > 1f || _colJson.Length == 0)
                {
                    using var memoryStream = new MemoryStream();
                    using (var colliderWriter = new Utf8JsonWriter(memoryStream))
                        WriteColliders(colliderWriter);
                    _colJson = Encoding.UTF8.GetString(memoryStream.ToArray());
                    _colSig = signature;
                    _colAt = t;
                }
                writer.WritePropertyName("colliders");
                writer.WriteRawValue(_colJson, true);
            }
            catch { }
            // 날아가는 작업자 (채집 중): 실제 궤적 검증용
            try
            {
                var balls = baseManager.ActiveBalls;
                writer.WriteStartArray("workers");
                if (balls != null)
                    for (int loopIndex = 0; loopIndex < balls.Count; loopIndex++)
                    {
                        var b = balls[loopIndex];
                        if (b == null || !b.IsActive) continue;
                        var currentPosition = b.transform.position;
                        writer.WriteStartArray();
                        writer.WriteNumberValue(currentPosition.x);
                        writer.WriteNumberValue(currentPosition.y);
                        writer.WriteNumberValue(b.AimDir.x);
                        writer.WriteNumberValue(b.AimDir.y);
                        writer.WriteNumberValue(b.Speed);
                        float r = -1;
                        try { var colliders = b.GetComponent<CircleCollider2D>(); if (colliders != null) r = colliders.radius * Math.Abs(b.transform.lossyScale.x); } catch { }
                        writer.WriteNumberValue(r);
                        writer.WriteNumberValue(b.NumBounces);
                        writer.WriteNumberValue(b.HeldResources != null ? b.HeldResources.GetTotalAmount() : 0);
                        writer.WriteNumberValue(b.WInst != null ? (int)b.WInst.Type : -1);
                        writer.WriteEndArray();
                    }
                writer.WriteEndArray();
            }
            catch { }
            writer.WriteEndObject();
        }

        static void WriteColliders(Utf8JsonWriter writer)
        {
            var gridManager = BaseGridMgr.I;
            writer.WriteStartArray();
            var dict = gridManager != null ? gridManager.BuildingColDict : null;
            if (dict != null)
                foreach (var colliders in dict)
                {
                    var collider = colliders.Key;
                    var buildingObject = colliders.Value;
                    if (collider == null || buildingObject == null || buildingObject.Inst == null || !collider.enabled || !collider.gameObject.activeInHierarchy) continue;
                    var currentTransform = collider.transform;
                    writer.WriteStartObject();
                    writer.WriteNumber("id", buildingObject.Inst.Id);
                    writer.WriteBoolean("trigger", collider.isTrigger);
                    var boxCollider = collider.TryCast<BoxCollider2D>();
                    var circ = collider.TryCast<CircleCollider2D>();
                    var poly = collider.TryCast<PolygonCollider2D>();
                    if (boxCollider != null)
                    {
                        writer.WriteString("shape", "box");
                        var currentPosition = boxCollider.offset; var currentPositionH = boxCollider.size * 0.5f;
                        writer.WriteStartArray("pts");
                        Pt(writer, currentTransform.TransformPoint(new Vector3(currentPosition.x - currentPositionH.x, currentPosition.y - currentPositionH.y, 0)));
                        Pt(writer, currentTransform.TransformPoint(new Vector3(currentPosition.x + currentPositionH.x, currentPosition.y - currentPositionH.y, 0)));
                        Pt(writer, currentTransform.TransformPoint(new Vector3(currentPosition.x + currentPositionH.x, currentPosition.y + currentPositionH.y, 0)));
                        Pt(writer, currentTransform.TransformPoint(new Vector3(currentPosition.x - currentPositionH.x, currentPosition.y + currentPositionH.y, 0)));
                        writer.WriteEndArray();
                    }
                    else if (circ != null)
                    {
                        writer.WriteString("shape", "circle");
                        writer.WritePropertyName("c");
                        Pt(writer, currentTransform.TransformPoint(new Vector3(circ.offset.x, circ.offset.y, 0)));
                        var currentPositionSc = currentTransform.lossyScale;
                        writer.WriteNumber("r", circ.radius * Math.Max(Math.Abs(currentPositionSc.x), Math.Abs(currentPositionSc.y)));
                    }
                    else if (poly != null)
                    {
                        writer.WriteString("shape", "poly");
                        writer.WriteStartArray("pts");
                        var pts = poly.points;
                        for (int componentIndex = 0; componentIndex < pts.Length; componentIndex++) Pt(writer, currentTransform.TransformPoint(new Vector3(pts[componentIndex].x + poly.offset.x, pts[componentIndex].y + poly.offset.y, 0)));
                        writer.WriteEndArray();
                    }
                    else
                    {
                        writer.WriteString("shape", "bounds");
                        var bd = collider.bounds;
                        writer.WriteStartArray("pts");
                        Pt(writer, new Vector3(bd.min.x, bd.min.y, 0)); Pt(writer, new Vector3(bd.max.x, bd.min.y, 0));
                        Pt(writer, new Vector3(bd.max.x, bd.max.y, 0)); Pt(writer, new Vector3(bd.min.x, bd.max.y, 0));
                        writer.WriteEndArray();
                    }
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
        }

        /// <summary>전투 상황: 경험치, 적·보스, 보스·융합기 일정 진행, 캐릭터 능력치, 상태 효과. 모두 읽기만 한다.</summary>
        static void WriteBattleExtra(Utf8JsonWriter writer, BattleSaveData battleData)
        {
            try
            {
                writer.WriteNumber("xp", Math.Round(battleData.CurXP, 1));
                writer.WriteNumber("xp_next", StatUtl.GetBattleTgtXP(battleData.UpgradeLvl));
            }
            catch { }
            writer.WriteNumber("boss_turns_elapsed", battleData.NumBossTurnsElapsed);
            writer.WriteNumber("fuser_turns_elapsed", battleData.NumFuserTurnsElapsed);
            writer.WriteNumber("treasures", battleData.NumTreasures);
            writer.WriteNumber("baby_kills", battleData.BabyKills);
            writer.WriteNumber("endless_kills", battleData.NumEndlessKills);
            writer.WriteNumber("fissions", battleData.NumFissionsDone);
            writer.WriteNumber("rows", battleData.NumRows);
            writer.WriteNumber("cols", battleData.NumCols);
            writer.WriteNumber("player_x", Math.Round(battleData.PlayerX, 2));
            try { writer.WriteNumber("enemies", battleData.GetNumActiveEnemies()); } catch { }
            try { writer.WriteNumber("lowest_enemy_y", Math.Round(battleData.GetLowestEnemyY(), 2)); } catch { }
            try
            {
                var pieces = battleData.Pieces;
                if (pieces != null && battleData.HasBossPiece())
                {
                    long hp = 0, max = 0;
                    string type = null;
                    for (int index = 0; index < pieces.Count; index++)
                    {
                        var pc = pieces[index];
                        if (pc == null || !StatUtl.IsBoss(pc.Type)) continue;
                        hp += Math.Max(0, pc.CurHealth);
                        max += Math.Max(0, pc.MaxHealth);
                        type ??= pc.Type.ToString();
                    }
                    if (max > 0)
                    {
                        writer.WriteStartObject("boss");
                        writer.WriteString("type", type);
                        writer.WriteNumber("hp", hp);
                        writer.WriteNumber("max", max);
                        writer.WriteEndObject();
                    }
                }
            }
            catch { }
            try
            {
                var upgradeManager = UpgradeMgr.I;
                if (upgradeManager != null)
                {
                    writer.WriteStartObject("stats");
                    writer.WriteNumber("crit_chance", Math.Round(upgradeManager.CritChance, 3));
                    writer.WriteNumber("crit_mult", Math.Round(upgradeManager.CritMultiplier, 3));
                    writer.WriteNumber("fire_rate", Math.Round(upgradeManager.FireRate, 3));
                    writer.WriteNumber("reload", Math.Round(upgradeManager.ReloadTime, 3));
                    writer.WriteNumber("ball_speed", Math.Round(upgradeManager.BaseSpeed, 3));
                    writer.WriteNumber("move_speed", Math.Round(upgradeManager.MoveSpeed, 3));
                    writer.WriteNumber("damage_reduction", Math.Round(upgradeManager.DamageReduction, 3));
                    writer.WriteNumber("dodge", Math.Round(upgradeManager.DodgeChance, 3));
                    writer.WriteNumber("thorns", upgradeManager.ThornsAmt);
                    writer.WriteNumber("health_per_kill", upgradeManager.HealthPerKill);
                    writer.WriteNumber("pickup_range", Math.Round(upgradeManager.PickupRange, 3));
                    writer.WriteNumber("bonus_xp", Math.Round(upgradeManager.BonusXPDropped, 3));
                    writer.WriteNumber("bonus_gold", Math.Round(upgradeManager.BonusGoldDropped, 3));
                    writer.WriteNumber("ball_damage_mult", Math.Round(upgradeManager.BallDamageMult, 3));
                    writer.WriteNumber("bonus_ball_damage", upgradeManager.BonusHeroDamage);
                    writer.WriteNumber("babies", upgradeManager.NumFollowers);
                    writer.WriteNumber("multi_balls", upgradeManager.NumMultiHeroes);
                    writer.WriteEndObject();
                }
            }
            catch { }
            try
            {
                var character = battleData.CurChar;
                if (character != null && character.Stats != null)
                {
                    writer.WriteStartArray("char_stats");   // 게임 StatType 순서: 체력·힘·통솔·속도·민첩·지능
                    for (int loopIndex = 0; loopIndex < character.Stats.Length; loopIndex++) writer.WriteNumberValue(character.Stats[loopIndex]);
                    writer.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var eff = battleData.PlayerStatusEffects;
                writer.WriteStartArray("effects");
                if (eff != null)
                    for (int loopIndex = 0; loopIndex < eff.Count; loopIndex++)
                    {
                        var e = eff[loopIndex];
                        if (e == null) continue;
                        writer.WriteStartObject();
                        writer.WriteString("type", e.Type.ToString());
                        writer.WriteNumber("left", Math.Round(e.RemainingLen, 1));
                        writer.WriteEndObject();
                    }
                writer.WriteEndArray();
            }
            catch { }
        }

        /// <summary>이번 런에서 이 볼이 준 피해·처치 (게임의 런 종료 통계와 같은 값).</summary>
        static void WriteHeroStats(Utf8JsonWriter writer, HeroInst h)
        {
            try
            {
                var sd = h.StatData;
                if (sd == null) return;
                long dmg = 0, kills = 0, launches = 0, bounce = 0, status = 0, alt = 0, aoe = 0;
                for (int componentIndex = 0; componentIndex < sd.Count; componentIndex++)
                {
                    var s = sd[componentIndex];
                    if (s == null) continue;
                    bounce += s.BounceDmgDealt;
                    status += s.StatusEffectDmgDealt;
                    alt += s.AltDmgDealt;
                    aoe += s.AOEDmgDealt;
                    kills += s.NumKills;
                    launches += s.NumLaunches;
                }
                dmg = bounce + status + alt + aoe;
                writer.WriteNumber("dmg", dmg);
                writer.WriteStartObject("dmg_by");
                writer.WriteNumber("bounce", bounce);
                writer.WriteNumber("status", status);
                writer.WriteNumber("other", alt);
                writer.WriteNumber("aoe", aoe);
                writer.WriteEndObject();
                writer.WriteNumber("kills", kills);
                writer.WriteNumber("launches", launches);
            }
            catch { }
        }

        static void WriteChoiceList(Utf8JsonWriter writer, string name, Il2CppSystem.Collections.Generic.List<UpgradeChoice> list)
        {
            writer.WriteStartArray(name);
            if (list != null)
                for (int index = 0; index < list.Count; index++) WriteInfoTypeValue(writer, list[index].Info);
            writer.WriteEndArray();
        }

        static void WriteLevelUp(Utf8JsonWriter writer)
        {
            var levelUpUI = LevelUpUI.I;
            bool open = levelUpUI != null && levelUpUI.gameObject.activeInHierarchy && levelUpUI.IsActiveOverlay();
            if (!open)
            {
                writer.WriteNull("levelup");
                return;
            }
            writer.WriteStartObject("levelup");
            writer.WriteString("type", levelUpUI.Type.ToString());
            writer.WriteString("page", levelUpUI.CurPage.ToString());
            writer.WriteNumber("reroll_cost", levelUpUI._rerollCost);
            WriteRect(writer, "panel", levelUpUI.PanelMain);

            writer.WriteStartArray("choices");
            var choices = levelUpUI._choices;
            var btns = levelUpUI.Btns;
            if (choices != null)
            {
                for (int index = 0; index < choices.Count; index++)
                {
                    var c = choices[index];
                    writer.WriteStartObject();
                    writer.WriteNumber("idx", index);
                    writer.WriteString("kind", c.Type.ToString());
                    writer.WriteBoolean("is_new", c.IsNew);
                    writer.WriteNumber("equip_idx", c.EquipmentIdx);
                    var info = c.Info;
                    if (info != null)
                    {
                        writer.WriteString("slug", info.Slug);
                        var hero = info.TryCast<HeroInfo>();
                        var passive = info.TryCast<PassiveInfo>();
                        if (hero != null) writer.WriteString("type", hero.Type.ToString());
                        else if (passive != null) writer.WriteString("type", passive.Type.ToString());
                        try { writer.WriteBoolean("ai_pick", info.ShouldAIPick()); } catch { }
                        if (hero != null) WriteSynergy(writer, hero);
                        if (hero == null && passive == null)
                        {
                            // 펫 강화 등: 게임 번역 표에서 현재 언어 이름·설명을 그대로 가져온다
                            var pet = info.TryCast<PetUpgradeInfo>();
                            if (pet != null) writer.WriteString("type", pet.Type.ToString());
                            writer.WriteString("name_loc", Loc(info.GetNameSlug()));
                            writer.WriteString("desc_loc", Loc(info.GetDescSlug()));
                        }
                    }
                    var choiceButton = FindButton(btns, index);
                    if (choiceButton != null)
                    {
                        writer.WriteNumber("tgt_lvl", choiceButton.TgtLvl);
                        EffectiveProperties.Write(writer, info, choiceButton.TgtLvl, c.IsNew, c.EquipmentIdx);
                        WriteRect(writer, "rect", choiceButton.Xfm);
                    }
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();
            // 다음 선택지가 뽑히는 후보 (새로고침 확률 추정용). 직전 선택지는 다음 새로고침에서 빠진다.
            writer.WriteStartObject("pool");
            try
            {
                WriteChoiceList(writer, "new_balls", levelUpUI._availNewHeroes);
                WriteChoiceList(writer, "ball_upgrades", levelUpUI._availHeroUpgrades);
                WriteChoiceList(writer, "new_passives", levelUpUI._availNewPassives);
                WriteChoiceList(writer, "passive_upgrades", levelUpUI._availPassiveUpgrades);
                WriteChoiceList(writer, "prev", levelUpUI._prevChoices);
                writer.WriteNumber("num_choices", levelUpUI._numUpgradeChoices);
                writer.WriteBoolean("banishing", levelUpUI._isBanishing);
            }
            catch { }
            writer.WriteEndObject();
            WriteFuser(writer, levelUpUI);
            writer.WriteEndObject();
        }

        /// <summary>선택지 볼과 게임이 '시너지'로 판정하는 보유 볼 (게임의 카드 설명 '시너지 장비'와 같은 판정).</summary>
        static void WriteSynergy(Utf8JsonWriter writer, HeroInfo choice)
        {
            var battleData = BattleSaveData.I;
            if (battleData == null || battleData.Heroes == null) return;
            writer.WriteStartArray("synergy");
            for (int index = 0; index < battleData.Heroes.Count; index++)
            {
                var h = battleData.Heroes[index];
                if (h == null) continue;
                try
                {
                    var owned = h.GetInfo();
                    if (owned != null && (choice.HasSynergy(owned) || owned.HasSynergy(choice)))
                        writer.WriteStringValue(h.Type.ToString());
                }
                catch { }
            }
            writer.WriteEndArray();
        }

        /// <summary>융합 화면: 지금 고를 수 있는 진화와 융합 조합. 게임 자동 선택 AI의 조합 점수도 함께 보낸다.</summary>
        static void WriteFuser(Utf8JsonWriter writer, LevelUpUI levelUpUI)
        {
            writer.WriteStartObject("fuser");
            try { writer.WriteBoolean("free_upgrades", levelUpUI.HasFreeUpgrades()); } catch { }
            writer.WriteStartArray("options");
            var opts = levelUpUI._fusionChoices;
            if (opts != null)
                for (int index = 0; index < opts.Count; index++) writer.WriteStringValue(((FuserOptionType)opts[index]).ToString());
            writer.WriteEndArray();

            writer.WriteStartArray("evos");
            var merges = levelUpUI._availMerges;
            if (merges != null)
            {
                for (int loopIndex = 0; loopIndex < merges.Count; loopIndex++)
                {
                    var m = merges[loopIndex];
                    writer.WriteStartObject();
                    writer.WriteNumber("idx", loopIndex);
                    writer.WriteNumber("equip_idx", m.EquipmentIdx);
                    writer.WriteNumber("evo_idx", m.EvoIdx);
                    WriteInfoType(writer, "type", m.Info);
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();

            writer.WriteStartArray("combos");
            var combos = levelUpUI._availHCombos;
            var heroes = BattleSaveData.I != null ? BattleSaveData.I.Heroes : null;
            if (combos != null)
            {
                for (int loopIndex = 0; loopIndex < combos.Count; loopIndex++)
                {
                    var hc = combos[loopIndex];
                    writer.WriteStartObject();
                    writer.WriteNumber("idx", loopIndex);
                    writer.WriteString("h1", hc.H1.ToString());
                    writer.WriteString("h2", hc.H2.ToString());
                    writer.WriteNumber("idx1", hc.Idx1);
                    writer.WriteNumber("idx2", hc.Idx2);
                    try { writer.WriteNumber("ai_score", levelUpUI.GetComboScore(loopIndex, hc)); } catch { }
                    try
                    {
                        if (heroes != null && hc.Idx1 >= 0 && hc.Idx2 >= 0 && hc.Idx1 < heroes.Count && hc.Idx2 < heroes.Count)
                            writer.WriteBoolean("bad", heroes[hc.Idx1].IsBadCombo(heroes[hc.Idx2]));
                    }
                    catch { }
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        /// <summary>게임 번역 표에서 현재 게임 언어 문장을 읽는다 (읽기만 함). 실패하면 빈 문자열.</summary>
        internal static string Loc(string term)
        {
            if (string.IsNullOrEmpty(term)) return "";
            try
            {
                var typeName = I2.Loc.LocalizationManager.GetTranslation(term, true, 0, true, false, null, null, true);
                return typeName ?? "";
            }
            catch { return ""; }
        }

        internal static void WriteInfoTypeValue(Utf8JsonWriter writer, UpgradeInfo info)
        {
            if (info == null) return;
            var hero = info.TryCast<HeroInfo>();
            if (hero != null) { writer.WriteStringValue(hero.Type.ToString()); return; }
            var passive = info.TryCast<PassiveInfo>();
            if (passive != null) { writer.WriteStringValue(passive.Type.ToString()); return; }
            writer.WriteStringValue(info.Slug);
        }

        internal static void WriteInfoType(Utf8JsonWriter writer, string name, UpgradeInfo info)
        {
            if (info == null) { writer.WriteNull(name); return; }
            var hero = info.TryCast<HeroInfo>();
            if (hero != null) { writer.WriteString(name, hero.Type.ToString()); return; }
            var passive = info.TryCast<PassiveInfo>();
            if (passive != null) { writer.WriteString(name, passive.Type.ToString()); return; }
            writer.WriteString(name, info.Slug);
        }

        /// <summary>게임 안 레시피 표 (진화 재료). 연결될 때 한 번 보낸다.</summary>
        public static string BuildCatalog()
        {
            var database = InfoDB.I;
            if (database == null) return null;
            using var memoryStream = new MemoryStream();
            using (var writer = new Utf8JsonWriter(memoryStream))
            {
                writer.WriteStartObject();
                writer.WriteNumber("v", PipeServer.ProtocolVersion);
                writer.WriteString("plugin", Plugin.Version);
                writer.WriteString("game_version", Application.version);
                writer.WriteStartObject("catalog");
                try { writer.WriteNumber("max_solo_lvl", UpgradeInst<HeroInfo>.kMaxSoloLvl); } catch { }
                WriteInfos(writer, "balls", database.Heroes);
                WriteInfos(writer, "passives", database.Passives);
                writer.WriteStartArray("levels");
                try
                {
                    var lvls = database.Levels;
                    if (lvls != null)
                        for (int index = 0; index < lvls.Length; index++)
                        {
                            var li = lvls[index];
                            if (li == null) continue;
                            writer.WriteStartObject();
                            writer.WriteString("type", li.Type.ToString());
                            writer.WriteStartArray("boss_turns");
                            if (li.BossTurns != null) for (int componentIndex = 0; componentIndex < li.BossTurns.Length; componentIndex++) writer.WriteNumberValue(li.BossTurns[componentIndex]);
                            writer.WriteEndArray();
                            writer.WriteStartArray("fuser_turns");
                            if (li.FuserTurns != null) for (int partIndex = 0; partIndex < li.FuserTurns.Length; partIndex++) writer.WriteNumberValue(li.FuserTurns[partIndex]);
                            writer.WriteEndArray();
                            writer.WriteNumber("turn_len", Math.Round(li.DefaultTurnLength, 3));
                            writer.WriteEndObject();
                        }
                }
                catch { }
                writer.WriteEndArray();
                writer.WriteEndObject();
                writer.WriteEndObject();
            }
            return Encoding.UTF8.GetString(memoryStream.ToArray());
        }

        static void WriteInfos<T>(Utf8JsonWriter writer, string name, Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<T> infos)
            where T : UpgradeInfo
        {
            writer.WriteStartArray(name);
            if (infos != null)
            {
                for (int index = 0; index < infos.Length; index++)
                {
                    var info = infos[index];
                    if (info == null) continue;
                    writer.WriteStartObject();
                    WriteInfoType(writer, "type", info);
                    writer.WriteBoolean("in_game", info.IsInGame);
                    WriteLevelProps(writer, info);
                    writer.WriteStartArray("recipes");
                    var comps = info.MergeComponents;
                    if (comps != null)
                    {
                        for (int r = 0; r < comps.Length; r++)
                        {
                            var recipe = comps[r];
                            if (recipe == null) continue;
                            writer.WriteStartArray();
                            for (int componentIndex = 0; componentIndex < recipe.Length; componentIndex++)
                            {
                                var part = recipe[componentIndex];
                                if (part == null) continue;
                                var hero = part.TryCast<HeroInfo>();
                                var passive = part.TryCast<PassiveInfo>();
                                writer.WriteStringValue(hero != null ? hero.Type.ToString()
                                    : passive != null ? passive.Type.ToString() : part.Slug);
                            }
                            writer.WriteEndArray();
                        }
                    }
                    writer.WriteEndArray();
                    writer.WriteEndObject();
                }
            }
            writer.WriteEndArray();
        }

        /// <summary>레벨별 수치 (피해 최소·최대, 상태 이상 피해 등). 게임 PropertiesByLvl 그대로.</summary>
        static void WriteLevelProps(Utf8JsonWriter writer, UpgradeInfo info)
        {
            try
            {
                var byLvl = info.PropertiesByLvl;
                if (byLvl == null) return;
                writer.WriteStartArray("lvl_props");
                for (int l = 0; l < byLvl.Length; l++)
                {
                    writer.WriteStartObject();
                    var d = byLvl[l];
                    if (d != null)
                        foreach (var kv in d) writer.WriteNumber(kv.Key.ToString(), kv.Value);
                    writer.WriteEndObject();
                }
                writer.WriteEndArray();
            }
            catch { }
        }

        static LevelUpBtn FindButton(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<LevelUpBtn> btns, int choiceIdx)
        {
            if (btns == null) return null;
            for (int index = 0; index < btns.Length; index++)
            {
                var choiceButton = btns[index];
                if (choiceButton != null && choiceButton.gameObject.activeInHierarchy && choiceButton.ChoiceIdx == choiceIdx) return choiceButton;
            }
            return null;
        }

        static readonly Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector3> Corners =
            new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector3>(4);

        /// <summary>UI 요소의 화면 사각형 (게임 클라이언트 영역 픽셀, 왼쪽 위 원점).</summary>
        /// <summary>1.11: 지금 화면에서 도우미 HUD 가 가리면 안 되는 게임 UI 영역 (글자·버튼·목록). 켜져 있는 것만.</summary>
        static void WriteUiAvoid(Utf8JsonWriter writer)
        {
            writer.WriteStartObject("ui");
            try
            {
                var characterSelection = CharSelectUI.I;
                if (characterSelection != null && characterSelection.gameObject.activeInHierarchy)
                {
                    writer.WriteString("screen", "char_select");
                    writer.WriteBoolean("fusion", characterSelection.IsSelectingFusion);
                    writer.WriteStartArray("avoid");
                    RectIfActive(writer, characterSelection.CharGrid);           // 캐릭터 목록 (이름표 포함)
                    RectIfActive(writer, characterSelection.WrapperList);
                    RectIfActive(writer, characterSelection.DetailsPanel);       // 선택한 캐릭터 설명
                    RectIfActive(writer, characterSelection.WrapperDetailsBtns);
                    RectIfActive(writer, characterSelection.BtnSelect);
                    RectIfActive(writer, characterSelection.BtnClose);
                    RectIfActive(writer, characterSelection.WrapperLvlItems);
                    writer.WriteEndArray();
                }
                else
                {
                    var levelUpUI = LevelUpUI.I;
                    if (levelUpUI != null && levelUpUI.gameObject.activeInHierarchy && levelUpUI.IsActiveOverlay())
                    {
                        writer.WriteString("screen", levelUpUI.Type.ToString() == "kFuser" ? "fuser" : "levelup");
                        writer.WriteStartArray("avoid");
                        RectIfActive(writer, levelUpUI.PanelSelectionDetails);   // 오른쪽 설명 패널
                        RectIfActive(writer, levelUpUI.WrapperCurHeroes);        // 볼 슬롯
                        RectIfActive(writer, levelUpUI.WrapperCurPassives);      // 패시브 슬롯
                        RectIfActive(writer, levelUpUI.BtnReroll);
                        RectIfActive(writer, levelUpUI.BtnBanish);
                        RectIfActive(writer, levelUpUI.WrapperFuserOptions);     // 융합 선택지
                        RectIfActive(writer, levelUpUI.WrapperSelectEvo);
                        RectIfActive(writer, levelUpUI.WrapperSelectCombo);
                        RectIfActive(writer, levelUpUI.EvoSelectInfoPanel);
                        writer.WriteEndArray();
                    }
                }
            }
            catch { }
            writer.WriteEndObject();
        }

        static void RectIfActive(Utf8JsonWriter writer, GameObject go)
        {
            if (go != null) RectIfActive(writer, go.transform);
        }

        static void RectIfActive(Utf8JsonWriter writer, Component comp)
        {
            if (comp == null || !comp.gameObject.activeInHierarchy) return;
            var rectTransform = comp.TryCast<RectTransform>() ?? comp.GetComponent<RectTransform>();
            if (rectTransform == null) return;
            rectTransform.GetWorldCorners(Corners);
            var canvas = rectTransform.GetComponentInParent<Canvas>();
            Camera camera = null;
            if (canvas != null)
            {
                canvas = canvas.rootCanvas;
                if (canvas.renderMode != RenderMode.ScreenSpaceOverlay) camera = canvas.worldCamera;
            }
            float x0 = float.MaxValue, y0 = float.MaxValue, x1 = float.MinValue, y1 = float.MinValue;
            for (int index = 0; index < 4; index++)
            {
                var currentPosition = RectTransformUtility.WorldToScreenPoint(camera, Corners[index]);
                x0 = Math.Min(x0, currentPosition.x); x1 = Math.Max(x1, currentPosition.x);
                y0 = Math.Min(y0, currentPosition.y); y1 = Math.Max(y1, currentPosition.y);
            }
            if (x1 - x0 < 2 || y1 - y0 < 2) return;
            int currentHeight = Screen.height;
            writer.WriteStartArray();
            writer.WriteNumberValue((int)Math.Round(x0));
            writer.WriteNumberValue((int)Math.Round(currentHeight - y1));
            writer.WriteNumberValue((int)Math.Round(x1 - x0));
            writer.WriteNumberValue((int)Math.Round(y1 - y0));
            writer.WriteEndArray();
        }

        static void WriteRect(Utf8JsonWriter writer, string name, Component comp)
        {
            if (comp == null) return;
            var rectTransform = comp.TryCast<RectTransform>() ?? comp.GetComponent<RectTransform>();
            if (rectTransform == null) return;
            rectTransform.GetWorldCorners(Corners);
            var canvas = rectTransform.GetComponentInParent<Canvas>();
            Camera camera = null;
            if (canvas != null)
            {
                canvas = canvas.rootCanvas;
                if (canvas.renderMode != RenderMode.ScreenSpaceOverlay) camera = canvas.worldCamera;
            }
            float x0 = float.MaxValue, y0 = float.MaxValue, x1 = float.MinValue, y1 = float.MinValue;
            for (int index = 0; index < 4; index++)
            {
                var currentPosition = RectTransformUtility.WorldToScreenPoint(camera, Corners[index]);
                x0 = Math.Min(x0, currentPosition.x); x1 = Math.Max(x1, currentPosition.x);
                y0 = Math.Min(y0, currentPosition.y); y1 = Math.Max(y1, currentPosition.y);
            }
            int currentHeight = Screen.height;
            writer.WriteStartArray(name);
            writer.WriteNumberValue((int)Math.Round(x0));
            writer.WriteNumberValue((int)Math.Round(currentHeight - y1));
            writer.WriteNumberValue((int)Math.Round(x1 - x0));
            writer.WriteNumberValue((int)Math.Round(y1 - y0));
            writer.WriteEndArray();
        }
    }
    /// <summary>기지·누적 기록 (런 밖에서도). 5초마다 읽고 바뀌었을 때만 보낸다.</summary>
    internal static class Meta
    {
        public static string Build()
        {
            var saveData = MetaSaveData.I;
            if (saveData == null) return null;
            using var memoryStream = new MemoryStream();
            using (var writer = new Utf8JsonWriter(memoryStream))
            {
                writer.WriteStartObject();
                writer.WriteNumber("v", PipeServer.ProtocolVersion);
                writer.WriteString("plugin", Plugin.Version);
                writer.WriteStartObject("meta");
                WriteInts(writer, "resources", saveData.NumResources);
                writer.WriteNumber("day", saveData.CurDay);
                writer.WriteNumber("battles", saveData.NumBattlesPlayed);
                writer.WriteNumber("boss_waves", saveData.NumBossWavesCompleted);
                writer.WriteStartObject("lifetime");
                writer.WriteNumber("kills", saveData.NumKills);
                writer.WriteNumber("play_time", Math.Round(saveData.PlayTime));
                writer.WriteNumber("harvests", saveData.NumHarvests);
                writer.WriteNumber("elevator", saveData.ElevatorLvl);
                writer.WriteNumber("buildings_built", saveData.NumBuildingsConstructed);
                writer.WriteNumber("boss_blueprints", saveData.NumBossBlueprintsDropped);
                writer.WriteEndObject();
                var buildingManager = BuildingMgr.I;
                if (buildingManager != null)
                {
                    writer.WriteStartObject("bonuses");
                    writer.WriteNumber("banishes", buildingManager.NumBanishes);
                    writer.WriteNumber("free_rerolls", buildingManager.NumFreeRerolls);
                    writer.WriteNumber("revives", buildingManager.NumRevives);
                    writer.WriteNumber("choices", buildingManager.NumLvlUpChoices);
                    writer.WriteNumber("ball_slots", buildingManager.NumBallSlots);
                    writer.WriteNumber("passive_slots", buildingManager.NumPassiveSlots);
                    writer.WriteBoolean("endless", buildingManager.EndlessModeUnlocked);
                    writer.WriteEndObject();
                }
                WriteBuildings(writer, saveData);
                WriteHeroStats(writer, saveData);
                WritePassiveStats(writer, saveData);
                WriteDiscovery(writer, saveData);
                if (saveData.CharWorkerOrder != null)
                {
                    writer.WriteStartArray("worker_order");
                    for (int index = 0; index < saveData.CharWorkerOrder.Count; index++)
                        writer.WriteStringValue(saveData.CharWorkerOrder[index].ToString());
                    writer.WriteEndArray();
                }
                writer.WriteStartArray("chars");
                var chars = saveData.Chars;
                if (chars != null)
                    for (int loopIndex = 0; loopIndex < chars.Length; loopIndex++)
                    {
                        var character = chars[loopIndex];
                        if (character == null || !character.IsUnlocked) continue;
                        writer.WriteStartObject();
                        writer.WriteString("type", character.Type.ToString());
                        writer.WriteNumber("lvl", character.Lvl);
                        writer.WriteNumber("battles", character.NumBattles);
                        try { writer.WriteString("state", character.CurState.ToString()); } catch { }
                        try { writer.WriteString("work", character.WorkerBuildingType.ToString()); writer.WriteNumber("work_id", character.WorkerBuildingId); } catch { }
                        try
                        {
                            var hu = character.HarvestUpgrades;
                            if (hu != null && hu.Count > 0)
                            {
                                writer.WriteStartObject("harvest");
                                for (int componentIndex = 0; componentIndex < hu.Count; componentIndex++) if (hu[componentIndex] != null) writer.WriteNumber(hu[componentIndex].Type.ToString(), hu[componentIndex].Lvl);
                                writer.WriteEndObject();
                            }
                        }
                        catch { }
                        // 강화 레벨과 실제 효과 값을 구분한다. 없는 강화의 기본 반환값도 게임에서 직접 읽는다.
                        writer.WriteStartObject("harvest_bonus");
                        foreach (HarvestUpgradeType type in Enum.GetValues(typeof(HarvestUpgradeType)))
                        {
                            if (type.ToString() == "kNum") continue;
                            try { writer.WriteNumber(type.ToString(), character.GetHarvestUpgradeBonusAmt(type)); }
                            catch { } // 읽을 수 없는 항목은 0을 만들어 보내지 않는다.
                        }
                        writer.WriteEndObject();
                        try
                        {
                            if (character.BonusStats != null)
                            {
                                writer.WriteStartArray("bonus_stats");
                                for (int partIndex = 0; partIndex < character.BonusStats.Length; partIndex++) writer.WriteNumberValue(character.BonusStats[partIndex]);
                                writer.WriteEndArray();
                            }
                        }
                        catch { }
                        writer.WriteEndObject();
                    }
                writer.WriteEndArray();
                WriteLevels(writer, saveData);
                writer.WriteEndObject();
                writer.WriteEndObject();
            }
            return Encoding.UTF8.GetString(memoryStream.ToArray());
        }

        /// <summary>지역별: 해금·완료, 무한의 심연 최고 기록, 이 지역을 깬 캐릭터, 아직 못 얻은 설계도.</summary>
        static void WriteLevels(Utf8JsonWriter writer, MetaSaveData saveData)
        {
            var have = new HashSet<int>();
            var bps = saveData.Blueprints;
            if (bps != null)
                for (int index = 0; index < bps.Length; index++)
                    if (bps[index] != null && bps[index].HasBlueprint) have.Add((int)bps[index].TgtBuilding);
            var database = InfoDB.I;
            writer.WriteStartArray("levels");
            var lv = saveData.LvlData;
            if (lv != null)
                for (int loopIndex = 0; loopIndex < lv.Length; loopIndex++)
                {
                    var d = lv[loopIndex];
                    if (d == null) continue;
                    writer.WriteStartObject();
                    writer.WriteString("type", d.Type.ToString());
                    try { writer.WriteString("name", d.GetInfo().GetNameTranslation(false)); } catch { }
                    try { writer.WriteBoolean("unlocked", d.IsUnlocked()); } catch { }
                    writer.WriteBoolean("done", d.DidComplete);
                    writer.WriteNumber("attempts", d.NumAttempts);
                    writer.WriteNumber("best_endless", d.BestEndlessDepth);
                    writer.WriteNumber("blueprint_attempts", d.NumBlueprintDropAttempts);
                    // 캐릭터별 최고 난이도·시도 횟수 원본 (배열 위치 = CharType). DidCompleteWithChar 는 실제 화면과 달랐다.
                    try
                    {
                        if (d.BestDifficultyByChar != null)
                        {
                            writer.WriteStartArray("best_diff_by_char");
                            for (int componentIndex = 0; componentIndex < d.BestDifficultyByChar.Length; componentIndex++) writer.WriteNumberValue(d.BestDifficultyByChar[componentIndex]);
                            writer.WriteEndArray();
                        }
                        if (d.NumAttemptsByChar != null)
                        {
                            writer.WriteStartArray("attempts_by_char");
                            for (int partIndex = 0; partIndex < d.NumAttemptsByChar.Length; partIndex++) writer.WriteNumberValue(d.NumAttemptsByChar[partIndex]);
                            writer.WriteEndArray();
                        }
                    }
                    catch { }
                    writer.WriteStartArray("blueprints_left");
                    try
                    {
                        var byLvl = database != null ? database.BlueprintsByLevel : null;
                        int li = (int)d.Type;
                        if (byLvl != null && li >= 0 && li < byLvl.Length && byLvl[li] != null)
                            for (int b = 0; b < byLvl[li].Count; b++)
                            {
                                var info = byLvl[li][b];
                                if (info != null && !have.Contains((int)info.Type)) writer.WriteStringValue(info.Type.ToString());
                            }
                    }
                    catch { }
                    writer.WriteEndArray();
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
        }

        static void WriteInts(Utf8JsonWriter writer, string name, Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<int> a)
        {
            writer.WriteStartArray(name);
            if (a != null) for (int index = 0; index < a.Length; index++) writer.WriteNumberValue(a[index]);
            writer.WriteEndArray();
        }

        static void WriteCost(Utf8JsonWriter writer, string name, Cost currentCost)
        {
            if (currentCost == null) return;
            WriteInts(writer, name, currentCost.Num);
        }

        static void WriteBuildings(Utf8JsonWriter writer, MetaSaveData saveData)
        {
            var built = new HashSet<int>();
            writer.WriteStartArray("buildings");
            var list = saveData.Buildings;
            if (list != null)
                for (int index = 0; index < list.Count; index++)
                {
                    var building = list[index];
                    if (building == null) continue;
                    built.Add((int)building.Type);
                    writer.WriteStartObject();
                    writer.WriteString("type", building.Type.ToString());
                    writer.WriteNumber("lvl", building.UpgradeLvl);
                    writer.WriteString("state", building.CurState.ToString());
                    try
                    {
                        bool can = building.CanBeUpgraded();
                        writer.WriteBoolean("can_upgrade", can);
                        if (can) WriteCost(writer, "upgrade_cost", building.GetUpgradeCost());
                    }
                    catch { }
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
            // 설계도는 있는데 아직 안 지은 건물 + 짓는 비용
            var infos = new Dictionary<int, BuildingInfo>();
            var database = InfoDB.I;
            if (database != null && database.Buildings != null)
                for (int loopIndex = 0; loopIndex < database.Buildings.Length; loopIndex++)
                    if (database.Buildings[loopIndex] != null) infos[(int)database.Buildings[loopIndex].Type] = database.Buildings[loopIndex];
            writer.WriteStartArray("blueprints");
            var bps = saveData.Blueprints;
            if (bps != null)
                for (int loopIndex = 0; loopIndex < bps.Length; loopIndex++)
                {
                    var bp = bps[loopIndex];
                    if (bp == null || !bp.HasBlueprint || built.Contains((int)bp.TgtBuilding)) continue;
                    writer.WriteStartObject();
                    writer.WriteString("type", bp.TgtBuilding.ToString());
                    if (infos.TryGetValue((int)bp.TgtBuilding, out var info))
                    {
                        writer.WriteString("slug", info.Slug);
                        writer.WriteString("cat", info.Cat.ToString());
                        WriteCost(writer, "cost", info.BuildCost);
                        try { writer.WriteNumber("tw", info.TileSize.x); writer.WriteNumber("th", info.TileSize.y); } catch { }
                        try { writer.WriteString("col", info.ColType.ToString()); } catch { }
                        try { writer.WriteString("stat", info.GetStatBonus().ToString()); } catch { }
                    }
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
            // 이미 지은 반복 건설형도 포함한다. 원래 blueprints 는 미건설 설계도 목록으로 유지한다.
            // 비용·추가 건설 가능 여부는 게임 getter 결과를 그대로 읽는다.
            writer.WriteStartArray("build_options");
            if (bps != null)
                for (int loopIndex = 0; loopIndex < bps.Length; loopIndex++)
                {
                    var bp = bps[loopIndex];
                    if (bp == null || !bp.HasBlueprint || !infos.TryGetValue((int)bp.TgtBuilding, out var info)) continue;
                    bool included, more;
                    Cost cost;
                    try { included = info.IsInGame && info.IncludeInGame(); more = info.CanBuildMore(); cost = info.GetCost(); }
                    catch { continue; }
                    if (!included || !more) continue;
                    writer.WriteStartObject();
                    writer.WriteString("type", info.Type.ToString());
                    writer.WriteString("slug", info.Slug);
                    writer.WriteString("cat", info.Cat.ToString());
                    writer.WriteBoolean("can_build_more", more);
                    WriteCost(writer, "cost", cost);
                    WriteCost(writer, "base_cost", info.BuildCost);
                    writer.WriteNumber("tw", info.TileSize.x);
                    writer.WriteNumber("th", info.TileSize.y);
                    try { writer.WriteNumber("max_instances", info.GetMaxBuildingInst()); } catch { }
                    PhysicsSnapshot.WriteRange(writer, info, info.TileSize, 0);
                    try { if (cost != null) writer.WriteBoolean("affordable", cost.CanAfford()); } catch { }
                    writer.WriteEndObject();
                }
            writer.WriteEndArray();
        }

        static void WriteHeroStats(Utf8JsonWriter writer, MetaSaveData saveData)
        {
            writer.WriteStartObject("ball_stats");
            var a = saveData.HeroStats;
            if (a != null)
                for (int index = 0; index < a.Length; index++)
                {
                    var s = a[index];
                    if (s == null || (s.NumObtained == 0 && s.NumNewRejected == 0)) continue;
                    writer.WriteStartObject(((HeroType)index).ToString());
                    writer.WriteNumber("obtained", s.NumObtained);
                    writer.WriteNumber("upgraded", s.NumUpgraded);
                    writer.WriteNumber("rejected", s.NumNewRejected);
                    writer.WriteNumber("completed", s.NumCompletedRuns);
                    writer.WriteNumber("damage", s.TotalDamage);
                    writer.WriteNumber("launches", s.TotalLaunches);
                    writer.WriteEndObject();
                }
            writer.WriteEndObject();
        }

        /// <summary>등장 가능 여부와 백과사전 발견 여부를 별도로 읽는다. 0회도 명시해 미확인과 구분한다.</summary>
        static void WriteDiscovery(Utf8JsonWriter writer, MetaSaveData saveData)
        {
            var heroes = InfoDB.I?.Heroes;
            var stats = saveData.HeroStats;
            if (heroes == null || stats == null) return;
            writer.WriteStartObject("discovery");
            for (int index = 0; index < heroes.Length; index++)
            {
                var info = heroes[index];
                if (info == null) continue;
                int indexIdx = (int)info.Type;
                if (indexIdx < 0 || indexIdx >= stats.Length || stats[indexIdx] == null) continue;
                var stat = stats[indexIdx];
                writer.WriteStartObject(info.Type.ToString());
                writer.WriteNumber("obtained", stat.NumObtained);
                writer.WriteBoolean("in_game", info.IncludeInGame());
                writer.WriteBoolean("available", info.IsUnlocked());
                writer.WriteBoolean("merged", info.IsMerged());
                var combos = stat.NumCombos;
                if (combos != null)
                {
                    writer.WriteStartObject("combos");
                    for (int otherIndex = 0; otherIndex < combos.Length; otherIndex++)
                        if (combos[otherIndex] > 0) writer.WriteNumber(((HeroType)otherIndex).ToString(), combos[otherIndex]);
                    writer.WriteEndObject();
                }
                writer.WriteEndObject();
            }
            writer.WriteEndObject();
        }

        static void WritePassiveStats(Utf8JsonWriter writer, MetaSaveData saveData)
        {
            writer.WriteStartObject("passive_stats");
            var a = saveData.PassiveStats;
            if (a != null)
                for (int index = 0; index < a.Length; index++)
                {
                    var s = a[index];
                    if (s == null || (s.NumObtained == 0 && s.NumNewRejected == 0)) continue;
                    writer.WriteStartObject(((PassiveType)index).ToString());
                    writer.WriteNumber("obtained", s.NumObtained);
                    writer.WriteNumber("upgraded", s.NumUpgraded);
                    writer.WriteNumber("rejected", s.NumNewRejected);
                    writer.WriteNumber("completed", s.NumCompletedRuns);
                    writer.WriteEndObject();
                }
            writer.WriteEndObject();
        }
    }
}
