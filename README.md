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
- [x] Güvenli kupon motoru (ana + alternatif, hedef oran bandı, serbest bacak sayısı)
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
python3 -m otomasyon.cli xg-auto --force
python3 -m otomasyon.cli coupon --rebuild     # maçlar başlamadıysa günün kuponunu yenile
python3 -m otomasyon.cli calibration          # piyasa/model ağırlıklarını yeniden ölç
python3 -m otomasyon.cli model-refresh --force
python3 -m otomasyon.cli shadow-predict
python3 -m otomasyon.cli shadow-metrics
python3 -m otomasyon.cli walk-forward --start 2025-10-01 --end 2026-03-31
python3 -m otomasyon.cli walk-forward-metrics --min-edge 0.04
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

### Telegram bağlantı dayanıklılığı

Uzun yoklama (long polling), Telegram'ın gönderecek mesajı yokken TCP
bağlantısını açık tutar. NAT ağ geçitleri bu boşta bağlantıları sessizce
düşürür: istemci okuma zaman aşımı dolana kadar bekler ve mesajlar bu süre
boyunca Telegram tarafında görülmeden kuyrukta durur. Bu, botun "çalışıyor
ama cevap vermiyor" görünmesinin başlıca nedenidir.

Bu yüzden yoklama katmanı şu değişmezlere dayanır:

- Yoklama süresi `TELEGRAM_POLL_TIMEOUT_SECONDS` ile sınırlıdır; düşen bir
  bağlantı mesajları en fazla bu süre kadar gizleyebilir.
- Soketler TCP keepalive sondaları gönderir, böylece kopan bağlantı okuma
  zaman aşımından çok önce fark edilir.
- Başarısız yoklama havuzdaki soketi emekliye ayırır; sonraki deneme yeni
  bağlantı kurar ve saniyenin altında yeniden denenir.
- `telegram_bot_runtime.last_poll_ts` yalnızca tamamlanan bir gidiş-dönüşte
  ilerler, bu yüzden "ayakta ama Telegram'a ulaşamıyor" durumu ölçülebilir.
- Scheduler bu damgayı izler; bot `TELEGRAM_WATCHDOG_STALE_SECONDS` boyunca
  sessiz kalırsa kullanıcıya bir kez uyarı, döndüğünde bir kez de düzelme
  bildirimi gönderilir.
- Kilit süresi kısadır: ani çöken bir süreç kilidini bırakamaz, bu yüzden
  devralma dakikalar değil saniyeler alır.
- Yanıt üretilemezse (kaynak erişilemez) komut sessizce düşürülmez; hata
  bildiren bir yanıt kuyruğa alınır. Sessizlik, ölü bir bottan ayırt edilemez.
- Taşıma katmanında otomatik yeniden deneme **kapalıdır**. Her iç deneme zaman
  aşımını sıfırdan başlattığı için tek bir yoklama dakikalarca sürebiliyordu;
  ölçümde 117 ve 710 saniyelik denemeler görüldü. Bunun yerine bot hatadan
  sonra bağlantıyı kendisi tazeler, böylece tek deneme bağlanma + okuma
  süresiyle sınırlı kalır.

### Sunucu duruşları: koddan bağımsız tek gerçek sınır

Bot ~15 saniyede bir yoklama yapar, scheduler ~25 saniyede bir uyanır. Bunlar
ayrı süreçlerdir ve biri diğerini durduramaz. Bu yüzden **ikisinin aynı anda ve
aynı süre boyunca susması** uygulama hatası değildir: makinenin kendisi
çalışmayı durdurmuştur.

Ölçülen örnek (9 Ağustos): 18:54'te 2,1 dakika ve 19:11'de 12,0 dakika boyunca
her iki süreç de hiç çalışmadı; scheduler'da aynı gün 36, 70, 85 ve 167
dakikalık duruşlar da görüldü. Bu pencerelerde gelen Telegram mesajı hiçbir
kodla karşılanamaz, çünkü karşılayacak süreç çalışmıyordur. Makine geri
döndüğünde boştaki TCP bağlantısı Telegram tarafından kapatılmış olur ve ilk
yoklama `RemoteDisconnected` ile döner — geç kalmanın sebebi değil, sonucudur.

`otomasyon.service.host_downtime` bu pencereleri iki sürecin kayıtlarını
karşılaştırarak tespit eder ve `durum` raporunun ilk satırında gösterir.
Kalıcı çözüm koddan değil dağıtımdan gelir: otomasyonun 7/24 açık bir
sunucuda (`docker compose up -d`) çalıştırılması gerekir.

Süreç ölümüne karşı koruma süreç dışındadır: Compose'da `restart:
unless-stopped`, elle çalıştırmada ise botu bir yeniden başlatma döngüsüne
sarın:

```bash
until python3 -u -m otomasyon.cli bot; do sleep 2; done
```

Kasıtlı durdurma (Ctrl-C) yeniden başlatma sayılmaz; yalnızca beklenmedik
sonlanmalar yeniden başlatılır.

Her gün `09:45`te, `10:00` kuponundan önce ayrı bir model sağlık bildirimi
gönderilir. Ana/alternatif ROI ve %95 güven aralığı, CLV, xG gölge ROI/Brier,
6+ Gol isabet/ROI ve kanıt kapısı durumu bu raporda yer alır.
İzinli kullanıcı aynı raporu istediği anda Telegram'da `durum` yazarak alabilir;
bu istek planlı günlük bildirimin deduplication durumunu değiştirmez.

Gece scheduler sırası sabittir: tam sonuç arşivi → güncel/önceki sezon
Understat xG senkronizasyonu → xG/Mackolik eşleştirme → günlük model eğitimi →
09:45 sağlık raporu → 10:00 kupon. Eğitim ancak önceki gün arşivi ve o günkü
xG senkronizasyonu başarılıysa `model_training_runs` tablosuna `ready` olarak
kaydedilir. Sağlık raporu son eğitim zamanı ile kullanılan toplam/xG maç
sayılarını gösterir.

Günlük eğitim iki ayrı artifact üretir: `goal-elo-ou-v1` tüm yeterli güvenli
resmi Alt/Üst 2.5 maçlarında gölge kanıt toplar; `xg-ou-v1` yalnız iki takımda
da yeterli geçmiş xG varsa ek tahmin üretir. Artifact'ler SHA-256 ile
doğrulanır ve takım/venue özellikleri `model_team_features` tablosunda
sürümlenir. Saat 10:00 tahminleri geceki cache'i kullanır; modellerin sonuç ve
kanıt kapıları birbirine karıştırılmaz.

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

### Kupon nasıl kuruluyor

Her kupona tek bir soru soruluyor: **en az şu kadar ödeyen kurgular arasında
en olası olan hangisi?** Ana kupon en az `1.50`, alternatif en az `2.00`
ödemek zorunda; üst sınır yok. Bacak sayısı `1`'den `5`'e kadar serbest ve her
biri ayrı ayrı aranıp karşılaştırılıyor — kısa kupon önce denenip orada
durulmuyor.

Pratikte tek bacak çoğu zaman kazanıyor, ama bu bir kural değil ölçüm sonucu:
her ek bacak kupona bir pazar marjı daha ekler, bu yüzden aynı ödemede uzun
kupon daha seyrek tutar. Arşivdeki 75 bin maçta `1.85–2.15` fiyatlı tek bir
seçim `%41,6` tutarken, aynı 2.00'ı veren iki `~1.41` bacak `%32,7` tutuyor.
Her bacak fiyatının tabanın altında kaldığı günlerde ise arama kendiliğinden
çoklu bacağa geçiyor.

### Hedef fiyat: ürünün asıl ayarı

Motor her zaman **tabana** oturur, çünkü tutma olasılığı fiyatla birlikte
tekdüze düşer. Yani taban fiyat bir aralık değil, ürünün kendisidir.

Arşivdeki 37 bin tek bacaklık seçimde ölçülen denge:

| taban | gerçek tutma | yatırılan paranın geri dönüşü |
| ----- | ------------ | ----------------------------- |
| 1.40  | %56,4        | %82,9                         |
| 1.60  | %49,4        | %82,7                         |
| 1.85  | %43,0        | %82,6                         |
| 2.20  | %35,7        | %81,2                         |

Kritik olan sağ sütun: geri dönüş her fiyatta aynı. Pahalı fiyat "daha
değerli" değil; taban yalnızca tutma sıklığı ile ödeme büyüklüğü arasında
seçim yaptırır. Tabanı ölçmek için:

```bash
python3 -m otomasyon.cli coupon-replay --start 2025-09-01 --end 2026-08-09 \
  --main-min-odds 1.40 --alt-min-odds 2.00
```

Havuz yalnız ucuz ve sonuçlandırabildiğimiz pazarlardan beslenir: canlı
bültende Maç Sonucu, Alt/Üst, Karşılıklı Gol ve Çifte Şans `%18` marjla
fiyatlanırken kombinasyon pazarları (skor+gol, handikap, İY/MS, gol bandı)
aynı para için `%21–24` istiyor; bunlar dışarıda kalır. Çifte Şans'ın üç
seçimi ikişer sonucu kapsadığından adil olasılıkları 1'e değil 2'ye toplanır —
bunu 1'e normalize etmek bültenin en güvenli pazarını yarı olasılıkla
gösterip her eşiğin arkasına saklıyordu.

### Kalibrasyon katmanı

`calibration` komutu piyasa fiyatını ve kendi modelimizi log-odds ekseninde
rakip iki görüş olarak birleştirir ve her birinin ağırlığını **ölçer**. Ölçüm
kendi holdout'unda yapılır: katsayıları uyduran veri, onları puanlayan veriden
önce biter.

Bugünkü sonuç açık: piyasa ağırlığı `1,14` / `1,11`, model ağırlığı `-0,06` /
`-0,00`, modelin log kaybına katkısı `+0,00004` düzeyinde. Yani model, fiyatın
içinde olmayan bir bilgi taşımıyor ve kupon seçimine girmiyor. Aynı ölçüm
modelin aynı fiyattaki adayları sıralayamadığını da gösteriyor (kova içi
AUC `0,483`, yani rastgeleden iyi değil). Katman kalıcı olarak bağlıdır: model
holdout'ta ölçülebilir katkı üretmeye başladığı gün ağırlığı kendiliğinden
alır, kod değişmesi gerekmez.

### Bağlam: ne aradık, ne bulduk

"Model bizim göremediğimizi görsün" fikri bir ölçüm sorusudur, ve bu depoda
ölçüldü. Aşağıdaki her satır, piyasa fiyatının **üstüne** ne kattığını kendi
holdout'unda verir. Hiçbiri kayda değer bir şey katmadı:

| bağlam                                   | veri            | fiyat üstüne katkısı |
| ---------------------------------------- | --------------- | -------------------- |
| dinlenme günü, fikstür yoğunluğu, form   | 62 bin maç      | `+0,00005` log kaybı |
| aynısı, lig büyüklüğüne göre ayrılmış    | 4 dilim         | her dilimde `≈0`     |
| gol modeli (Poisson + Elo + xG)          | 63 bin seçim    | her veri diliminde negatif |
| maç günü yağmuru ve rüzgârı              | 24 bin maç      | `1x2 +0,00007`, `ou25 −0,00111` |

Yağmurun **gerçek** ama küçük bir etkisi var: aynı lig içinde yağmurlu maçlarda
2.5 Üst piyasadan `+2,3` puan daha sık geliyor (`2,1` sigma). Ancak bu, kupon
sonucunu değiştirecek kadar büyük değil. Aralık 2025 – Ağustos 2026 replay'inde
hava bilgisi ana kuponu `%53,0`'ten `%56,3`'e çıkarmış *görünüyor*; fakat aynı
dağılımdan çekilmiş **sahte** hava verisiyle yapılan 8 plasebo koşusu `%48,7` ile
`%57,0` arasında sonuç veriyor. Yani iyileşme havadan değil, 151 kuponluk bir
örneklemde sıralamayı sarsmaktan geliyor.

Kadro, sakat ve cezalı bilgisi ise ölçülemedi çünkü **kupon saatinde yok**:
FotMob'da kesin 11'ler (`standard`) ancak maç başlarken beliriyor, saat 10:00'da
elde en fazla "tahmini kadro" oluyor — o da herkese açık ve fiyata girmiş.

Bu yüzden kupon şu an fiyat alıcıdır. Ölçüm makinesi yerinde duruyor: her gece
kalibrasyon katmanı piyasayı, modeli ve bağlamı yeniden tartıyor, bunlardan biri
holdout'ta ölçülebilir katkı üretmeye başladığı gün ağırlığını kendiliğinden
alıyor.

### Maç günü hava verisi

`weather` komutu iki adımda çalışır: bir takımın stadyum koordinatını FotMob'dan
bir kez çözer, sonra Open-Meteo'ya o noktada gökyüzünün ne yaptığını (ya da ne
yapacağını) sorar. Open-Meteo anahtar istemez ve hem geçmiş arşivi hem tahmini
verir — aynı özelliğin geçmiş maçlarda ölçülüp yarınki maç için söylenebilmesini
sağlayan şey budur.

Şu an `805` stadyum ve `423` binden fazla günlük yağmur/rüzgâr kaydı var.
Zamanlayıcı bunu 6 saatte bir tazeler; koordinat bir kez çözülür ve bir daha
istek harcamaz.

```bash
python3 -m otomasyon.cli weather --venue-limit 60 --days 3
```

Günlük kupon ve sürpriz laboratuvarı ayrı modüllerdir. Sürpriz aday/sistem
kuralları günlük kupon motorunun pazar havuzunu veya seçimini değiştirmez.
Her iki süreç de hazırlık, genç, amatör/bölgesel ve rezerv liglerin yanında
takım adındaki `II`, `B`, `2`, `Academy` ve `Uxx` rezerv işaretlerini dışlar.
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

### Walk-forward tahmin arşivi

`walk-forward`, her yerel gün için modeli yalnız önceki günlerin verisiyle
yeniden kurar; aynı günün sonucu/xG'si tahmine giremez. Her xG kapsamlı resmi
maç için tek Alt/Üst 2.5 tahmini, model/piyasa olasılığı, edge, oran, sonuç,
profit ve Brier girdileri kalıcı saklanır.

İlk sabit dönem (`2025-10-01..2026-03-31`) 108 günlük model ve 835 bağımsız
tahmin üretmiştir. `%4+` edge alt kümesi 574 tahminde `-%7,6` ROI
(`%95: -%15,4..+%0,3`) ve `0,2523` Brier vermiş; piyasa Brier'ı `0,2478` ile
daha iyi kalmıştır. Bu nedenle `xg-ou-v1` canlı kapısı kapalıdır.

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
