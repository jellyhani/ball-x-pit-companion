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
        public const string Version = "1.13.1";   // 도우미 앱이 이 값으로 설치된 플러그인이 최신인지 확인한다
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
            catch (Exception e)
            {
                Plugin.L?.LogWarning("파이프 권한 지정 실패, 기본 권한 사용: " + e.Message);
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
                catch (Exception e)
                {
                    Plugin.L?.LogWarning("파이프 오류: " + e.Message);
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
                catch (Exception e) { if (_loggedErrors.Add("catalog" + e.Message)) Plugin.L.LogWarning("레시피 표 실패: " + e); }
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
                catch (Exception e)
                {
                    if (_loggedErrors.Add("meta" + e.GetType().Name + e.Message)) Plugin.L.LogWarning("기지 정보 읽기 실패: " + e);
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
            catch (Exception e)
            {
                if (_loggedErrors.Add(e.GetType().Name + e.Message))
                    Plugin.L.LogWarning("상태 읽기 실패: " + e);
            }
        }
    }

    /// <summary>게임 객체에서 값을 읽어 JSON 본문(중괄호 없는 필드들)을 만든다. 게임 메인 스레드 전용.</summary>
    internal static class Snapshot
    {
        public static string Build()
        {
            using var ms = new MemoryStream();
            using (var w = new Utf8JsonWriter(ms))
            {
                w.WriteStartObject();
                w.WriteString("game_version", Application.version);
                w.WriteNumber("screen_w", Screen.width);
                w.WriteNumber("screen_h", Screen.height);
                WriteGameState(w);
                WriteBattle(w);
                WriteLevelUp(w);
                WriteGameOver(w);
                WriteField(w);
                WriteBase(w);
                WriteUiAvoid(w);
                w.WriteEndObject();
            }
            var json = Encoding.UTF8.GetString(ms.ToArray());
            return json.Substring(1, json.Length - 2);   // 바깥 중괄호 제거 (머리 필드와 합치기 위해)
        }

        static void WriteGameState(Utf8JsonWriter w)
        {
            var gm = GameMgr.I;
            w.WriteString("game_state", gm != null ? gm.CurState.ToString() : null);
        }

        /// <summary>런이 끝난 화면(보스 격퇴 후 '원정 계속' 버튼 포함). 계속/복귀 판단용.</summary>
        static void WriteGameOver(Utf8JsonWriter w)
        {
            var ui = GameUIMgr.I != null ? GameUIMgr.I.GameOver : null;
            bool open = ui != null && ui.gameObject.activeInHierarchy && ui.IsActiveOverlay();
            if (!open)
            {
                w.WriteNull("game_over");
                return;
            }
            w.WriteStartObject("game_over");
            var b = BattleSaveData.I;
            if (b != null) w.WriteBoolean("completed", b.CompletedLevel);
            try { w.WriteBoolean("endless_btn", ui.BtnEndless != null && ui.BtnEndless.gameObject.activeInHierarchy); } catch { }
            try { w.WriteBoolean("endless_unlocked", BuildingMgr.I != null && BuildingMgr.I.EndlessModeUnlocked); } catch { }
            w.WriteEndObject();
        }

        static void WriteBattle(Utf8JsonWriter w)
        {
            var b = BattleSaveData.I;
            if (b == null)
            {
                w.WriteNull("battle");
                return;
            }
            w.WriteStartObject("battle");
            var ch = b.CurChar;
            if (ch != null)
            {
                w.WriteString("char", ch.Type.ToString());
                var extra = ch.CombinedTypes;
                w.WriteStartArray("chars_combined");
                if (extra != null)
                    for (int i = 0; i < extra.Count; i++) w.WriteStringValue(extra[i].ToString());
                w.WriteEndArray();
            }
            w.WriteString("level", b.CurLevel.ToString());
            w.WriteNumber("turn", b.CurTurn);
            var res = b.NumResources;
            if (res != null)
            {
                w.WriteNumber("gold", res.GetTotalAmount());
                w.WriteString("resources", res.ToString());
            }
            w.WriteNumber("health", b.CurHealth);
            w.WriteNumber("upgrade_lvl", b.UpgradeLvl);
            w.WriteNumber("level_ups_avail", b.NumLevelUpsAvail);
            w.WriteNumber("free_rerolls", b.NumFreeRerolls);
            w.WriteNumber("banishes", b.NumBanishes);
            w.WriteNumber("rerolls", b.NumLvlUpRerolls);
            // 진행 상황 (판단 근거용)
            try { if (UpgradeMgr.I != null) w.WriteNumber("max_health", UpgradeMgr.I.MaxHealth); } catch { }
            w.WriteNumber("final_boss_turn", b.FinalBossTurn);
            w.WriteNumber("difficulty", b.CurDifficulty);
            w.WriteNumber("ng_plus", b.CurNGPlusLvl);
            w.WriteBoolean("endless", b.IsEndless);
            w.WriteNumber("endless_start_turn", b.EndlessStartTurn);
            w.WriteBoolean("completed_level", b.CompletedLevel);
            w.WriteNumber("revives", b.NumRevives);
            w.WriteNumber("kills", b.NumKills);
            w.WriteNumber("elapsed", Math.Round(b.ElapsedTime, 1));
            w.WriteNumber("evos_fused", b.NumEvosFused);
            w.WriteNumber("combos_fused", b.NumCombosFused);
            w.WriteNumber("baby_dmg", b.BabyDamageDealt);
            try { if (BuildingMgr.I != null) w.WriteNumber("revives_max", BuildingMgr.I.NumRevives); } catch { }
            WriteBattleExtra(w, b);
            try { w.WriteNumber("max_balls", StatUtl.GetMaxHeroes()); } catch { }
            try { w.WriteNumber("max_passives", StatUtl.GetMaxPassives()); } catch { }
            w.WriteStartArray("banished");
            var banished = b.BanishedItems;
            if (banished != null)
                for (int i = 0; i < banished.Count; i++) WriteInfoTypeValue(w, banished[i]);
            w.WriteEndArray();

            w.WriteStartArray("balls");
            var heroes = b.Heroes;
            if (heroes != null)
            {
                for (int i = 0; i < heroes.Count; i++)
                {
                    var h = heroes[i];
                    if (h == null) continue;
                    w.WriteStartObject();
                    w.WriteNumber("idx", i);
                    w.WriteString("type", h.Type.ToString());
                    w.WriteNumber("lvl", h.Lvl);
                    w.WriteBoolean("max", h.IsAtMaxSolo());
                    WriteHeroStats(w, h);
                    var combo = h.CombinedHeroes;
                    if (combo != null && combo.Count > 0)
                    {
                        w.WriteStartArray("combined");
                        for (int j = 0; j < combo.Count; j++)
                        {
                            w.WriteStringValue(combo[j].ToString());
                        }
                        w.WriteEndArray();
                    }
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();

            w.WriteStartArray("passives");
            var passives = b.Passives;
            if (passives != null)
            {
                for (int i = 0; i < passives.Count; i++)
                {
                    var p = passives[i];
                    if (p == null) continue;
                    w.WriteStartObject();
                    w.WriteNumber("idx", i);
                    w.WriteString("type", p.Type.ToString());
                    w.WriteNumber("lvl", p.Lvl);
                    try { w.WriteBoolean("max", p.IsAtMaxSolo()); } catch { }
                    try
                    {
                        var sd = p.StatData;
                        long bonus = 0;
                        if (sd != null) for (int k = 0; k < sd.Count; k++) if (sd[k] != null) bonus += sd[k].BonusDamage;
                        w.WriteNumber("dmg", bonus);
                    }
                    catch { }
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();
            w.WriteEndObject();
        }

        /// <summary>게임 월드 좌표 → 게임 클라이언트 화면 좌표(왼쪽 위 원점). 카메라가 없으면 false.</summary>
        static bool ToScreen(Vector3 world, out float x, out float y)
        {
            x = y = 0;
            var cam = Camera.main;
            if (cam == null) return false;
            var p = cam.WorldToScreenPoint(world);
            if (p.z < 0) return false;
            x = p.x;
            y = Screen.height - p.y;
            return true;
        }

        /// <summary>전투 필드: 적 위치(화면 좌표, 가까운 순), 플레이어 위치, 적이 공격하는 선. 읽기만 한다.</summary>
        static void WriteField(Utf8JsonWriter w)
        {
            var gm = GridMgr.I;
            if (gm == null || BattleSaveData.I == null || GameMgr.I == null)
            {
                w.WriteNull("field");
                return;
            }
            w.WriteStartObject("field");
            try
            {
                w.WriteNumber("attack_y", Math.Round(gm.AttackY, 2));
                w.WriteNumber("front_enemy_y", Math.Round(gm.FrontEnemyY, 2));
                w.WriteNumber("bottom_y", Math.Round(gm.BottomBorderY, 2));
                w.WriteNumber("top_y", Math.Round(gm.TopBorderY, 2));
                w.WriteNumber("left_x", Math.Round(gm.LeftBorderX, 2));
                w.WriteNumber("right_x", Math.Round(gm.RightBorderX, 2));
            }
            catch { }
            try
            {
                var pl = Player.I;
                if (pl != null)
                {
                    var pos = pl.transform.position;
                    w.WriteStartArray("player");
                    if (ToScreen(pos, out var sx, out var sy)) { w.WriteNumberValue((int)sx); w.WriteNumberValue((int)sy); }
                    else { w.WriteNumberValue(-1); w.WriteNumberValue(-1); }
                    w.WriteNumberValue(Math.Round(pos.x, 2));
                    w.WriteNumberValue(Math.Round(pos.y, 2));
                    w.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var list = new List<(float wy, float wx, float sx, float sy, int hp, int max, bool boss)>();
                var dict = gm.PieceColDict;
                if (dict != null)
                    foreach (var kv in dict)
                    {
                        var obj = kv.Value;
                        if (obj == null || !obj.IsActive) continue;
                        var inst = obj.Inst;
                        if (inst == null || inst.CurHealth <= 0) continue;
                        var pos = obj.transform.position;
                        if (!ToScreen(pos, out var sx, out var sy)) continue;
                        bool boss = false;
                        try { boss = StatUtl.IsBoss(inst.Type); } catch { }
                        list.Add((pos.y, pos.x, sx, sy, inst.CurHealth, inst.MaxHealth, boss));
                    }
                list.Sort((a, b) => a.wy.CompareTo(b.wy));
                w.WriteStartArray("enemies");   // [화면x, 화면y, 월드y, 체력, 최대, 보스] — 월드 y 가 작을수록 플레이어에 가깝다
                for (int i = 0; i < list.Count && i < 40; i++)
                {
                    var e = list[i];
                    w.WriteStartArray();
                    w.WriteNumberValue((int)e.sx);
                    w.WriteNumberValue((int)e.sy);
                    w.WriteNumberValue(Math.Round(e.wy, 2));
                    w.WriteNumberValue(e.hp);
                    w.WriteNumberValue(e.max);
                    w.WriteNumberValue(e.boss ? 1 : 0);
                    w.WriteEndArray();
                }
                w.WriteEndArray();
            }
            catch { }
            w.WriteEndObject();
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
            { "kIdleStoneMine", new[] { "kBoulder", "kGraniteSlab", "kStonePile" } },
            { "kHovel", new[] { "kBoulder", "kGraniteSlab", "kStonePile" } },
            { "kRockyHill", new[] { "kBoulder", "kGraniteSlab", "kStonePile" } },
        };
        static readonly Dictionary<int, string> _rangeJson = new Dictionary<int, string>();
        static long _rangeSig = long.MinValue;

        static void WriteInRange(Utf8JsonWriter w, BuildingInst b)
        {
            string key = b.Type.ToString();
            if (!RangeTargets.TryGetValue(key, out var targets)) return;
            if (_rangeSig != _colSig) { _rangeJson.Clear(); _rangeSig = _colSig; }
            if (!_rangeJson.TryGetValue(b.Id, out var js))
            {
                using var ms = new MemoryStream();
                using (var rw = new Utf8JsonWriter(ms))
                {
                    rw.WriteStartObject();
                    float r = b.GetRange();
                    foreach (var t in targets)
                    {
                        try
                        {
                            var bt = (BuildingType)Enum.Parse(typeof(BuildingType), t);
                            rw.WriteNumber(t, b.GetNumBuildingsInRange(bt, r));
                        }
                        catch { }
                    }
                    rw.WriteEndObject();
                }
                js = Encoding.UTF8.GetString(ms.ToArray());
                _rangeJson[b.Id] = js;
            }
            w.WritePropertyName("in_range");
            w.WriteRawValue(js, true);
        }

        static void WriteBase(Utf8JsonWriter w)
        {
            var bm = BaseMgr.I;
            var m = MetaSaveData.I;
            if (bm == null || m == null || !bm.gameObject.activeInHierarchy)
            {
                Aiming = false;
                w.WriteNull("base");
                return;
            }
            w.WriteStartObject("base");
            try { var st = bm.CurState.ToString(); Aiming = st == "kAimWorkers"; w.WriteString("state", st); } catch { }
            // 알선소로 2명 원정: 로드아웃 화면의 두 캐릭터 패널이 지금까지 고른 캐릭터를 담고 있다
            // (kSelectingChar 로 캐릭터 고르는 화면이 열려 있는 동안에도 로드아웃 화면은 뒤에 그대로 있다).
            try
            {
                var lo = LoadoutUI.I;
                if (lo != null && lo.gameObject.activeInHierarchy)
                {
                    w.WriteStartObject("loadout");
                    try { w.WriteString("char1", lo.CharPanel?._tgtChar?.Type.ToString()); } catch { }
                    try { w.WriteString("char2", lo.Char2Panel?._tgtChar?.Type.ToString()); } catch { }
                    w.WriteEndObject();
                }
            }
            catch { }
            try { w.WriteNumber("harvest_secs_left", Math.Round(bm.RemainingHarvestSecs, 1)); } catch { }
            try { w.WriteBoolean("harvested_today", m.DidHarvestToday); } catch { }
            // 스파(목욕탕): 골드를 내고 바로 한 번 더 채집 — 비용·오늘 쓴 횟수 (앱이 지난 채집량과 비교해 손익을 보여 준다)
            try
            {
                var bmg = BuildingMgr.I;
                if (bmg != null)
                {
                    w.WriteStartObject("spa");
                    try { w.WriteNumber("cost", bmg.GetMasseuseCost()); } catch { }
                    try { w.WriteNumber("lvl", bmg.MasseuseLvl); } catch { }
                    try { w.WriteNumber("used_today", m.NumMasseuseToday); } catch { }
                    try { w.WriteNumber("harvests", m.NumHarvests); } catch { }
                    try { w.WriteBoolean("built", bmg.IsBuildingBuilt(BuildingType.kMasseuse)); } catch { }
                    w.WriteEndObject();
                }
            }
            catch { }
            try { w.WriteNumber("day", m.CurDay); } catch { }
            try
            {
                var bp = BasePlayer.I;
                if (bp != null)
                {
                    w.WriteStartArray("player");
                    if (ToScreen(bp.transform.position, out var sx, out var sy)) { w.WriteNumberValue((int)sx); w.WriteNumberValue((int)sy); }
                    else { w.WriteNumberValue(-1); w.WriteNumberValue(-1); }
                    var aim = bp.GetAimDir();
                    w.WriteNumberValue(Math.Round(aim.x, 3));
                    w.WriteNumberValue(Math.Round(aim.y, 3));
                    w.WriteEndArray();
                }
            }
            catch { }
            WriteBaseGeometry(w, bm, m);
            w.WriteStartArray("buildings");
            var list = m.Buildings;
            if (list != null)
                for (int i = 0; i < list.Count; i++)
                {
                    var b = list[i];
                    if (b == null) continue;
                    w.WriteStartObject();
                    w.WriteNumber("id", b.Id);
                    w.WriteString("type", b.Type.ToString());
                    w.WriteNumber("lvl", b.UpgradeLvl);
                    w.WriteNumber("x", Math.Round(b.X, 2));
                    w.WriteNumber("y", Math.Round(b.Y, 2));
                    try
                    {
                        if (b.Obj != null && ToScreen(b.Obj.transform.position, out var sx, out var sy))
                        {
                            w.WriteNumber("sx", (int)sx);
                            w.WriteNumber("sy", (int)sy);
                        }
                    }
                    catch { }
                    try { w.WriteNumber("res", b.GetNumResources()); } catch { }
                    try { w.WriteNumber("cap", b.GetResourceCapacity()); } catch { }
                    try { w.WriteBoolean("can_harvest", b.CanHarvest()); } catch { }
                    try { if (b.HeldResources != null) { w.WriteStartArray("held"); for (int k = 0; k < b.HeldResources.Num.Length; k++) w.WriteNumberValue(b.HeldResources.Num[k]); w.WriteEndArray(); } } catch { }
                    try { w.WriteNumber("worker", b.WorkerChar); } catch { }
                    try { if (b.HasActiveTask()) w.WriteNumber("task", Math.Round(b.GetTaskProgress(), 2)); } catch { }
                    try { w.WriteNumber("upgrade_pct", Math.Round(b.GetUpgradePct(), 2)); } catch { }
                    // 미완성(공사장 kScaffold·강화 공사 kUpgrading): 작업자가 맞힐 때마다 UpgradePts 가 쌓여 목표에 닿으면 완성
                    try { w.WriteString("state", b.CurState.ToString()); } catch { }
                    try { w.WriteNumber("upg_pts", b.UpgradePts); w.WriteNumber("upg_tgt", b.GetUpgradeTgt()); } catch { }
                    try { w.WriteNumber("range", Math.Round(b.GetRange(), 2)); } catch { }
                    try { WriteInRange(w, b); } catch { }
                    w.WriteNumber("rot", b.Rotation);
                    try
                    {
                        var info = b.GetInfo();
                        if (info != null)
                        {
                            w.WriteNumber("tw", info.TileSize.x);
                            w.WriteNumber("th", info.TileSize.y);
                            w.WriteString("col", info.ColType.ToString());
                            try { w.WriteString("stat", info.GetStatBonus().ToString()); } catch { }   // 능력치 보너스 건물이면 그 능력치
                        }
                    }
                    catch { }
                    w.WriteEndObject();
                }
            w.WriteEndArray();
            w.WriteEndObject();
        }

        static void Pt(Utf8JsonWriter w, Vector3 v)
        {
            w.WriteStartArray();
            w.WriteNumberValue(Math.Round(v.x, 3));
            w.WriteNumberValue(Math.Round(v.y, 3));
            w.WriteEndArray();
        }

        /// <summary>기지 물리 모양: 벽, 청크, 발사대, 작업자 속도, 건물 충돌 모양(월드 좌표), 화면 대응점, 날아가는 작업자.
        /// 채집 궤적·배치 계산용. 읽기만 한다 (물리 질의도 하지 않는다).</summary>
        static void WriteBaseGeometry(Utf8JsonWriter w, BaseMgr bm, MetaSaveData m)
        {
            var g = BaseGridMgr.I;
            if (g == null) return;
            w.WriteStartObject("geo");
            try
            {
                w.WriteNumber("left", Math.Round(g.LeftBorderX, 3));
                w.WriteNumber("right", Math.Round(g.RightBorderX, 3));
                w.WriteNumber("top", Math.Round(g.TopBorderY, 3));
                w.WriteNumber("bottom", Math.Round(g.BottomBorderY, 3));
                w.WriteNumber("player_y", Math.Round(g.PlayerY, 3));
                w.WriteNumber("space_w", Math.Round(BaseGridMgr.kSpaceWidth, 4));
                w.WriteNumber("space_h", Math.Round(BaseGridMgr.kSpaceHeight, 4));
                w.WriteNumber("chunk_w", BaseGridMgr.kChunkWidth);
                w.WriteNumber("chunk_h", BaseGridMgr.kChunkHeight);
                w.WriteNumber("chunk_world_w", Math.Round(BaseGridMgr.kChunkWorldWidth, 3));
                w.WriteNumber("chunk_world_h", Math.Round(BaseGridMgr.kChunkWorldHeight, 3));
                w.WriteNumber("chunk_cols", BaseGridMgr.kChunkCols);
                w.WriteNumber("chunk_rows", BaseGridMgr.kChunkRows);
            }
            catch { }
            try
            {
                w.WriteStartArray("chunks");
                var ch = m.BaseChunks;
                if (ch != null)
                    for (int x = 0; x < ch.Length; x++)
                        if (ch[x] != null)
                            for (int y = 0; y < ch[x].Length; y++)
                            {
                                var c = ch[x][y];
                                if (c != null && c.IsPurchased) { w.WriteStartArray(); w.WriteNumberValue(c.X); w.WriteNumberValue(c.Y); w.WriteEndArray(); }
                            }
                w.WriteEndArray();
            }
            catch { }
            // 게임의 입구 청크 좌표를 읽는다. IsEntrance 는 타일이 아닌 청크 좌표를 받는다.
            try
            {
                int ex = g.GetEntranceX(), ey = g.GetEntranceY();
                if (g.IsEntrance(ex, ey))
                {
                    w.WriteStartArray("entrance_chunk");
                    w.WriteNumberValue(ex);
                    w.WriteNumberValue(ey);
                    w.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var bmgr = BuildingMgr.I;
                if (bmgr != null)
                {
                    w.WriteNumber("worker_speed", Math.Round(bmgr.WorkerMoveSpeed, 3));
                    w.WriteNumber("worker_speed_mult", Math.Round(bmgr.WorkerMoveSpeedMult, 3));
                    w.WriteNumber("harvest_len", Math.Round(bmgr.HarvestLength, 3));
                }
                w.WriteNumber("ball_time_dist", Math.Round(BaseMgr.kBallTimeDist, 3));
            }
            catch { }
            try
            {
                var bp = BasePlayer.I;
                if (bp != null) { w.WritePropertyName("launcher"); Pt(w, bp.transform.position); }
            }
            catch { }
            // 화면 대응점: 기지 네 모서리의 화면 좌표 (앱이 월드 ↔ 화면 변환을 만든다)
            try
            {
                w.WriteStartArray("proj");
                foreach (var (x, y) in new[] { (g.LeftBorderX, g.BottomBorderY), (g.RightBorderX, g.BottomBorderY),
                                               (g.LeftBorderX, g.TopBorderY), (g.RightBorderX, g.TopBorderY),
                                               ((g.LeftBorderX + g.RightBorderX) / 2, (g.BottomBorderY + g.TopBorderY) / 2) })
                {
                    if (!ToScreen(new Vector3(x, y, 0), out var sx, out var sy)) continue;
                    w.WriteStartArray();
                    w.WriteNumberValue(Math.Round(x, 3)); w.WriteNumberValue(Math.Round(y, 3));
                    w.WriteNumberValue((int)sx); w.WriteNumberValue((int)sy);
                    w.WriteEndArray();
                }
                w.WriteEndArray();
            }
            catch { }
            // 건물 충돌 모양 (월드 좌표). 60여 개 모양을 매번 읽으면 게임 프레임 시간을 먹으므로
            // 배치 지문(건물 id·위치·방향·상태)이 같으면 지난 결과를 쓰고, 1초마다 한 번은 새로 읽는다.
            try
            {
                long sig = 17;
                var bl = m.Buildings;
                if (bl != null)
                    for (int i = 0; i < bl.Count; i++)
                    {
                        var b = bl[i];
                        if (b == null) continue;
                        sig = sig * 31 + b.Id;
                        sig = sig * 31 + (long)Math.Round(b.X * 100);
                        sig = sig * 31 + (long)Math.Round(b.Y * 100);
                        sig = sig * 31 + b.Rotation;
                        try { sig = sig * 31 + (int)b.CurState; } catch { }
                    }
                float t = Time.realtimeSinceStartup;
                if (sig != _colSig || t - _colAt > 1f || _colJson.Length == 0)
                {
                    using var cms = new MemoryStream();
                    using (var cw = new Utf8JsonWriter(cms))
                        WriteColliders(cw);
                    _colJson = Encoding.UTF8.GetString(cms.ToArray());
                    _colSig = sig;
                    _colAt = t;
                }
                w.WritePropertyName("colliders");
                w.WriteRawValue(_colJson, true);
            }
            catch { }
            // 날아가는 작업자 (채집 중): 실제 궤적 검증용
            try
            {
                var balls = bm.ActiveBalls;
                w.WriteStartArray("workers");
                if (balls != null)
                    for (int i = 0; i < balls.Count; i++)
                    {
                        var b = balls[i];
                        if (b == null || !b.IsActive) continue;
                        var pos = b.transform.position;
                        w.WriteStartArray();
                        w.WriteNumberValue(Math.Round(pos.x, 3));
                        w.WriteNumberValue(Math.Round(pos.y, 3));
                        w.WriteNumberValue(Math.Round(b.AimDir.x, 3));
                        w.WriteNumberValue(Math.Round(b.AimDir.y, 3));
                        w.WriteNumberValue(Math.Round(b.Speed, 3));
                        float r = -1;
                        try { var cc = b.GetComponent<CircleCollider2D>(); if (cc != null) r = cc.radius * Math.Abs(b.transform.lossyScale.x); } catch { }
                        w.WriteNumberValue(Math.Round(r, 3));
                        w.WriteNumberValue(b.NumBounces);
                        w.WriteNumberValue(b.HeldResources != null ? b.HeldResources.GetTotalAmount() : 0);
                        w.WriteNumberValue(b.WInst != null ? (int)b.WInst.Type : -1);
                        w.WriteEndArray();
                    }
                w.WriteEndArray();
            }
            catch { }
            w.WriteEndObject();
        }

        static void WriteColliders(Utf8JsonWriter w)
        {
            var g = BaseGridMgr.I;
            w.WriteStartArray();
            var dict = g != null ? g.BuildingColDict : null;
            if (dict != null)
                foreach (var kv in dict)
                {
                    var col = kv.Key;
                    var obj = kv.Value;
                    if (col == null || obj == null || obj.Inst == null || !col.enabled || !col.gameObject.activeInHierarchy) continue;
                    var t = col.transform;
                    w.WriteStartObject();
                    w.WriteNumber("id", obj.Inst.Id);
                    w.WriteBoolean("trigger", col.isTrigger);
                    var box = col.TryCast<BoxCollider2D>();
                    var circ = col.TryCast<CircleCollider2D>();
                    var poly = col.TryCast<PolygonCollider2D>();
                    if (box != null)
                    {
                        w.WriteString("shape", "box");
                        var o = box.offset; var h = box.size * 0.5f;
                        w.WriteStartArray("pts");
                        Pt(w, t.TransformPoint(new Vector3(o.x - h.x, o.y - h.y, 0)));
                        Pt(w, t.TransformPoint(new Vector3(o.x + h.x, o.y - h.y, 0)));
                        Pt(w, t.TransformPoint(new Vector3(o.x + h.x, o.y + h.y, 0)));
                        Pt(w, t.TransformPoint(new Vector3(o.x - h.x, o.y + h.y, 0)));
                        w.WriteEndArray();
                    }
                    else if (circ != null)
                    {
                        w.WriteString("shape", "circle");
                        w.WritePropertyName("c");
                        Pt(w, t.TransformPoint(new Vector3(circ.offset.x, circ.offset.y, 0)));
                        var sc = t.lossyScale;
                        w.WriteNumber("r", Math.Round(circ.radius * Math.Max(Math.Abs(sc.x), Math.Abs(sc.y)), 3));
                    }
                    else if (poly != null)
                    {
                        w.WriteString("shape", "poly");
                        w.WriteStartArray("pts");
                        var pts = poly.points;
                        for (int k = 0; k < pts.Length; k++) Pt(w, t.TransformPoint(new Vector3(pts[k].x + poly.offset.x, pts[k].y + poly.offset.y, 0)));
                        w.WriteEndArray();
                    }
                    else
                    {
                        w.WriteString("shape", "bounds");
                        var bd = col.bounds;
                        w.WriteStartArray("pts");
                        Pt(w, new Vector3(bd.min.x, bd.min.y, 0)); Pt(w, new Vector3(bd.max.x, bd.min.y, 0));
                        Pt(w, new Vector3(bd.max.x, bd.max.y, 0)); Pt(w, new Vector3(bd.min.x, bd.max.y, 0));
                        w.WriteEndArray();
                    }
                    w.WriteEndObject();
                }
            w.WriteEndArray();
        }

        /// <summary>전투 상황: 경험치, 적·보스, 보스·융합기 일정 진행, 캐릭터 능력치, 상태 효과. 모두 읽기만 한다.</summary>
        static void WriteBattleExtra(Utf8JsonWriter w, BattleSaveData b)
        {
            try
            {
                w.WriteNumber("xp", Math.Round(b.CurXP, 1));
                w.WriteNumber("xp_next", StatUtl.GetBattleTgtXP(b.UpgradeLvl));
            }
            catch { }
            w.WriteNumber("boss_turns_elapsed", b.NumBossTurnsElapsed);
            w.WriteNumber("fuser_turns_elapsed", b.NumFuserTurnsElapsed);
            w.WriteNumber("treasures", b.NumTreasures);
            w.WriteNumber("baby_kills", b.BabyKills);
            w.WriteNumber("endless_kills", b.NumEndlessKills);
            w.WriteNumber("fissions", b.NumFissionsDone);
            w.WriteNumber("rows", b.NumRows);
            w.WriteNumber("cols", b.NumCols);
            w.WriteNumber("player_x", Math.Round(b.PlayerX, 2));
            try { w.WriteNumber("enemies", b.GetNumActiveEnemies()); } catch { }
            try { w.WriteNumber("lowest_enemy_y", Math.Round(b.GetLowestEnemyY(), 2)); } catch { }
            try
            {
                var pieces = b.Pieces;
                if (pieces != null && b.HasBossPiece())
                {
                    long hp = 0, max = 0;
                    string type = null;
                    for (int i = 0; i < pieces.Count; i++)
                    {
                        var pc = pieces[i];
                        if (pc == null || !StatUtl.IsBoss(pc.Type)) continue;
                        hp += Math.Max(0, pc.CurHealth);
                        max += Math.Max(0, pc.MaxHealth);
                        type ??= pc.Type.ToString();
                    }
                    if (max > 0)
                    {
                        w.WriteStartObject("boss");
                        w.WriteString("type", type);
                        w.WriteNumber("hp", hp);
                        w.WriteNumber("max", max);
                        w.WriteEndObject();
                    }
                }
            }
            catch { }
            try
            {
                var um = UpgradeMgr.I;
                if (um != null)
                {
                    w.WriteStartObject("stats");
                    w.WriteNumber("crit_chance", Math.Round(um.CritChance, 3));
                    w.WriteNumber("crit_mult", Math.Round(um.CritMultiplier, 3));
                    w.WriteNumber("fire_rate", Math.Round(um.FireRate, 3));
                    w.WriteNumber("reload", Math.Round(um.ReloadTime, 3));
                    w.WriteNumber("ball_speed", Math.Round(um.BaseSpeed, 3));
                    w.WriteNumber("move_speed", Math.Round(um.MoveSpeed, 3));
                    w.WriteNumber("damage_reduction", Math.Round(um.DamageReduction, 3));
                    w.WriteNumber("dodge", Math.Round(um.DodgeChance, 3));
                    w.WriteNumber("thorns", um.ThornsAmt);
                    w.WriteNumber("health_per_kill", um.HealthPerKill);
                    w.WriteNumber("pickup_range", Math.Round(um.PickupRange, 3));
                    w.WriteNumber("bonus_xp", Math.Round(um.BonusXPDropped, 3));
                    w.WriteNumber("bonus_gold", Math.Round(um.BonusGoldDropped, 3));
                    w.WriteNumber("ball_damage_mult", Math.Round(um.BallDamageMult, 3));
                    w.WriteNumber("bonus_ball_damage", um.BonusHeroDamage);
                    w.WriteNumber("babies", um.NumFollowers);
                    w.WriteNumber("multi_balls", um.NumMultiHeroes);
                    w.WriteEndObject();
                }
            }
            catch { }
            try
            {
                var ch = b.CurChar;
                if (ch != null && ch.Stats != null)
                {
                    w.WriteStartArray("char_stats");   // 게임 StatType 순서: 체력·힘·통솔·속도·민첩·지능
                    for (int i = 0; i < ch.Stats.Length; i++) w.WriteNumberValue(ch.Stats[i]);
                    w.WriteEndArray();
                }
            }
            catch { }
            try
            {
                var eff = b.PlayerStatusEffects;
                w.WriteStartArray("effects");
                if (eff != null)
                    for (int i = 0; i < eff.Count; i++)
                    {
                        var e = eff[i];
                        if (e == null) continue;
                        w.WriteStartObject();
                        w.WriteString("type", e.Type.ToString());
                        w.WriteNumber("left", Math.Round(e.RemainingLen, 1));
                        w.WriteEndObject();
                    }
                w.WriteEndArray();
            }
            catch { }
        }

        /// <summary>이번 런에서 이 볼이 준 피해·처치 (게임의 런 종료 통계와 같은 값).</summary>
        static void WriteHeroStats(Utf8JsonWriter w, HeroInst h)
        {
            try
            {
                var sd = h.StatData;
                if (sd == null) return;
                long dmg = 0, kills = 0, launches = 0, bounce = 0, status = 0, alt = 0, aoe = 0;
                for (int k = 0; k < sd.Count; k++)
                {
                    var s = sd[k];
                    if (s == null) continue;
                    bounce += s.BounceDmgDealt;
                    status += s.StatusEffectDmgDealt;
                    alt += s.AltDmgDealt;
                    aoe += s.AOEDmgDealt;
                    kills += s.NumKills;
                    launches += s.NumLaunches;
                }
                dmg = bounce + status + alt + aoe;
                w.WriteNumber("dmg", dmg);
                w.WriteStartObject("dmg_by");
                w.WriteNumber("bounce", bounce);
                w.WriteNumber("status", status);
                w.WriteNumber("other", alt);
                w.WriteNumber("aoe", aoe);
                w.WriteEndObject();
                w.WriteNumber("kills", kills);
                w.WriteNumber("launches", launches);
            }
            catch { }
        }

        static void WriteChoiceList(Utf8JsonWriter w, string name, Il2CppSystem.Collections.Generic.List<UpgradeChoice> list)
        {
            w.WriteStartArray(name);
            if (list != null)
                for (int i = 0; i < list.Count; i++) WriteInfoTypeValue(w, list[i].Info);
            w.WriteEndArray();
        }

        static void WriteLevelUp(Utf8JsonWriter w)
        {
            var ui = LevelUpUI.I;
            bool open = ui != null && ui.gameObject.activeInHierarchy && ui.IsActiveOverlay();
            if (!open)
            {
                w.WriteNull("levelup");
                return;
            }
            w.WriteStartObject("levelup");
            w.WriteString("type", ui.Type.ToString());
            w.WriteString("page", ui.CurPage.ToString());
            w.WriteNumber("reroll_cost", ui._rerollCost);
            WriteRect(w, "panel", ui.PanelMain);

            w.WriteStartArray("choices");
            var choices = ui._choices;
            var btns = ui.Btns;
            if (choices != null)
            {
                for (int i = 0; i < choices.Count; i++)
                {
                    var c = choices[i];
                    w.WriteStartObject();
                    w.WriteNumber("idx", i);
                    w.WriteString("kind", c.Type.ToString());
                    w.WriteBoolean("is_new", c.IsNew);
                    w.WriteNumber("equip_idx", c.EquipmentIdx);
                    var info = c.Info;
                    if (info != null)
                    {
                        w.WriteString("slug", info.Slug);
                        var hero = info.TryCast<HeroInfo>();
                        var passive = info.TryCast<PassiveInfo>();
                        if (hero != null) w.WriteString("type", hero.Type.ToString());
                        else if (passive != null) w.WriteString("type", passive.Type.ToString());
                        try { w.WriteBoolean("ai_pick", info.ShouldAIPick()); } catch { }
                        if (hero != null) WriteSynergy(w, hero);
                        if (hero == null && passive == null)
                        {
                            // 펫 강화 등: 게임 번역 표에서 현재 언어 이름·설명을 그대로 가져온다
                            var pet = info.TryCast<PetUpgradeInfo>();
                            if (pet != null) w.WriteString("type", pet.Type.ToString());
                            w.WriteString("name_loc", Loc(info.GetNameSlug()));
                            w.WriteString("desc_loc", Loc(info.GetDescSlug()));
                        }
                    }
                    var btn = FindButton(btns, i);
                    if (btn != null)
                    {
                        w.WriteNumber("tgt_lvl", btn.TgtLvl);
                        WriteRect(w, "rect", btn.Xfm);
                    }
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();
            // 다음 선택지가 뽑히는 후보 (새로고침 확률 추정용). 직전 선택지는 다음 새로고침에서 빠진다.
            w.WriteStartObject("pool");
            try
            {
                WriteChoiceList(w, "new_balls", ui._availNewHeroes);
                WriteChoiceList(w, "ball_upgrades", ui._availHeroUpgrades);
                WriteChoiceList(w, "new_passives", ui._availNewPassives);
                WriteChoiceList(w, "passive_upgrades", ui._availPassiveUpgrades);
                WriteChoiceList(w, "prev", ui._prevChoices);
                w.WriteNumber("num_choices", ui._numUpgradeChoices);
                w.WriteBoolean("banishing", ui._isBanishing);
            }
            catch { }
            w.WriteEndObject();
            WriteFuser(w, ui);
            w.WriteEndObject();
        }

        /// <summary>선택지 볼과 게임이 '시너지'로 판정하는 보유 볼 (게임의 카드 설명 '시너지 장비'와 같은 판정).</summary>
        static void WriteSynergy(Utf8JsonWriter w, HeroInfo choice)
        {
            var b = BattleSaveData.I;
            if (b == null || b.Heroes == null) return;
            w.WriteStartArray("synergy");
            for (int i = 0; i < b.Heroes.Count; i++)
            {
                var h = b.Heroes[i];
                if (h == null) continue;
                try
                {
                    var owned = h.GetInfo();
                    if (owned != null && (choice.HasSynergy(owned) || owned.HasSynergy(choice)))
                        w.WriteStringValue(h.Type.ToString());
                }
                catch { }
            }
            w.WriteEndArray();
        }

        /// <summary>융합 화면: 지금 고를 수 있는 진화와 융합 조합. 게임 자동 선택 AI의 조합 점수도 함께 보낸다.</summary>
        static void WriteFuser(Utf8JsonWriter w, LevelUpUI ui)
        {
            w.WriteStartObject("fuser");
            try { w.WriteBoolean("free_upgrades", ui.HasFreeUpgrades()); } catch { }
            w.WriteStartArray("options");
            var opts = ui._fusionChoices;
            if (opts != null)
                for (int i = 0; i < opts.Count; i++) w.WriteStringValue(((FuserOptionType)opts[i]).ToString());
            w.WriteEndArray();

            w.WriteStartArray("evos");
            var merges = ui._availMerges;
            if (merges != null)
            {
                for (int i = 0; i < merges.Count; i++)
                {
                    var m = merges[i];
                    w.WriteStartObject();
                    w.WriteNumber("idx", i);
                    w.WriteNumber("equip_idx", m.EquipmentIdx);
                    w.WriteNumber("evo_idx", m.EvoIdx);
                    WriteInfoType(w, "type", m.Info);
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();

            w.WriteStartArray("combos");
            var combos = ui._availHCombos;
            var heroes = BattleSaveData.I != null ? BattleSaveData.I.Heroes : null;
            if (combos != null)
            {
                for (int i = 0; i < combos.Count; i++)
                {
                    var hc = combos[i];
                    w.WriteStartObject();
                    w.WriteNumber("idx", i);
                    w.WriteString("h1", hc.H1.ToString());
                    w.WriteString("h2", hc.H2.ToString());
                    w.WriteNumber("idx1", hc.Idx1);
                    w.WriteNumber("idx2", hc.Idx2);
                    try { w.WriteNumber("ai_score", ui.GetComboScore(i, hc)); } catch { }
                    try
                    {
                        if (heroes != null && hc.Idx1 >= 0 && hc.Idx2 >= 0 && hc.Idx1 < heroes.Count && hc.Idx2 < heroes.Count)
                            w.WriteBoolean("bad", heroes[hc.Idx1].IsBadCombo(heroes[hc.Idx2]));
                    }
                    catch { }
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();
            w.WriteEndObject();
        }

        /// <summary>게임 번역 표에서 현재 게임 언어 문장을 읽는다 (읽기만 함). 실패하면 빈 문자열.</summary>
        internal static string Loc(string term)
        {
            if (string.IsNullOrEmpty(term)) return "";
            try
            {
                var t = I2.Loc.LocalizationManager.GetTranslation(term, true, 0, true, false, null, null, true);
                return t ?? "";
            }
            catch { return ""; }
        }

        internal static void WriteInfoTypeValue(Utf8JsonWriter w, UpgradeInfo info)
        {
            if (info == null) return;
            var hero = info.TryCast<HeroInfo>();
            if (hero != null) { w.WriteStringValue(hero.Type.ToString()); return; }
            var passive = info.TryCast<PassiveInfo>();
            if (passive != null) { w.WriteStringValue(passive.Type.ToString()); return; }
            w.WriteStringValue(info.Slug);
        }

        internal static void WriteInfoType(Utf8JsonWriter w, string name, UpgradeInfo info)
        {
            if (info == null) { w.WriteNull(name); return; }
            var hero = info.TryCast<HeroInfo>();
            if (hero != null) { w.WriteString(name, hero.Type.ToString()); return; }
            var passive = info.TryCast<PassiveInfo>();
            if (passive != null) { w.WriteString(name, passive.Type.ToString()); return; }
            w.WriteString(name, info.Slug);
        }

        /// <summary>게임 안 레시피 표 (진화 재료). 연결될 때 한 번 보낸다.</summary>
        public static string BuildCatalog()
        {
            var db = InfoDB.I;
            if (db == null) return null;
            using var ms = new MemoryStream();
            using (var w = new Utf8JsonWriter(ms))
            {
                w.WriteStartObject();
                w.WriteNumber("v", PipeServer.ProtocolVersion);
                w.WriteString("plugin", Plugin.Version);
                w.WriteString("game_version", Application.version);
                w.WriteStartObject("catalog");
                try { w.WriteNumber("max_solo_lvl", UpgradeInst<HeroInfo>.kMaxSoloLvl); } catch { }
                WriteInfos(w, "balls", db.Heroes);
                WriteInfos(w, "passives", db.Passives);
                w.WriteStartArray("levels");
                try
                {
                    var lvls = db.Levels;
                    if (lvls != null)
                        for (int i = 0; i < lvls.Length; i++)
                        {
                            var li = lvls[i];
                            if (li == null) continue;
                            w.WriteStartObject();
                            w.WriteString("type", li.Type.ToString());
                            w.WriteStartArray("boss_turns");
                            if (li.BossTurns != null) for (int k = 0; k < li.BossTurns.Length; k++) w.WriteNumberValue(li.BossTurns[k]);
                            w.WriteEndArray();
                            w.WriteStartArray("fuser_turns");
                            if (li.FuserTurns != null) for (int k = 0; k < li.FuserTurns.Length; k++) w.WriteNumberValue(li.FuserTurns[k]);
                            w.WriteEndArray();
                            w.WriteNumber("turn_len", Math.Round(li.DefaultTurnLength, 3));
                            w.WriteEndObject();
                        }
                }
                catch { }
                w.WriteEndArray();
                w.WriteEndObject();
                w.WriteEndObject();
            }
            return Encoding.UTF8.GetString(ms.ToArray());
        }

        static void WriteInfos<T>(Utf8JsonWriter w, string name, Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<T> infos)
            where T : UpgradeInfo
        {
            w.WriteStartArray(name);
            if (infos != null)
            {
                for (int i = 0; i < infos.Length; i++)
                {
                    var info = infos[i];
                    if (info == null) continue;
                    w.WriteStartObject();
                    WriteInfoType(w, "type", info);
                    w.WriteBoolean("in_game", info.IsInGame);
                    WriteLevelProps(w, info);
                    w.WriteStartArray("recipes");
                    var comps = info.MergeComponents;
                    if (comps != null)
                    {
                        for (int r = 0; r < comps.Length; r++)
                        {
                            var recipe = comps[r];
                            if (recipe == null) continue;
                            w.WriteStartArray();
                            for (int k = 0; k < recipe.Length; k++)
                            {
                                var part = recipe[k];
                                if (part == null) continue;
                                var hero = part.TryCast<HeroInfo>();
                                var passive = part.TryCast<PassiveInfo>();
                                w.WriteStringValue(hero != null ? hero.Type.ToString()
                                    : passive != null ? passive.Type.ToString() : part.Slug);
                            }
                            w.WriteEndArray();
                        }
                    }
                    w.WriteEndArray();
                    w.WriteEndObject();
                }
            }
            w.WriteEndArray();
        }

        /// <summary>레벨별 수치 (피해 최소·최대, 상태 이상 피해 등). 게임 PropertiesByLvl 그대로.</summary>
        static void WriteLevelProps(Utf8JsonWriter w, UpgradeInfo info)
        {
            try
            {
                var byLvl = info.PropertiesByLvl;
                if (byLvl == null) return;
                w.WriteStartArray("lvl_props");
                for (int l = 0; l < byLvl.Length; l++)
                {
                    w.WriteStartObject();
                    var d = byLvl[l];
                    if (d != null)
                        foreach (var kv in d) w.WriteNumber(kv.Key.ToString(), kv.Value);
                    w.WriteEndObject();
                }
                w.WriteEndArray();
            }
            catch { }
        }

        static LevelUpBtn FindButton(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<LevelUpBtn> btns, int choiceIdx)
        {
            if (btns == null) return null;
            for (int i = 0; i < btns.Length; i++)
            {
                var b = btns[i];
                if (b != null && b.gameObject.activeInHierarchy && b.ChoiceIdx == choiceIdx) return b;
            }
            return null;
        }

        static readonly Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector3> Corners =
            new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector3>(4);

        /// <summary>UI 요소의 화면 사각형 (게임 클라이언트 영역 픽셀, 왼쪽 위 원점).</summary>
        /// <summary>1.11: 지금 화면에서 도우미 HUD 가 가리면 안 되는 게임 UI 영역 (글자·버튼·목록). 켜져 있는 것만.</summary>
        static void WriteUiAvoid(Utf8JsonWriter w)
        {
            w.WriteStartObject("ui");
            try
            {
                var cs = CharSelectUI.I;
                if (cs != null && cs.gameObject.activeInHierarchy)
                {
                    w.WriteString("screen", "char_select");
                    w.WriteBoolean("fusion", cs.IsSelectingFusion);
                    w.WriteStartArray("avoid");
                    RectIfActive(w, cs.CharGrid);           // 캐릭터 목록 (이름표 포함)
                    RectIfActive(w, cs.WrapperList);
                    RectIfActive(w, cs.DetailsPanel);       // 선택한 캐릭터 설명
                    RectIfActive(w, cs.WrapperDetailsBtns);
                    RectIfActive(w, cs.BtnSelect);
                    RectIfActive(w, cs.BtnClose);
                    RectIfActive(w, cs.WrapperLvlItems);
                    w.WriteEndArray();
                }
                else
                {
                    var ui = LevelUpUI.I;
                    if (ui != null && ui.gameObject.activeInHierarchy && ui.IsActiveOverlay())
                    {
                        w.WriteString("screen", ui.Type.ToString() == "kFuser" ? "fuser" : "levelup");
                        w.WriteStartArray("avoid");
                        RectIfActive(w, ui.PanelSelectionDetails);   // 오른쪽 설명 패널
                        RectIfActive(w, ui.WrapperCurHeroes);        // 볼 슬롯
                        RectIfActive(w, ui.WrapperCurPassives);      // 패시브 슬롯
                        RectIfActive(w, ui.BtnReroll);
                        RectIfActive(w, ui.BtnBanish);
                        RectIfActive(w, ui.WrapperFuserOptions);     // 융합 선택지
                        RectIfActive(w, ui.WrapperSelectEvo);
                        RectIfActive(w, ui.WrapperSelectCombo);
                        RectIfActive(w, ui.EvoSelectInfoPanel);
                        w.WriteEndArray();
                    }
                }
            }
            catch { }
            w.WriteEndObject();
        }

        static void RectIfActive(Utf8JsonWriter w, GameObject go)
        {
            if (go != null) RectIfActive(w, go.transform);
        }

        static void RectIfActive(Utf8JsonWriter w, Component comp)
        {
            if (comp == null || !comp.gameObject.activeInHierarchy) return;
            var rt = comp.TryCast<RectTransform>() ?? comp.GetComponent<RectTransform>();
            if (rt == null) return;
            rt.GetWorldCorners(Corners);
            var canvas = rt.GetComponentInParent<Canvas>();
            Camera cam = null;
            if (canvas != null)
            {
                canvas = canvas.rootCanvas;
                if (canvas.renderMode != RenderMode.ScreenSpaceOverlay) cam = canvas.worldCamera;
            }
            float x0 = float.MaxValue, y0 = float.MaxValue, x1 = float.MinValue, y1 = float.MinValue;
            for (int i = 0; i < 4; i++)
            {
                var p = RectTransformUtility.WorldToScreenPoint(cam, Corners[i]);
                x0 = Math.Min(x0, p.x); x1 = Math.Max(x1, p.x);
                y0 = Math.Min(y0, p.y); y1 = Math.Max(y1, p.y);
            }
            if (x1 - x0 < 2 || y1 - y0 < 2) return;
            int h = Screen.height;
            w.WriteStartArray();
            w.WriteNumberValue((int)Math.Round(x0));
            w.WriteNumberValue((int)Math.Round(h - y1));
            w.WriteNumberValue((int)Math.Round(x1 - x0));
            w.WriteNumberValue((int)Math.Round(y1 - y0));
            w.WriteEndArray();
        }

        static void WriteRect(Utf8JsonWriter w, string name, Component comp)
        {
            if (comp == null) return;
            var rt = comp.TryCast<RectTransform>() ?? comp.GetComponent<RectTransform>();
            if (rt == null) return;
            rt.GetWorldCorners(Corners);
            var canvas = rt.GetComponentInParent<Canvas>();
            Camera cam = null;
            if (canvas != null)
            {
                canvas = canvas.rootCanvas;
                if (canvas.renderMode != RenderMode.ScreenSpaceOverlay) cam = canvas.worldCamera;
            }
            float x0 = float.MaxValue, y0 = float.MaxValue, x1 = float.MinValue, y1 = float.MinValue;
            for (int i = 0; i < 4; i++)
            {
                var p = RectTransformUtility.WorldToScreenPoint(cam, Corners[i]);
                x0 = Math.Min(x0, p.x); x1 = Math.Max(x1, p.x);
                y0 = Math.Min(y0, p.y); y1 = Math.Max(y1, p.y);
            }
            int h = Screen.height;
            w.WriteStartArray(name);
            w.WriteNumberValue((int)Math.Round(x0));
            w.WriteNumberValue((int)Math.Round(h - y1));
            w.WriteNumberValue((int)Math.Round(x1 - x0));
            w.WriteNumberValue((int)Math.Round(y1 - y0));
            w.WriteEndArray();
        }
    }
    /// <summary>기지·누적 기록 (런 밖에서도). 5초마다 읽고 바뀌었을 때만 보낸다.</summary>
    internal static class Meta
    {
        public static string Build()
        {
            var m = MetaSaveData.I;
            if (m == null) return null;
            using var ms = new MemoryStream();
            using (var w = new Utf8JsonWriter(ms))
            {
                w.WriteStartObject();
                w.WriteNumber("v", PipeServer.ProtocolVersion);
                w.WriteString("plugin", Plugin.Version);
                w.WriteStartObject("meta");
                WriteInts(w, "resources", m.NumResources);
                w.WriteNumber("day", m.CurDay);
                w.WriteNumber("battles", m.NumBattlesPlayed);
                w.WriteNumber("boss_waves", m.NumBossWavesCompleted);
                w.WriteStartObject("lifetime");
                w.WriteNumber("kills", m.NumKills);
                w.WriteNumber("play_time", Math.Round(m.PlayTime));
                w.WriteNumber("harvests", m.NumHarvests);
                w.WriteNumber("elevator", m.ElevatorLvl);
                w.WriteNumber("buildings_built", m.NumBuildingsConstructed);
                w.WriteNumber("boss_blueprints", m.NumBossBlueprintsDropped);
                w.WriteEndObject();
                var bm = BuildingMgr.I;
                if (bm != null)
                {
                    w.WriteStartObject("bonuses");
                    w.WriteNumber("banishes", bm.NumBanishes);
                    w.WriteNumber("free_rerolls", bm.NumFreeRerolls);
                    w.WriteNumber("revives", bm.NumRevives);
                    w.WriteNumber("choices", bm.NumLvlUpChoices);
                    w.WriteNumber("ball_slots", bm.NumBallSlots);
                    w.WriteNumber("passive_slots", bm.NumPassiveSlots);
                    w.WriteBoolean("endless", bm.EndlessModeUnlocked);
                    w.WriteEndObject();
                }
                WriteBuildings(w, m);
                WriteHeroStats(w, m);
                WritePassiveStats(w, m);
                w.WriteStartArray("chars");
                var chars = m.Chars;
                if (chars != null)
                    for (int i = 0; i < chars.Length; i++)
                    {
                        var c = chars[i];
                        if (c == null || !c.IsUnlocked) continue;
                        w.WriteStartObject();
                        w.WriteString("type", c.Type.ToString());
                        w.WriteNumber("lvl", c.Lvl);
                        w.WriteNumber("battles", c.NumBattles);
                        try { w.WriteString("state", c.CurState.ToString()); } catch { }
                        try { w.WriteString("work", c.WorkerBuildingType.ToString()); w.WriteNumber("work_id", c.WorkerBuildingId); } catch { }
                        try
                        {
                            var hu = c.HarvestUpgrades;
                            if (hu != null && hu.Count > 0)
                            {
                                w.WriteStartObject("harvest");
                                for (int k = 0; k < hu.Count; k++) if (hu[k] != null) w.WriteNumber(hu[k].Type.ToString(), hu[k].Lvl);
                                w.WriteEndObject();
                            }
                        }
                        catch { }
                        try
                        {
                            if (c.BonusStats != null)
                            {
                                w.WriteStartArray("bonus_stats");
                                for (int k = 0; k < c.BonusStats.Length; k++) w.WriteNumberValue(c.BonusStats[k]);
                                w.WriteEndArray();
                            }
                        }
                        catch { }
                        w.WriteEndObject();
                    }
                w.WriteEndArray();
                WriteLevels(w, m);
                w.WriteEndObject();
                w.WriteEndObject();
            }
            return Encoding.UTF8.GetString(ms.ToArray());
        }

        /// <summary>지역별: 해금·완료, 무한의 심연 최고 기록, 이 지역을 깬 캐릭터, 아직 못 얻은 설계도.</summary>
        static void WriteLevels(Utf8JsonWriter w, MetaSaveData m)
        {
            var have = new HashSet<int>();
            var bps = m.Blueprints;
            if (bps != null)
                for (int i = 0; i < bps.Length; i++)
                    if (bps[i] != null && bps[i].HasBlueprint) have.Add((int)bps[i].TgtBuilding);
            var db = InfoDB.I;
            w.WriteStartArray("levels");
            var lv = m.LvlData;
            if (lv != null)
                for (int i = 0; i < lv.Length; i++)
                {
                    var d = lv[i];
                    if (d == null) continue;
                    w.WriteStartObject();
                    w.WriteString("type", d.Type.ToString());
                    try { w.WriteString("name", d.GetInfo().GetNameTranslation(false)); } catch { }
                    try { w.WriteBoolean("unlocked", d.IsUnlocked()); } catch { }
                    w.WriteBoolean("done", d.DidComplete);
                    w.WriteNumber("attempts", d.NumAttempts);
                    w.WriteNumber("best_endless", d.BestEndlessDepth);
                    w.WriteNumber("blueprint_attempts", d.NumBlueprintDropAttempts);
                    // 캐릭터별 최고 난이도·시도 횟수 원본 (배열 위치 = CharType). DidCompleteWithChar 는 실제 화면과 달랐다.
                    try
                    {
                        if (d.BestDifficultyByChar != null)
                        {
                            w.WriteStartArray("best_diff_by_char");
                            for (int k = 0; k < d.BestDifficultyByChar.Length; k++) w.WriteNumberValue(d.BestDifficultyByChar[k]);
                            w.WriteEndArray();
                        }
                        if (d.NumAttemptsByChar != null)
                        {
                            w.WriteStartArray("attempts_by_char");
                            for (int k = 0; k < d.NumAttemptsByChar.Length; k++) w.WriteNumberValue(d.NumAttemptsByChar[k]);
                            w.WriteEndArray();
                        }
                    }
                    catch { }
                    w.WriteStartArray("blueprints_left");
                    try
                    {
                        var byLvl = db != null ? db.BlueprintsByLevel : null;
                        int li = (int)d.Type;
                        if (byLvl != null && li >= 0 && li < byLvl.Length && byLvl[li] != null)
                            for (int b = 0; b < byLvl[li].Count; b++)
                            {
                                var info = byLvl[li][b];
                                if (info != null && !have.Contains((int)info.Type)) w.WriteStringValue(info.Type.ToString());
                            }
                    }
                    catch { }
                    w.WriteEndArray();
                    w.WriteEndObject();
                }
            w.WriteEndArray();
        }

        static void WriteInts(Utf8JsonWriter w, string name, Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<int> a)
        {
            w.WriteStartArray(name);
            if (a != null) for (int i = 0; i < a.Length; i++) w.WriteNumberValue(a[i]);
            w.WriteEndArray();
        }

        static void WriteCost(Utf8JsonWriter w, string name, Cost c)
        {
            if (c == null) return;
            WriteInts(w, name, c.Num);
        }

        static void WriteBuildings(Utf8JsonWriter w, MetaSaveData m)
        {
            var built = new HashSet<int>();
            w.WriteStartArray("buildings");
            var list = m.Buildings;
            if (list != null)
                for (int i = 0; i < list.Count; i++)
                {
                    var b = list[i];
                    if (b == null) continue;
                    built.Add((int)b.Type);
                    w.WriteStartObject();
                    w.WriteString("type", b.Type.ToString());
                    w.WriteNumber("lvl", b.UpgradeLvl);
                    w.WriteString("state", b.CurState.ToString());
                    try
                    {
                        bool can = b.CanBeUpgraded();
                        w.WriteBoolean("can_upgrade", can);
                        if (can) WriteCost(w, "upgrade_cost", b.GetUpgradeCost());
                    }
                    catch { }
                    w.WriteEndObject();
                }
            w.WriteEndArray();
            // 설계도는 있는데 아직 안 지은 건물 + 짓는 비용
            var infos = new Dictionary<int, BuildingInfo>();
            var db = InfoDB.I;
            if (db != null && db.Buildings != null)
                for (int i = 0; i < db.Buildings.Length; i++)
                    if (db.Buildings[i] != null) infos[(int)db.Buildings[i].Type] = db.Buildings[i];
            w.WriteStartArray("blueprints");
            var bps = m.Blueprints;
            if (bps != null)
                for (int i = 0; i < bps.Length; i++)
                {
                    var bp = bps[i];
                    if (bp == null || !bp.HasBlueprint || built.Contains((int)bp.TgtBuilding)) continue;
                    w.WriteStartObject();
                    w.WriteString("type", bp.TgtBuilding.ToString());
                    if (infos.TryGetValue((int)bp.TgtBuilding, out var info))
                    {
                        w.WriteString("slug", info.Slug);
                        w.WriteString("cat", info.Cat.ToString());
                        WriteCost(w, "cost", info.BuildCost);
                        try { w.WriteNumber("tw", info.TileSize.x); w.WriteNumber("th", info.TileSize.y); } catch { }
                        try { w.WriteString("col", info.ColType.ToString()); } catch { }
                        try { w.WriteString("stat", info.GetStatBonus().ToString()); } catch { }
                    }
                    w.WriteEndObject();
                }
            w.WriteEndArray();
        }

        static void WriteHeroStats(Utf8JsonWriter w, MetaSaveData m)
        {
            w.WriteStartObject("ball_stats");
            var a = m.HeroStats;
            if (a != null)
                for (int i = 0; i < a.Length; i++)
                {
                    var s = a[i];
                    if (s == null || (s.NumObtained == 0 && s.NumNewRejected == 0)) continue;
                    w.WriteStartObject(((HeroType)i).ToString());
                    w.WriteNumber("obtained", s.NumObtained);
                    w.WriteNumber("upgraded", s.NumUpgraded);
                    w.WriteNumber("rejected", s.NumNewRejected);
                    w.WriteNumber("completed", s.NumCompletedRuns);
                    w.WriteNumber("damage", s.TotalDamage);
                    w.WriteNumber("launches", s.TotalLaunches);
                    w.WriteEndObject();
                }
            w.WriteEndObject();
        }

        static void WritePassiveStats(Utf8JsonWriter w, MetaSaveData m)
        {
            w.WriteStartObject("passive_stats");
            var a = m.PassiveStats;
            if (a != null)
                for (int i = 0; i < a.Length; i++)
                {
                    var s = a[i];
                    if (s == null || (s.NumObtained == 0 && s.NumNewRejected == 0)) continue;
                    w.WriteStartObject(((PassiveType)i).ToString());
                    w.WriteNumber("obtained", s.NumObtained);
                    w.WriteNumber("upgraded", s.NumUpgraded);
                    w.WriteNumber("rejected", s.NumNewRejected);
                    w.WriteNumber("completed", s.NumCompletedRuns);
                    w.WriteEndObject();
                }
            w.WriteEndObject();
        }
    }
}
