# BALL x PIT Companion — Türkçe

[🌐 Languages](../../README.md#choose-your-language)

BALL x PIT için resmi olmayan bir Windows yardımcısı. Oyun durumunu okuyarak seviye atlama, birleştirme, ansiklopedi açılımları, hasat ve üs yerleşimi için öneriler sunar. Oyunu siz kontrol edersiniz.

## Kurulum

Windows 10/11 ve size ait bir Steam BALL x PIT kurulumu gerekir. [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) sayfasında ZIP varsa tamamını çıkarıp `BallxPitCompanion.exe` dosyasını çalıştırın; `_internal` klasörünü yanında tutun. Yayımlanmış sürüm yoksa aşağıdaki kaynak kodu kurulumunu kullanın. İmzasız program SmartScreen uyarısı verebilir.

Depoyu indirin, klasöründe PowerShell açın, uv kurun ve hazırlığı çalıştırın:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Ardından `run_overlay.bat` dosyasını başlatın. Kurulum bağımlılıkları ve BepInEx'i indirir, metin ve simgeleri sizin oyununuzdan çıkarır. Bağlantıyı kurmak veya güncellemek için oyundan normal şekilde çıkın; oyun açıkken kurulum bekler.

## Kullanım

- Ayarlarda bağlantıyı kontrol edin, ardından seviye atlama veya birleştirme ekranını açın.
- Keşiflere öncelik vermek için görüntü ayarlarından ansiklopedi modunu açın; varsayılan olarak kapalıdır.
- Mevcut ve önerilen yerleşimi karşılaştırıp binaları belirtilen sırayla elle taşıyın.
- Yollar ve hasat miktarları tahmindir. Birkaç açıdan erişim, tek atışta her şeyin toplanacağı anlamına gelmez.

## Gizlilik ve sınırlar

Bağlantı salt okunurdur: Harmony yaması, kayıt değişikliği veya oyuna girdi gönderimi yoktur. Günlükler ve çıkarılan veriler `%LOCALAPPDATA%\BallxPitCompanion` altında kalır; oyun kayıtları yüklenmez. Erken sürüm ağırlıklı olarak Windows 11, 1920×1080, Korece ve oyun 1.301 ile denenmiştir. En iyi yerleşim veya kesin gelecek DPS garantisi yoktur. Tüm çeviriler ana dili konuşan kişilerce incelenmemiştir. Resmi ürün değildir.

## Sorun giderme ve bildirim

Katman görünmüyorsa görünürlüğü, oyun penceresini ve bağlantıyı kontrol edin. [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) bölümünde sürümleri, dili, çözünürlüğü, adımları, beklenen ve gerçek sonucu belirtin. Görsellerden ve günlüklerden özel bilgileri kaldırın; kayıt dosyalarını veya çıkarılmış oyun varlıklarını yüklemeyin.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
