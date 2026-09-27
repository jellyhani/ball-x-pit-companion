// 원본 레벨 표와 현재 런의 읽기 전용 getter 결과를 구분한다.
using System;
using System.IO;
using System.Text;
using System.Text.Json;

namespace BallxPitBridge
{
    internal static class EffectiveProperties
    {
        // 게임의 targetLevel은 0부터, 도우미에 보내는 level은 1부터 시작한다.
        // 실패한 계산을 부분 JSON으로 보내지 않도록 임시 버퍼가 완성된 뒤에만 붙인다.
        internal static void Write(Utf8JsonWriter w, UpgradeInfo info, int targetLevel, bool isNew, int equipmentIndex)
        {
            try
            {
                if (info == null || targetLevel < 0 || info.PropertiesByLvl == null || info.PropertiesByLvl.Length == 0)
                    return;
                // 복합 볼의 GetProperty는 성분별 별도 보정이 있다. 단독 후보와 혼용하지 않는다.
                if (!isNew && info.TryCast<HeroInfo>() != null)
                {
                    var heroes = BattleSaveData.I?.Heroes;
                    if (heroes == null || equipmentIndex < 0 || equipmentIndex >= heroes.Count || heroes[equipmentIndex].IsCombo())
                        return;
                }
                using var ms = new MemoryStream();
                using (var ew = new Utf8JsonWriter(ms))
                {
                    ew.WriteStartObject();
                    ew.WriteString("scope", "current_run_uncombined");
                    ew.WriteNumber("level", targetLevel + 1);
                    Row(ew, "after", info, targetLevel);
                    if (!isNew && targetLevel > 0)
                        Row(ew, "before", info, targetLevel - 1);
                    ew.WriteEndObject();
                }
                w.WritePropertyName("effective");
                w.WriteRawValue(Encoding.UTF8.GetString(ms.ToArray()), true);
            }
            catch (Exception e)
            {
                w.WriteString("effective_error", e.GetType().Name);
            }
        }

        static void Row(Utf8JsonWriter w, string key, UpgradeInfo info, int level)
        {
            var raw = info.PropertiesByLvl[Math.Min(level, info.PropertiesByLvl.Length - 1)];
            w.WriteStartObject(key);
            if (raw != null)
            {
                foreach (var pair in raw)
                    w.WriteNumber(pair.Key.ToString(), info.GetPropertyByLvl(pair.Key, level, 0));
            }
            w.WriteEndObject();
        }
    }
}
