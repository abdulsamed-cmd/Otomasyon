# Otomasyon

İddaa futbol bülteninden **düşük riskli günlük kupon** üreten, ayrıca haftalık
bir **"6+ Gol laboratuvarı"** ve sistem senaryoları çalıştıran
bir asistan. **Otomatik oynama yapmaz** — kupon hazırlar, Telegram'dan
bilgilendirir ve sonuçları takip ederek performans metriklerini (isabet, ROI,
ortalama oran, kalibrasyon, kapanış oranına göre değer/CLV) hesaplar.

> Bilgilendirme amaçlıdır; bahis oynatmaz ve finansal tavsiye değildir.

## Durum (yol haritası)

- [x] Veri kaynağı keşfi — iddaa genel JSON API (`sportsbookv2.iddaa.com`)
- [x] Veri katmanı — istemci, normalize, SQLite depolama, adil olasılık
- [x] Güvenli kupon motoru (ana + alternatif, 2.00–3.00)
- [x] Sürpriz modülü (6+ Gol, sistem senaryoları)
- [x] Sürpriz aday/sonuç persistence + 2'li sistem teorik ROI kapısı
- [x] Telegram botu (`bugün` / `sürpriz` / `kadro` / `durum`, tek kullanıcı) + proaktif gönderim
- [x] Settlement + sonuç bildirimi + metrikler (isabet, ROI, ort. oran)
- [x] Kapanış oranı capture'ı ve seçim bazlı CLV
- [x] Otomatik sonuç kaynağı (Mackolik) + exact `iddaaCode` eşleştirme +
  15 dakikalık oto-settlement ve Telegram sonuç bildirimi
- [x] Bağlamsal gol modeli + opponent-adjusted Elo + pazar bazlı kronolojik backtest
- [x] İstatistiksel ROI kabul kapısı + bağımsız ClubElo rapor/validasyon katmanı
- [x] FotMob xG/doğrulanmış kadro capture katmanı (rapor modu)
- [x] Understat toplu tarihsel xG + `xg-ou-v1` ileriye dönük gölge model
- [x] Açık performans / şifreli kupon detaylı responsive web dashboard
- [x] Docker Compose ile kalıcı disk, bot ve dashboard servisleri
- [ ] Bağlamsal capture sonuçlarını biriktirme ve pozitif holdout kanıtı

### Komutlar

```bash
python3 -m otomasyon.cli fetch [--sample N]   # bülteni çek/sakla
python3 -m otomasyon.cli coupon               # günün ana + alternatif kuponu
python3 -m otomasyon.cli surprise             # sürpriz laboratuvarı
python3 -m otomasyon.cli bot                  # Telegram komut/chat botu
python3 -m otomasyon.cli scheduler            # planlı arşiv/bildirim/capture döngüsü
python3 -m otomasyon.cli push [--force]       # günün kuponunu proaktif gönder
python3 -m otomasyon.cli result --event ID --ft 2-1 [--ht 1-0]
python3 -m otomasyon.cli settle [--notify]    # sonucu gelen kuponları kapat + bildir
python3 -m otomasyon.cli auto-results [--force] [--notify] # Mackolik otomatik
python3 -m otomasyon.cli metrics              # isabet / ROI / ort. oran
python3 -m otomasyon.cli history-backfill --days 365
python3 -m otomasyon.cli history-auto --force
python3 -m otomasyon.cli backtest --test-days 30 --test-end 2026-04-30
python3 -m otomasyon.cli clubelo                       # canlı, rapor modu
python3 -m otomasyon.cli clubelo-backfill --start 2026-03-01 --end 2026-03-31
python3 -m otomasyon.cli clubelo-backtest --start 2026-03-01 --end 2026-03-31
python3 -m otomasyon.cli coupon-replay --start 2025-09-01 --end 2026-02-28
python3 -m otomasyon.cli coupon-replay --start 2026-03-01 --end 2026-03-31 \
  --calibrated --calibration-prior 400 --min-ev 0
python3 -m otomasyon.cli fotmob-context --detail-window 2 --detail-limit 30
python3 -m otomasyon.cli fotmob-lineup-backfill --per-team 1
python3 -m otomasyon.cli lineup-risk --high-rotation 5
python3 -m otomasyon.cli xg-backfill --season 2024 --season 2025
python3 -m otomasyon.cli shadow-predict
python3 -m otomasyon.cli shadow-metrics
python3 -m gunicorn --bind 0.0.0.0:8080 otomasyon.web:app
```

## Veri kaynağı

Kırılgan HTML kazıma yerine iddaa'nın **kimlik doğrulaması gerektirmeyen
JSON API'si** kullanılır:

| Amaç | Uç nokta |
| --- | --- |
| Futbol bülteni + oranlar | `GET /sportsbook/events?st=1&type=0&version=0` |
| Tek maç | `GET /sportsbook/event/{id}` |
| Pazar sözlüğü (isimler) | `GET /sportsbook/get_market_config` |
| Ligler | `GET /sportsbook/competitions?st=1` |

Pazar isimleri her zaman `get_market_config`'ten çözülür (kod: `f"{t}_{st}"`).
Maç sonuçları Mackolik canlı-sonuç JSON feed'inden alınır; aynı kaynak ileride
bağlamsal modelin geçmiş-veri katmanında da kullanılacaktır.

### Otomatik sonuç akışı

Mackolik canlı-sonuç sayfasının kullandığı JSON feed tarih bazında çekilir.
Bahis kapsamındaki kayıtlardaki `iddaaCode`, iddaa bültenindeki event id ile
doğrudan aynıdır; sonuçlar öncelikle bu kesin anahtarla eşleştirilir. Kod
bulunmayan istisnalarda yalnızca yüksek güvenli tarih+saat+takım benzerliği
yedeği kullanılır; belirsiz kayıtlar yanlış settlement yerine atlanır.
Modern feed geçici olarak erişilemezse arşiv feed'i yedek olur. Uzatma/penaltı
maçlarında normal süre bahisleri arşiv feed'indeki 90 dakika skoru ile
sonuçlandırılır; uzatma sonu görünen skor yanlışlıkla kullanılmaz.

Bağımsız scheduler çalışırken sonuç taraması 15 dakikada bir yapılır. Sonuçlanan
kupon kapatılır ve kullanıcıya otomatik bildirim gönderilir. Erteleme/iptal
seçimleri `void` kabul edilir ve efektif oranları `1.00` sayılır.

Scheduler ayrıca İstanbul saatiyle 04:00 sonrasında önceki günün **tüm** tamamlanmış
futbol maçlarını Mackolik arşivinden otomatik kaydeder. Son yedi gündeki
başarısız günler yeniden denenir; kaynak hatası alan gün tamamlanmış sayılmaz.
Bu tam günlük arşiv kupon dışındaki resmi maçları da model eğitimine ekler.
Hazırlık, genç ve rezerv kayıtları ham veride bulunsa bile eğitim filtresinden
geçemez.

Başarılı gece arşivi Telegram'da tarih, kaynak maç sayısı, toplam tarihsel
veri ve yeniden denenecek gün sayısıyla bir kez bildirilir. Gönderim başarısız
olursa tarih bildirilmiş sayılmaz ve sonraki bot döngüsünde yeniden denenir.

Her gün `09:45`te, `10:00` kuponundan önce ayrı bir model sağlık bildirimi
gönderilir. Ana/alternatif ROI ve %95 güven aralığı, CLV, xG gölge ROI/Brier,
6+ Gol isabet/ROI ve kanıt kapısı durumu bu raporda yer alır.
İzinli kullanıcı aynı raporu istediği anda Telegram'da `durum` yazarak alabilir;
bu istek planlı günlük bildirimin deduplication durumunu değiştirmez.

Bu endpoint'ler belgesiz/harici veri kaynaklarıdır. Kişisel ve düşük frekanslı
kullanım hedeflenir; ticari kullanım veya yeniden dağıtım öncesinde veri
lisansı/izin değerlendirmesi yapılmalıdır.

### Model güvenlik kapısı

Günlük düşük-risk süreci hazırlık, genç ve rezerv liglerini tamamen dışlar.
Bağlamsal model; zaman ağırlıklı form, lig bazlı ev/deplasman gol ortalaması,
takım hücum/savunma gücü ve Poisson pazar olasılıklarını hesaplar. Eğitim ve
test kronolojik olarak ayrılır; test dönemindeki hiçbir sonuç eğitime girmez.

Model varsayılan olarak yalnızca rapor/backtest modundadır
(`MODEL_LIVE_ENABLED = False`). En az 200 seçimde ROI'nin %95 güven aralığı
sıfırın üstüne çıkmadan kabul kapısı geçilemez. ClubElo da ayrı bir dış görüş
olarak aynı kurala tabidir. Bu kanıt oluşmadan iki model de Telegram kuponlarını
etkileyemez; mesajlar açıkça "piyasa tabanlı deneme kuponu" olarak etiketlenir.

### Günlük kupon replay sınırları

`coupon-replay`, üretimde kullanılan ana/alternatif kupon aramasını ve settlement
kodunu tarihsel maçlarda aynen çalıştırır. Arşiv yalnızca 1X2 ile Alt/Üst 2.5
oranlarını içerdiğinden Çifte Şans ve KG Var/Yok geçmiş replay'e dahil değildir.
Ayrıca oranlar 10:00 anlık görüntüsü değil, arşivdeki son maç önü oranlarıdır.
Komut bu nedenle sonucu açıkça `closing_odds_partial` olarak etiketler.

Yeni bülten çekimleri ayrıca tam bir point-in-time capture olarak saklanır.
Capture; o çekimde görülen etkinlik, pazar durumu, seçim ve oran üyeliğini
korur. `Database.load_bulletin_as_of(ts)` geçmişteki en son tam capture'ı
`NormalizedEvent` listesi olarak yeniden kurar. Böylece saat 10:00 capture'ları
biriktikçe Çifte Şans, KG Var/Yok ve tüm Alt/Üst çizgileriyle gerçek üretim
replay'i ileriye dönük olarak mümkün olur.

Geliştirme dönemi baz replay'inde ana kupon ROI'si `-%23,4`, alternatif ROI'si
`-%40,7` çıktı. Mart validasyonunda geçmiş dönem kalibrasyonu ve beklenen-değer
eşikleri de pozitif sonuç üretmedi. Bu aday canlıya alınmadı ve daha sonraki test
dönemi, başarısız kuralları test sonucuna göre ayarlamamak için açılmadı.

Günlük kupon ve sürpriz laboratuvarı ayrı modüllerdir. Sürpriz aday/sistem
kuralları günlük kupon motorunun pazar havuzunu veya seçimini değiştirmez.
Her iki süreç de hazırlık, genç ve rezerv liglerinin yanında takım adındaki
`II`, `B`, `2`, `Academy` ve `Uxx` rezerv işaretlerini dışlar.
6+ Gol adayları ve sistem senaryoları günlük kuponlardan bağımsız ölçülür.
Başka Alt/Üst çizgileri veya İY/MS seçimleri bu laboratuvara dahil edilmez.

### xG ve doğrulanmış kadro capture'ı

`fotmob-context`, güncel FotMob web API'sinden günlük fikstürü alır ve iddaa
maçlarıyla takım adı+saat üzerinden yalnız açık ara benzersiz eşleşmeleri
saklar. Başlama saatine yaklaşan maçlarda doğrulanmış 11'ler capture edilir;
maç sonrası oluşan xG ayrıca `started`/`finished` bayraklarıyla saklandığı için
maç önü tahmine sızamaz. Katman rapor modundadır ve kupon motoruna bağlı
değildir. İlk canlı ölçümde 757 iddaa maçının 426'sı eşleşmiş, incelenen 30
yakın maçın 12'sinde maç başlamadan doğrulanmış kadro bulunmuştur.
Scheduler çalışırken bu capture en fazla 30 dakikada bir otomatik yenilenir;
poll döngüsünün sık çalışması veri kaynaklarına ek yük oluşturmaz.
Oyuncu kimlikleri, pozisyonları ve mevcut piyasa değerleri ayrıca saklanır.
Önceki resmi maçın ilk 11'iyle devamlılık karşılaştırması yapılır; beş veya
daha fazla değişiklik şimdilik yalnız `kadro` komutunda risk uyarısı üretir ve
kupon seçimini değiştirmez.

### Tarihsel xG ve gölge model

Understat'ın cookie gerektiren güncel `/getLeagueData/{league}/{season}` JSON
endpoint'i EPL, La Liga, Bundesliga, Serie A, Ligue 1 ve RFPL sezonlarını toplu
alır. Maçlar Mackolik geçmişine saat, iki takım adı ve kesin skorla bağlanır.
2024/25 ve 2025/26 verilerinden 3.984 xG maçı çekilmiş, mevcut tarih aralığıyla
1.889 güvenli bağlantı kurulmuştur.

`xg-ou-v1`, yalnız cutoff öncesinde iki takım için de yeterli xG geçmişi varsa
Alt/Üst 2.5 tahmini kaydeder. Mart validasyonunda gol-only model ROI'sini
`-%15,6` seviyesinden xG ağırlıklı sürümde `-%3,4` seviyesine iyileştirmiştir;
ancak %95 güven aralığı sıfırın altına indiği için canlı seçim etkisi kapalıdır.
Günlük üretim sırasında tahminler `model_predictions` tablosuna gölge kayıt
olarak yazılır ve sonuç/ROI/Brier ileriye dönük takip edilir.

> **Sızıntı koruması:** Understat `dates[].forecast.w/d/l` alanı kullanılmaz.
> Bu değerler gelecek fikstürlerde boş olup yalnız maçta oluşan toplam xG'den
> sonuç sonrası hesaplanır. Pre-match tahmin gibi kullanılması yapay pozitif ROI
> üretir. Canlı kupon ve backtest kodunda bu alan için tablo/komut bulunmaz.

## Web dashboard

`/` yalnız toplu ve sonuçlanmış verileri gösterir: ana, alternatif ve sürpriz
için ayrı kupon sayısı, isabet, ortalama oran, ROI ve kanıt kapısı. Açık
response'a takım, maç, pazar veya seçim alanları gönderilmez.

`/coupons` ve `/coupons/<id>` şifreli oturum gerektirir. Telegram'da paylaşılan
bekleyen ve geçmiş kuponların takım/pazar/seçim detayları yalnız burada görünür.
`/surprises` ise haftalık adayları ve her sistem boyutunun teorik maliyet/ROI
sonucunu ayrı bir özel arşivde tutar. Sürpriz kanıt kapısı yalnız sabit 2'li
sistem senaryosunu takip eder; farklı sistem boyutlarının sonuçları birbirine
karıştırılmaz.
Şifre ve Flask oturum anahtarı sırasıyla `DASHBOARD_PASSWORD` ve
`DASHBOARD_SECRET_KEY` secret'larından okunur. HTTPS arkasında
`DASHBOARD_SECURE_COOKIE=1` kullanılmalıdır.

## Kalıcı deployment

```bash
cp .env.example .env
# .env içindeki gerçek Telegram ve dashboard secret'larını doldurun.
docker compose up -d --build
curl http://localhost:8080/healthz
```

Compose üç servis çalıştırır: Gunicorn dashboard, Telegram komut botu ve bağımsız
scheduler. Üçü `otomasyon-data` adlı kalıcı volume üzerindeki aynı WAL-mode SQLite
veritabanını kullanır. Bot ve scheduler dashboard health check'i geçmeden başlamaz.
Sunucu yeniden başladığında servisler `unless-stopped` politikasıyla geri gelir.

## Kurulum ve çalıştırma

```bash
python3 -m pip install -r requirements-dev.txt

# Bugünkü futbol bültenini çek, SQLite'a yaz ve 3 örnek maç göster
python3 -m otomasyon.cli fetch --sample 3
```

Veritabanı varsayılan olarak `data/otomasyon.db` (git'e dahil değil).
`OTOMASYON_DB` ortam değişkeni ile yol değiştirilebilir.

## Testler

```bash
python3 -m pytest -q
```

## Yapı

```
otomasyon/
  config.py            # sabitler, saat dilimi (Europe/Istanbul), pazar kodları
  probability.py       # implied/fair (marj çıkarma) olasılık yardımcıları
  iddaa/
    client.py          # iddaa JSON API istemcisi
    markets.py         # (t,st) -> pazar adı çözücü
    normalize.py       # ham JSON -> tipli domain nesneleri
  storage/
    schema.sql         # SQLite şeması (event/market/oran geçmişi/kupon/sonuç)
    db.py              # kalıcılık katmanı
  cli.py               # `fetch` komutu
tests/                 # pytest
```

## Gizli anahtarlar (secrets)

Telegram için kod, ortam değişkenlerinden okur — repoda **token tutulmaz**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USERNAME` (yalnızca bu kullanıcı botla konuşabilir)
