# mAistro Moderator Agent - Mimari Dokumantasyon

## 1. Proje Genel Bakis

**mAistro Moderator Agent**, Azure OpenAI Realtime API kullanarak konferanslari canli olarak modere eden bir AI agent'tir. Turkce konusur, konusmacilari tanistirr, sure takibi yapar, Q&A yonetir, acilis/kapanis konusmasi yapar.

**Teknoloji Yigini:**
- Backend: Python 3.11 + FastAPI
- Frontend: Vanilla TypeScript + Vite
- AI: Azure OpenAI Realtime API (gpt-4o-realtime)
- Iletisim: WebRTC (ses) + WebSocket (kontrol)
- State Management: `transitions` library (Python), Custom Store (TypeScript)

---

## 2. Uygulama Mimarisi

### 2.1. Genel Akis Diyagrami

```
+------------------+          WebRTC (Audio)           +-------------------+
|                  | =================================> |                   |
|   Browser        |          SDP Exchange              |  Azure OpenAI     |
|   (TypeScript)   | <================================= |  Realtime API     |
|                  |                                    |                   |
|   webrtc.ts      |                                    |  gpt-4o-realtime  |
|   app.ts         |                                    |                   |
|   store.ts       |          WebSocket                 |                   |
|   server-ws.ts   | <==============================>   |                   |
|                  |    |                               +--------^----------+
+------------------+    |                                        |
                        |                                        |
                        v                                        |
               +------------------+      Sideband WebSocket      |
               |                  | <===========================>|
               |  FastAPI Server  |    (session.update,           |
               |  (Python 3.11)  |     response.create,          |
               |                  |     function calls)           |
               |  main.py         |                              |
               |  handler.py      |                              |
               |  state_machine   |                              |
               |  timer           |                              |
               |  events.py       |                              |
               |  session.py      |                              |
               |  sideband.py     |                              |
               +------------------+                              |
```

### 2.2. Baglanti Topolojisi

1. **Browser → Azure OpenAI (WebRTC)**: Dogrudan ses baglantisi. Mikrofon sesi Azure'a gider, AI yaniti ses olarak geri gelir. ~400-600ms latency.

2. **Browser → FastAPI Server (WebSocket)**: Kontrol kanali. Agenda yukleme, konferans baslatma, duraklatma, sonraki oturum, override mesaj.

3. **FastAPI Server → Azure OpenAI (Sideband WebSocket)**: Ayni session'a `call_id` ile baglanan ikinci kanal. Server bu kanal uzerinden:
   - `session.update` ile prompt'lari gunceller
   - `response.create` ile AI'yi konusturur
   - Function call sonuclarini gonderir
   - `turn_detection` yapilandirmasini degistirir

### 2.3. Neden Bu Topoloji?

- **Dusuk Latency**: Browser dogrudan Azure'a baglanir, server uzerinden ses gecmez
- **Server Kontrolu**: Sideband ile server prompt'lari ve davranislari kontrol eder
- **Guvenlik**: Ephemeral token browser'a verilir (kisa omurlu), API key server'da kalir

---

## 3. Backend Mimarisi

### 3.1. Modul Yapisi

```
server/
├── main.py                 # FastAPI uygulamasi, CORS, routes
├── config.py               # Pydantic Settings, Azure yapilandirma
│
├── models/                 # Veri modelleri
│   ├── agenda.py           # SessionType, SpeakerInfo, ConferenceSession, ConferenceAgenda
│   ├── state.py            # ConferenceState enum, ConferenceContext dataclass
│   └── messages.py         # WebSocket mesaj tipleri (Client/Server)
│
├── conference/             # Konferans is mantigi
│   ├── state_machine.py    # 13 state, 21 transition, async callback'ler
│   ├── agenda_manager.py   # Agenda yukleme, dogrulama, sorgulama
│   ├── timer.py            # asyncio tabanli precision timer
│   └── tools.py            # 4 function calling tool tanimi + handler
│
├── realtime/               # Azure OpenAI entegrasyonu
│   ├── session.py          # Ephemeral token olusturma, session yenileme
│   ├── sideband.py         # WebSocket sideband baglantisi
│   └── events.py           # Realtime API olaylarini state machine'e kopruleme
│
├── prompts/                # Turkce prompt sistemi
│   ├── system.py           # Temel sistem prompt'u (kimlik, kurallar)
│   ├── templates.py        # 13 state-specific template
│   └── builder.py          # Dinamik prompt olusturucu
│
├── ws/                     # WebSocket handler
│   └── handler.py          # Browser baglantisi orchestrator
│
└── utils/
    └── logger.py           # structlog yapilandirmasi
```

### 3.2. State Machine (Durum Makinesi)

```
                    +--------+
                    |  IDLE  |
                    +---+----+
                        | start_conference
                        v
                    +--------+
                    |OPENING |
                    +---+----+
                        | opening_complete
                        v
                  +--------------+
           +----->|TRANSITIONING |<-----------+
           |      +------+-------+            |
           |             |                    |
           |    +--------+--------+           |
           |    |        |        |           |
           |    v        v        v           |
           | +----+  +-----+  +------+       |
           | |INTR|  |CLOSE|  |BREAK |       |
           | |ODUC|  |     |  |ANNC. |       |
           | +--+-+  +--+--+  +--+---+       |
           |    |       |        |            |
           |    v       v        v            |
           | +------+ +----+ +------+        |
           | |SPKR  | |ENDED| |BREAK |        |
           | |ACTIVE| |     | |ACTIVE|        |
           | +--+---+ +-----+ +--+---+        |
           |    |                 |            |
           |    v                 v            |
           | +------+        +-------+        |
           | |TIME  |        |BREAK  |        |
           | |WARN  |        |ENDING |        |
           | +--+---+        +---+---+        |
           |    |                 |            |
           |    v                 |            |
           | +------+            |            |
           | |THANK |            |            |
           | |SPKR  |            |            |
           | +--+---+            |            |
           |    |                 |            |
           +----+-----------------+------------+
```

**13 Durum:**
| Durum | Aciklama | AI Davranisi |
|-------|----------|-------------|
| IDLE | Konferans baslamadi | Sessiz |
| OPENING | Acilis konusmasi | Konusuyor |
| INTRODUCING_SPEAKER | Konusmaci tanitimi | Konusuyor |
| SPEAKER_ACTIVE | Konusmaci sunumda | Sessiz (dinliyor) |
| INTERACTING | Q&A / dialog modu | Konusuyor |
| TIME_WARNING | Sure uyarisi | Konusuyor |
| THANKING_SPEAKER | Konusmaciya tesekkur | Konusuyor |
| TRANSITIONING | Oturumlar arasi gecis | Router (yonlendirici) |
| BREAK_ANNOUNCEMENT | Mola duyurusu | Konusuyor |
| BREAK_ACTIVE | Mola devam ediyor | Sessiz |
| BREAK_ENDING | Mola bitiyor | Konusuyor |
| CLOSING | Kapanis konusmasi | Konusuyor |
| ENDED | Konferans bitti | Sessiz |

**21 Gecis (Transition):**
- Her gecis `trigger` (tetikleyici), `source` (kaynak durum), `dest` (hedef durum) icerir
- `operator_next` gecisi 5 farkli durumdan tetiklenebilir (operator override)
- TRANSITIONING durumu akilli bir router: sonraki oturum tipine gore dogru duruma yonlendirir

### 3.3. Timer Sistemi

```
SessionTimer
├── start() → asyncio.Task olusturur
├── stop() → Task iptal eder
├── pause() / resume() → Duraklama destegi
└── _run() → Her saniye:
    ├── Tick callback'leri cagir (browser'a gonder)
    ├── %80 esiginde → state_machine.handle_time_warning()
    └── Sure bittiginde → state_machine.handle_time_expired()
```

### 3.4. Prompt Sistemi

Her state degisiminde prompt yeniden olusturulur:

```
Nihai Prompt = BASE_SYSTEM_PROMPT + STATE_TEMPLATE + [PANEL_ADDENDUM] + [QA_ADDENDUM]
```

- **BASE_SYSTEM_PROMPT**: Kimlik, temel kurallar, konferans bilgileri, arac tanimlari
- **STATE_TEMPLATE**: O duruma ozel talimatlar (13 template)
- **PANEL_ADDENDUM**: Panel oturumlarinda eklenir (panelist yonetim kurallari)
- **QA_ADDENDUM**: Q&A oturumlarinda eklenir (soru yonetim kurallari)

Placeholder degiskenleri: `{speaker_name}`, `{talk_title}`, `{duration_minutes}`, `{minutes_remaining}`, vb.

### 3.5. Function Calling (Tool Use)

AI model 4 arac kullanabilir:

| Arac | Amac | Parametreler |
|------|------|-------------|
| `advance_to_next_session` | Sonraki oturuma gec | reason: speaker_finished, time_expired, break_over, operator_skip |
| `check_time_remaining` | Kalan sureyi kontrol et | - |
| `get_session_info` | Oturum bilgisi al | which: current / next |
| `announce_time_warning` | Sure uyarisi ver | minutes_remaining: number |

### 3.6. Azure OpenAI Entegrasyonu

**Session Manager (`session.py`):**
- Ephemeral token olusturma (REST API)
- 55. dakikada otomatik yenileme (60 dk timeout oncesi)
- `api-key` header auth

**Sideband Connection (`sideband.py`):**
- WebSocket uzerinden ayni session'a baglanma (`call_id` ile)
- Event dinleme (listen loop)
- Yuksek seviyeli metodlar: `update_session()`, `create_response()`, `send_function_call_output()`, `cancel_response()`

**Event Handler (`events.py`):**
- Realtime API olaylarini state machine aksiyonlarina cevirir
- `response.done` → state ilerletme
- `response.function_call_arguments.done` → tool calistirma
- `input_audio_buffer.speech_started/stopped` → moderator durumu guncelleme
- `response.audio_transcript.done` → transcript browser'a gonderme

---

## 4. Frontend Mimarisi

### 4.1. Modul Yapisi

```
client/src/
├── main.ts              # Entry point (App olustur ve basalt)
├── app.ts               # Ana orchestrator (445 satir)
│   ├── setupServerHandlers()   # WebSocket mesaj routing
│   ├── setupUIHandlers()       # DOM event binding
│   ├── render()                # State -> DOM guncelleme
│   ├── renderSpeakerCard()     # Konusmaci karti
│   ├── renderTimer()           # Zamanlayici gosterim
│   ├── renderControls()        # Buton durumu
│   ├── renderTranscript()      # Konusma metni
│   └── renderAgendaList()      # Program listesi
│
├── audio/
│   └── webrtc.ts        # WebRTC baglantisi (Azure OpenAI)
│       ├── connect()           # Peer connection + SDP exchange
│       ├── muteMicrophone()    # Mikrofon kontrolu
│       ├── disconnect()        # Temizleme
│       └── setVolume()         # Ses seviyesi
│
├── connection/
│   └── server-ws.ts     # WebSocket client (FastAPI)
│       ├── connect()           # Baglanti + auto-reconnect
│       ├── on() / off()        # Event handlers
│       ├── send()              # Mesaj gonderme
│       └── Convenience methods # loadAgenda, requestToken, etc.
│
├── state/
│   └── store.ts         # Reaktif state store
│       ├── update()            # Partial state guncelleme
│       ├── subscribe()         # Listener kaydi
│       └── addTranscript()     # Transcript ekleme (max 50 satir)
│
└── ui/styles/
    └── main.css         # Dark theme dashboard (466 satir)
```

### 4.2. Veri Akisi

```
Server (WebSocket) → ServerConnection → App.setupServerHandlers() → Store.update() → Store.notify() → App.render() → DOM
UI Events → App.setupUIHandlers() → ServerConnection.send() → Server
```

### 4.3. UI Bileşenleri

- **Header**: Logo, moderator durumu (HAZIR/KONUSUYOR/DINLIYOR), baglanti durumu
- **Setup Screen**: Agenda JSON yukle (drag-drop veya dosya sec), "Baglan ve Basla" butonu
- **Dashboard**: Sol kolon (speaker card, timer, kontroller, transcript) + sag kolon (ilerleme, program)
- **Kontroller**: Basla, Duraklat/Devam, Sonraki, Etkilesim, Mikrofon, Override mesaj
- **Paused Overlay**: Duraklatildiginda tam ekran overlay

---

## 5. Mesaj Protokolu

### 5.1. Client → Server

| Mesaj | Payload | Aciklama |
|-------|---------|----------|
| LOAD_AGENDA | `{agenda: {...}}` | Konferans ajandasi yukle |
| REQUEST_TOKEN | - | Ephemeral token iste |
| START_CONFERENCE | - | Konferansi baslat |
| SIDEBAND_CONNECT | `{call_id: "..."}` | Sideband baglantisi kur |
| PAUSE | - | Duraklat |
| RESUME | - | Devam et |
| NEXT_SESSION | - | Sonraki oturuma gec |
| TOGGLE_INTERACT | - | Etkilesim modunu ac/kapa |
| OVERRIDE_MESSAGE | `{message: "..."}` | Ozel mesaj soyle |

### 5.2. Server → Client

| Mesaj | Payload | Aciklama |
|-------|---------|----------|
| AGENDA_LOADED | `{title, total_sessions, sessions[]}` | Agenda yuklendi |
| TOKEN_READY | `{token, endpoint_url, voice}` | Token hazir, WebRTC baslatilabilir |
| STATE_UPDATE | `{state, session_index, speaker_name, is_paused}` | Durum degisti |
| TIMER_TICK | `{elapsed, remaining, total, progress_ratio}` | Her saniye |
| MODERATOR_STATUS | `{status: idle/speaking/listening}` | AI durumu |
| TRANSCRIPT | `{text: "..."}` | AI'nin soyledikleri (metin) |
| ERROR | `{message: "..."}` | Hata mesaji |
| CONFERENCE_ENDED | - | Konferans bitti |

---

## 6. Dosya Istatistikleri

| Kategori | Dosya Sayisi | Satir Sayisi |
|----------|-------------|-------------|
| Python Backend | 17 kaynak | ~2,500 satir |
| TypeScript Frontend | 6 kaynak | ~1,350 satir |
| HTML/CSS | 2 dosya | ~600 satir |
| Testler | 4 dosya | ~520 satir |
| Yapilandirma | 5 dosya | ~180 satir |
| **TOPLAM** | **34 dosya** | **~5,150 satir** |

### Bagimlilklar

**Python (8 paket):** fastapi, uvicorn, websockets, pydantic, pydantic-settings, python-dotenv, structlog, transitions, httpx

**TypeScript (2 paket):** vite, typescript (sifir runtime bagimlilk)

---

## 7. Iyilestirme Noktalari

### 7.1. Kritik (Production Oncesi Yapilmali)

| # | Sorun | Konum | Aciklama | Oneri |
|---|-------|-------|----------|-------|
| K1 | **Tek Oturum Limiti** | `handler.py` | Server tek bir konferans oturumunu destekler. Ikinci client baglanirsa karisiklik olur. | Session ID bazli izolasyon veya conference room sistemi ekle |
| K2 | **WebRTC SDP Guvenlik** | `webrtc.ts` | Ephemeral token browser'a acik gonderiliyor. MITM riski. | Token'i sadece HTTPS uzerinden gonder, CSP header ekle |
| K3 | **Hata Kurtarma** | Tum sistem | WebRTC veya sideband koparsA, konferans durur. Otomatik yeniden baglanti yok. | Reconnection strategy: sideband koparsA yeni session olustur ve devam et |
| K4 | **Agenda Dogrulama** | `agenda_manager.py` | Agenda icerigi sanitize edilmiyor. XSS riski (JSON'dan gelen degerler dogrudan prompt'a giriyor). | Agenda field'larini sanitize et, max uzunluk kontrolleri ekle |
| K5 | **Rate Limiting** | `main.py` | WebSocket endpoint'te rate limiting yok. | FastAPI middleware ile rate limiting ekle |

### 7.2. Yuksek Oncelik

| # | Sorun | Konum | Aciklama | Oneri |
|---|-------|-------|----------|-------|
| Y1 | **messages.py Kullanilmiyor** | `ws/handler.py` | Pydantic mesaj modelleri tanimli ama handler raw dict kullaniyor. Mesaj dogrulama yok. | Gelen mesajlari `ClientMessage.model_validate()` ile dogrula |
| Y2 | **Concurrent State Mutation** | `state_machine.py` | State machine thread-safe degil. Iki async event ayni anda gelirse race condition olusabilir. | asyncio.Lock ile state degisimlerini koruma altina al |
| Y3 | **Timer Drift** | `timer.py` | `asyncio.sleep(1.0)` tam olarak 1 saniye garantisi vermez. Uzun sureli oturumlarda sapma birikir. | `time.monotonic()` bazli drift duzeltme ekle (zaten elapsed icin kullaniliyor ama tick frekansi sapmaz garantisi yok) |
| Y4 | **Transcript Siniri** | `store.ts` | Son 50 satir tutulur ama onceki satirlar kayboluyor. | Tum transcript'i bir log dosyasina veya localStorage'a kaydet |
| Y5 | **SIDEBAND_CONNECT ClientMessage'da Yok** | `messages.py` | ClientMessage type union'inda "SIDEBAND_CONNECT" tanimli degil. | Literal tipine ekle |
| Y6 | **Graceful Shutdown** | `main.py`, `handler.py` | Server kapatildiginda aktif WebSocket ve sideband baglantilari duzgun kapatilmiyor. | Lifespan'da aktif handler'lari takip et ve shutdown'da temizle |

### 7.3. Orta Oncelik

| # | Sorun | Konum | Aciklama | Oneri |
|---|-------|-------|----------|-------|
| O1 | **UI innerHTML Kullanimi** | `app.ts` | `renderAgendaList()` ve `renderTranscript()` innerHTML kullaniyor. `escapeHtml` var ama DOM API daha guvenli. | `document.createElement` + `textContent` kullan |
| O2 | **Environment Validation** | `config.py` | Azure ayarlari bos string default'a sahip. Server bos config ile baslar ve runtime'da patlAR. | Validators ekle: bos ise anlamli hata mesaji goster |
| O3 | **Logging Tutarsizligi** | Tum dosyalar | Bazi dosyalar `logging`, bazilari `structlog` kullaniyor. Logger setup var ama sadece structlog icin. | Tek bir logging stratejisi belirle, her yerde ayni logger kullan |
| O4 | **Test Coverage Eksiklikleri** | `tests/` | Realtime entegrasyonu (session.py, sideband.py, events.py), WebSocket handler, timer icin test yok. | Mock-based integration testler ekle |
| O5 | **Frontend Error Display** | `app.ts` | Hatalar sadece console.error'a gidiyor. Kullaniciya gosterilmiyor. | Toast/notification UI bileseni ekle |
| O6 | **Operator Next vs Thanking** | `state_machine.py` | Operator OPENING'den next derse TRANSITIONING'e gider ama konusmaci yoksa tesekkure gerek yok. Break durumunda da farkli davranmali. | OPENING'den next icin ozel gecis mantigi |
| O7 | **Speaker Card Bos Alanlar** | `app.ts` | `titleOrg` ve `org` elementleri guncellenmiyor (speaker varken de bos). | Speaker bilgilerini tam render et |

### 7.4. Dusuk Oncelik (Nice-to-Have)

| # | Sorun | Konum | Aciklama | Oneri |
|---|-------|-------|----------|-------|
| D1 | **Responsive Tasarim** | `main.css` | Dashboard mobil uyumlu degil. | Media query'ler ekle |
| D2 | **Accessibility** | `index.html` | ARIA etiketleri eksik. | aria-label, role, tabindex ekle |
| D3 | **i18n** | `templates.py`, `index.html` | Dil degistirme destegi yok. Sadece Turkce. | Dil dosyalari ile coklu dil destegi |
| D4 | **Agenda Editoru** | Frontend | Agenda sadece JSON yuklemeyle giriyor. | UI uzerinden agenda duzenleyici |
| D5 | **Kayit/Playback** | Yok | Konferans kaydi ozeligi yok. | Transcript + timeline kaydini dosyaya yaz |
| D6 | **Multi-Conference** | Backend | Ayni anda birden fazla konferans destegi yok. | Room/channel sistemi |
| D7 | **Metrics/Monitoring** | Yok | Performans metrikleri toplanmiyor. | Prometheus/OpenTelemetry entegrasyonu |
| D8 | **Docker** | Yok | Container imaji yok. | Dockerfile + docker-compose.yml |

---

## 8. Calistirma Rehberi

### 8.1. Onkosuular

#### Sistem Gereksinimleri
- **Python**: 3.11+ (kesin gerekli, `from __future__ import annotations` ve `X | Y` union syntax kullaniliyor)
- **Node.js**: 18+ (Vite icin)
- **npm**: 8+ (paket yonetimi)
- **Tarayici**: Chrome/Edge/Firefox (WebRTC destegi gerekli)
- **Mikrofon**: WebRTC icin gerekli

#### Azure OpenAI Gereksinimleri
- Azure OpenAI kaynagi olusturulmus olmali
- **gpt-4o-realtime** modeli deploy edilmis olmali
- API key ve endpoint bilgileri hazir olmali
- API versiyonu: `2025-04-01-preview`

### 8.2. Kurulum Adimlari

#### Adim 1: Repoyu Klonla
```bash
git clone <repo-url>
cd "mAistro Moderator Project"
```

#### Adim 2: Python Ortamini Kur
```bash
# Virtual environment olustur (onerilen)
python -m venv .venv

# Aktive et
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Bagimliiklari yukle
pip install -e ".[dev]"
```

> **Not**: `python` komutu Python 3.11+ degilse, tam yolu kullanin:
> ```bash
> "C:\Users\asus\AppData\Local\Programs\Python\Python311\python.exe" -m pip install -e ".[dev]"
> ```

#### Adim 3: Environment Degiskenlerini Ayarla
```bash
# .env.example'dan kopyala
cp .env.example .env

# .env dosyasini duzenle:
```

```env
# ZORUNLU - Azure OpenAI yapilandirmasi
AZURE_OPENAI_API_KEY=your-actual-api-key
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-realtime
AZURE_OPENAI_API_VERSION=2025-04-01-preview

# OPSIYONEL - Server ayarlari
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173

# OPSIYONEL - Konferans ayarlari
SESSION_RENEWAL_SECONDS=3300
TIME_WARNING_THRESHOLD=0.80
BREAK_ENDING_BUFFER_SECONDS=120
```

#### Adim 4: Frontend Bagimliliklarini Yukle
```bash
cd client
npm install
cd ..
```

#### Adim 5: Testleri Calistir (Dogrulama)
```bash
python -m pytest tests/ -v
```

Beklenen cikti:
```
36 passed in 0.31s
```

### 8.3. Calistirma

#### Gelistirme Modu (2 terminal)

**Terminal 1 - Backend:**
```bash
python -m uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd client
npm run dev
```

Frontend `http://localhost:5173` adresinde baslar ve API isteklerini `http://localhost:8000`'e proxy'ler.

#### Uretim Modu (tek terminal)

```bash
# Frontend'i derle
cd client
npx vite build
cd ..

# Server'i baslat (static dosyalari da serve eder)
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Tarayicida `http://localhost:8000` adresini ac.

### 8.4. Kullanim Adimlari

1. **Tarayiciyi Ac**: `http://localhost:5173` (dev) veya `http://localhost:8000` (prod)

2. **Agenda Yukle**: Setup ekraninda `config/sample-agenda.json` dosyasini surukle-birak veya sec

3. **Baglan**: "Baglan ve Basla" butonuna tikla
   - Server ephemeral token olusturur
   - Browser WebRTC ile Azure OpenAI'ye baglanir
   - Sideband WebSocket kurulur

4. **Konferansi Baslat**: Dashboard'da "Basla" butonuna tikla
   - AI moderator acilis konusmasi yapar
   - State machine otomatik olarak konusmacilari tanistirr
   - Timer calisir, sure uyarilari otomatik verilir

5. **Operator Kontrolleri**:
   - **Duraklat / Devam**: Konferansi duraklat
   - **Sonraki**: Mevcut oturumu atla, bir sonrakine gec
   - **Etkilesim**: Q&A modunu ac/kapat
   - **Mikrofon**: Moderator mikrofonunu ac/kapat
   - **Override Mesaj**: AI'ya ozel bir mesaj soylettir

### 8.5. Agenda JSON Formati

```json
{
  "id": "unique-event-id",
  "title": "Konferans Adi",
  "date": "2026-03-15",
  "venue": "Mekan Adi",
  "language": "tr",
  "moderator_voice": "coral",
  "sessions": [
    {
      "id": "session-1",
      "type": "opening",
      "title": "Acilis",
      "duration_minutes": 5,
      "notes": "Ozel talimatlar (opsiyonel)"
    },
    {
      "id": "session-2",
      "type": "keynote",
      "title": "Sunum Basligi",
      "duration_minutes": 30,
      "speaker": {
        "name": "Ad Soyad",
        "title": "Unvan",
        "organization": "Kurum",
        "talk_title": "Konu Basligi",
        "bio": "Kisa biyografi (opsiyonel)"
      }
    },
    {
      "id": "session-3",
      "type": "panel",
      "title": "Panel Basligi",
      "duration_minutes": 30,
      "panelists": [
        {"name": "Panelist 1", "title": "...", "organization": "...", "talk_title": "..."},
        {"name": "Panelist 2", "title": "...", "organization": "...", "talk_title": "..."}
      ]
    },
    {
      "id": "break",
      "type": "break",
      "title": "Kahve Molasi",
      "duration_minutes": 15
    },
    {
      "id": "closing",
      "type": "closing",
      "title": "Kapanis",
      "duration_minutes": 5
    }
  ]
}
```

**Session Tipleri:** `opening`, `keynote`, `talk`, `panel`, `qa`, `break`, `closing`

**Ses Secenekleri (moderator_voice):** `coral`, `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

### 8.6. Troubleshooting

| Sorun | Olasi Neden | Cozum |
|-------|------------|-------|
| "Mikrofon erisimi reddedildi" | Tarayici mikrofon izni | Tarayici ayarlarindan mikrofon iznini ver |
| WebRTC baglanamadi | HTTPS gerekli (bazi tarayicilarda) | Production'da HTTPS kullan veya localhost ile calis |
| "Token olusturma hatasi" | Azure API key veya endpoint yanlis | `.env` dosyasindaki Azure ayarlarini kontrol et |
| Sideband baglanti hatasi | `call_id` alinamiyor | WebRTC SDP exchange'in basarili oldugundan emin ol |
| Timer calismiyor | Konferans baslatilmamis | "Basla" butonuna tikladiginizdan emin olun |
| CORS hatasi | `ALLOWED_ORIGINS` yanlis | `.env`'de dogru origin'leri ekleyin |
| "setuptools flat-layout" hatasi | pyproject.toml eksik | `[tool.setuptools.packages.find]` bolumund `include = ["server*", "tests*"]` oldugunu dogrulayin |

### 8.7. Log Izleme

```bash
# Detayli log
LOG_LEVEL=DEBUG python -m uvicorn server.main:app --reload

# Onemli olaylar:
# "Session created: id=..."          → Token basariyla olusturuldu
# "Sideband WebSocket connected"     → Sideband baglandi
# "State changed to: opening"        → Konferans basladi
# "Function call: advance_to_next..."→ AI sonraki oturuma geciyor
# "Timer error"                      → Timer'da hata (loglari incele)
```
