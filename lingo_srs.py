#!/usr/bin/env python3
"""
================================================================================
 LINGO-SRS : Sistem Belajar Bahasa Inggris & Mandarin Otomatis
 (Retrieval Practice + Spaced Repetition + Interleaving + Metakognisi)
================================================================================

Dibangun berdasarkan 4 prinsip "Make It Stick":
  1. RETRIEVAL PRACTICE  -> user selalu diminta MENJAWAB (bukan membaca),
                            lewat kuis interaktif (fill-in, terjemahan, nada).
  2. SPACED REPETITION   -> jadwal ulang otomatis H+1 / H+3 / H+7 / H+14,
                            ditentukan oleh skor metakognisi (1-4) tiap kartu.
  3. INTERLEAVING        -> satu sesi mencampur: Bahasa Inggris & Mandarin,
                            grammar & vocab, materi baru & materi due-review.
  4. METACOGNITION       -> di akhir tiap kartu & tiap sesi, user menilai
                            seberapa yakin/​sulit jawabannya (1=lupa total,
                            4=sangat yakin) -> dipakai algoritma penjadwalan.

Storage : SQLite lokal (lingo_srs.db) secara default -> otomatis dibuat saat
          pertama run. BISA diganti ke Turso (cloud, permanen, gratis) tinggal
          dengan mengisi 2 environment variable TURSO_DATABASE_URL dan
          TURSO_AUTH_TOKEN -- tidak ada kode lain yang perlu diubah.
Jalankan: python lingo_srs.py

Struktur file (satu file, siap jalan, tanpa dependency eksternal):
  - DB SCHEMA & KONEKSI
  - DATASET BAWAAN 15 HARI PERTAMA (Mandarin: Pinyin & Nada, Inggris: Struktur Dasar)
  - PETA KURIKULUM (Pemula -> Menengah -> Mahir) sebagai metadata level/topic
  - SRS ENGINE (penjadwalan H+1/H+3/H+7/H+14)
  - INTERLEAVING ENGINE (pemilih & pengacak sesi harian)
  - RETRIEVAL CLI (antarmuka kuis interaktif)
  - MAIN LOOP
================================================================================
"""

import sqlite3
import random
import datetime
import os
import sys
import textwrap

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lingo_srs.db")

# ==============================================================================
# 1. PETA KURIKULUM BERTINGKAT (dipakai sebagai metadata "level" tiap materi)
# ==============================================================================
# Level dipakai untuk mengunci progres: user tidak akan diberi materi ADVANCED
# sebelum menyelesaikan cukup banyak materi BASIC/INTERMEDIATE (lihat
# get_unlocked_levels()). Struktur ini sengaja disimpan sebagai data python
# (bukan hard-code di UI) supaya gampang diperluas / diimpor dari CSV nanti.

CURRICULUM_MAP = {
    "en": {
        "basic": [
            "greetings", "pronouns", "to-be", "simple-present",
            "numbers-time", "basic-questions", "daily-vocab",
        ],
        "intermediate": [
            "past-tense", "future-tense", "modals", "comparatives",
            "phrasal-verbs", "conditionals-1", "business-email-vocab",
        ],
        "advanced": [
            "conditionals-mixed", "passive-voice-advanced", "idioms",
            "academic-writing", "business-negotiation", "toefl-ielts-strategy",
        ],
    },
    "zh": {
        "basic": [
            "pinyin-tones", "greetings", "numbers", "pronouns",
            "basic-sentence-shi", "family-words", "daily-vocab-hsk1",
        ],
        "intermediate": [
            "measure-words", "le-particle", "comparison-bi", "hsk2-hsk3-vocab",
            "directions-time", "ba-sentence", "business-basics",
        ],
        "advanced": [
            "hsk4-hsk5-vocab", "chengyu-idioms", "formal-writing",
            "business-negotiation-zh", "news-reading", "hsk6-prep",
        ],
    },
}

# ==============================================================================
# 2. MODUL INTEGRASI COURSERA (rekomendasi jalur, dipakai untuk ditampilkan
#    di menu "Roadmap Eksternal" — TIDAK melakukan enrol otomatis, hanya
#    referensi + link agar user bisa ajukan Financial Aid).
# ==============================================================================

COURSERA_TRACKS = {
    "en": [
        {"level": "Pemula", "name": "Learn English: Beginning Grammar",
         "provider": "University of California, Irvine",
         "url": "https://www.coursera.org/learn/beginning-grammar"},
        {"level": "Pemula-Menengah", "name": "Improve Your English Communication Skills (Specialization)",
         "provider": "Georgia Institute of Technology",
         "url": "https://www.coursera.org/specializations/improve-english"},
        {"level": "Menengah", "name": "Business English Specialization",
         "provider": "University of Washington",
         "url": "https://www.coursera.org/specializations/business-english"},
        {"level": "Menengah-Mahir", "name": "TOEFL Preparation Specialization",
         "provider": "University of California, Irvine",
         "url": "https://www.coursera.org/specializations/toefl-preparation"},
        {"level": "Mahir", "name": "Academic English: Writing Specialization",
         "provider": "UC Berkeley",
         "url": "https://www.coursera.org/specializations/academic-english"},
        {"level": "Pemula-Menengah (Speaking)", "name": "Speak English Professionally: In Person, Online & On the Phone",
         "provider": "Georgia Institute of Technology",
         "url": "https://www.coursera.org/learn/speak-english-professionally"},
        {"level": "Menengah-Mahir (Speaking)", "name": "The Pronunciation of American English (Specialization)",
         "provider": "University of California, Irvine",
         "url": "https://www.coursera.org/specializations/american-english-pronunciation"},
    ],
    "zh": [
        {"level": "Pemula", "name": "Chinese for Beginners / Chinese Characters for Beginner 汉字",
         "provider": "Peking University",
         "url": "https://www.coursera.org/learn/hanzi"},
        {"level": "Pemula-Menengah", "name": "Learn Chinese: HSK Test Preparation (Spesialisasi, HSK 1-3)",
         "provider": "Peking University",
         "url": "https://www.coursera.org/specializations/hsk-learn-chinese"},
        {"level": "Menengah", "name": "Chinese for HSK 3 / HSK 4",
         "provider": "Peking University",
         "url": "https://www.coursera.org/learn/hsk-3"},
        {"level": "Menengah-Mahir", "name": "Business Chinese Specialization",
         "provider": "Peking University",
         "url": "https://www.coursera.org/specializations/business-chinese"},
        {"level": "Mahir", "name": "Chinese for HSK 5-6 / Advanced Reading",
         "provider": "Peking University",
         "url": "https://www.coursera.org/learn/hsk-3"},
        {"level": "Pemula (Speaking)", "name": "Chinese for Beginners (ABC Chinese) -- fokus speaking, ada latihan VR",
         "provider": "Peking University",
         "url": "https://www.coursera.org/learn/learn-chinese"},
        {"level": "Pemula-Menengah (Speaking)", "name": "More Chinese for Beginners (lanjutan speaking)",
         "provider": "Peking University",
         "url": "https://www.coursera.org/learn/more-chinese-for-beginners"},
    ],
}


def print_coursera_tracks():
    print("\n=== REKOMENDASI JALUR COURSERA (ajukan Financial Aid hari ini) ===")
    for lang, label in (("en", "BAHASA INGGRIS"), ("zh", "BAHASA MANDARIN")):
        print(f"\n-- {label} --")
        for c in COURSERA_TRACKS[lang]:
            print(f"  [{c['level']:<15}] {c['name']}")
            print(f"                    Penyedia : {c['provider']}")
            print(f"                    Link     : {c['url']}")
    print("\nCatatan: cek tombol 'Financial Aid' di halaman course/specialization "
          "masing-masing (biasanya di bawah tombol 'Enroll'). Ajukan semua "
          "level sekaligus supaya tidak perlu menunggu bertahap.\n")


# ==============================================================================
# 3. DATASET BAWAAN 15 HARI PERTAMA
# ==============================================================================
# Format tiap item:
#   (lang, level, topic, item_type, day, prompt, answer, hint)
# item_type: "vocab" | "grammar" | "tone" | "translate"
#
# Materi didesain sebagai RETRIEVAL (user harus mengetik jawaban / menebak),
# bukan flashcard pasif satu arah.

SEED_DATA_15_DAYS = []

def _add(lang, level, topic, item_type, day, prompt, answer, hint=""):
    SEED_DATA_15_DAYS.append((lang, level, topic, item_type, day, prompt, answer, hint))

# ---- HARI 1-3 : Mandarin Pinyin & Nada dasar + Inggris to-be/pronouns ----
_add("zh", "basic", "pinyin-tones", "tone", 1, "Nada berapa pada 'mā' (妈, ibu)?", "1", "Nada datar tinggi")
_add("zh", "basic", "pinyin-tones", "tone", 1, "Nada berapa pada 'má' (麻)?", "2", "Naik")
_add("zh", "basic", "pinyin-tones", "tone", 1, "Nada berapa pada 'mǎ' (马, kuda)?", "3", "Turun-naik")
_add("zh", "basic", "pinyin-tones", "tone", 1, "Nada berapa pada 'mà' (骂, memarahi)?", "4", "Turun tajam")
_add("zh", "basic", "greetings", "translate", 1, "Terjemahkan ke Mandarin: 'Halo'", "你好 (nǐ hǎo)")
_add("zh", "basic", "greetings", "translate", 1, "Terjemahkan ke Mandarin: 'Terima kasih'", "谢谢 (xièxie)")
_add("en", "basic", "to-be", "grammar", 1, "Lengkapi: 'I ___ a student.'", "am")
_add("en", "basic", "to-be", "grammar", 1, "Lengkapi: 'She ___ happy.'", "is")
_add("en", "basic", "pronouns", "vocab", 1, "Kata ganti untuk 'mereka' dalam Bahasa Inggris?", "they")

_add("zh", "basic", "numbers", "vocab", 2, "Angka 1 dalam Mandarin (pinyin)?", "yī")
_add("zh", "basic", "numbers", "vocab", 2, "Angka 5 dalam Mandarin (pinyin)?", "wǔ")
_add("zh", "basic", "numbers", "vocab", 2, "Angka 10 dalam Mandarin (pinyin)?", "shí")
_add("zh", "basic", "pinyin-tones", "tone", 2, "Nada pada 'bà' (爸, ayah)?", "4")
_add("en", "basic", "to-be", "grammar", 2, "Lengkapi: 'They ___ students.'", "are")
_add("en", "basic", "numbers-time", "vocab", 2, "Tulis dalam angka: 'seventeen'", "17")
_add("en", "basic", "pronouns", "vocab", 2, "Kata ganti untuk 'kita' dalam Bahasa Inggris?", "we")

_add("zh", "basic", "pronouns", "vocab", 3, "'Saya' dalam Mandarin (pinyin)?", "wǒ")
_add("zh", "basic", "pronouns", "vocab", 3, "'Kamu' dalam Mandarin (pinyin)?", "nǐ")
_add("zh", "basic", "pronouns", "vocab", 3, "'Dia (laki-laki)' dalam Mandarin (pinyin)?", "tā")
_add("en", "basic", "simple-present", "grammar", 3, "Lengkapi: 'He ___ (go) to school every day.'", "goes")
_add("en", "basic", "simple-present", "grammar", 3, "Lengkapi: 'They ___ (like) coffee.'", "like")
_add("en", "basic", "greetings", "translate", 3, "Terjemahkan ke Inggris: 'Selamat pagi'", "Good morning")

# ---- HARI 4-7 : struktur dasar S-V, kalimat 是, tata bahasa dasar ----
_add("zh", "basic", "basic-sentence-shi", "grammar", 4, "Lengkapi: 'Wǒ ___ xuéshēng.' (Saya adalah murid)", "shì")
_add("zh", "basic", "basic-sentence-shi", "translate", 4, "Terjemahkan: 'Ini adalah buku.' -> Mandarin (pinyin)", "Zhè shì shū.")
_add("zh", "basic", "family-words", "vocab", 4, "'Ibu' dalam Mandarin (pinyin)?", "māma")
_add("en", "basic", "simple-present", "grammar", 4, "Lengkapi (negatif): 'She ___ not like tea.'", "does")
_add("en", "basic", "daily-vocab", "vocab", 4, "Bahasa Inggris dari 'meja'?", "table")

_add("zh", "basic", "family-words", "vocab", 5, "'Ayah' dalam Mandarin (pinyin)?", "bàba")
_add("zh", "basic", "numbers", "vocab", 5, "Angka 100 dalam Mandarin (pinyin)?", "yìbǎi")
_add("en", "basic", "basic-questions", "grammar", 5, "Susun jadi kalimat tanya: 'you / are / student / a ?'", "Are you a student?")
_add("en", "basic", "daily-vocab", "vocab", 5, "Bahasa Inggris dari 'kursi'?", "chair")

_add("zh", "basic", "pinyin-tones", "tone", 6, "Nada pada 'hǎo' (好, baik)?", "3")
_add("zh", "basic", "daily-vocab-hsk1", "vocab", 6, "'Air' dalam Mandarin (pinyin)?", "shuǐ")
_add("en", "basic", "basic-questions", "grammar", 6, "Susun jadi kalimat tanya: 'what / your / name / is ?'", "What is your name?")
_add("en", "basic", "daily-vocab", "vocab", 6, "Bahasa Inggris dari 'buku'?", "book")

_add("zh", "basic", "daily-vocab-hsk1", "vocab", 7, "'Makan' dalam Mandarin (pinyin)?", "chī")
_add("zh", "basic", "greetings", "translate", 7, "Terjemahkan ke Mandarin: 'Sampai jumpa'", "再见 (zàijiàn)")
_add("en", "basic", "simple-present", "grammar", 7, "Lengkapi: 'We ___ (study) English every day.'", "study")
_add("en", "basic", "numbers-time", "vocab", 7, "Jam berapa 'half past three'? (format HH:MM)", "03:30")

# ---- HARI 8-11 : perluasan kosakata + partikel 了 + past tense ----
_add("zh", "basic", "le-particle", "grammar", 8, "Lengkapi: 'Wǒ chī ___ fàn.' (Saya sudah makan)", "le")
_add("zh", "basic", "daily-vocab-hsk1", "vocab", 8, "'Minum' dalam Mandarin (pinyin)?", "hē")
_add("en", "intermediate", "past-tense", "grammar", 8, "Bentuk lampau dari 'go'?", "went")
_add("en", "intermediate", "past-tense", "grammar", 8, "Bentuk lampau dari 'eat'?", "ate")

_add("zh", "basic", "measure-words", "grammar", 9, "Kata bantu bilangan untuk buku: 'yī ___ shū'", "běn")
_add("zh", "basic", "daily-vocab-hsk1", "vocab", 9, "'Sekolah' dalam Mandarin (pinyin)?", "xuéxiào")
_add("en", "intermediate", "past-tense", "grammar", 9, "Lengkapi: 'Yesterday I ___ (watch) a movie.'", "watched")
_add("en", "intermediate", "comparatives", "grammar", 9, "Bentuk komparatif dari 'big'?", "bigger")

_add("zh", "basic", "directions-time", "vocab", 10, "'Hari ini' dalam Mandarin (pinyin)?", "jīntiān")
_add("zh", "basic", "directions-time", "vocab", 10, "'Besok' dalam Mandarin (pinyin)?", "míngtiān")
_add("en", "intermediate", "comparatives", "grammar", 10, "Bentuk superlatif dari 'good'?", "best")
_add("en", "intermediate", "modals", "grammar", 10, "Lengkapi (kemampuan): 'She ___ speak French.'", "can")

_add("zh", "basic", "directions-time", "vocab", 11, "'Kemarin' dalam Mandarin (pinyin)?", "zuótiān")
_add("zh", "basic", "comparison-bi", "grammar", 11, "Lengkapi: 'Tā ___ wǒ gāo.' (Dia lebih tinggi dari saya)", "bǐ")
_add("en", "intermediate", "modals", "grammar", 11, "Lengkapi (keharusan): 'You ___ wear a seatbelt.'", "must")
_add("en", "intermediate", "future-tense", "grammar", 11, "Lengkapi: 'Tomorrow I ___ (visit) my grandma.'", "will visit")

# ---- HARI 12-15 : gabungan review-berat + sedikit materi baru lanjutan ----
_add("zh", "basic", "daily-vocab-hsk1", "vocab", 12, "'Terima kasih kembali / sama-sama' dalam Mandarin (pinyin)?", "bú kèqi")
_add("zh", "intermediate", "hsk2-hsk3-vocab", "vocab", 12, "'Pekerjaan / kerja' dalam Mandarin (pinyin)?", "gōngzuò")
_add("en", "intermediate", "future-tense", "grammar", 12, "Lengkapi: 'They ___ (arrive) next week.'", "will arrive")
_add("en", "intermediate", "phrasal-verbs", "vocab", 12, "Arti 'give up' dalam Bahasa Indonesia?", "menyerah")

_add("zh", "intermediate", "hsk2-hsk3-vocab", "vocab", 13, "'Restoran' dalam Mandarin (pinyin)?", "cānguǎn")
_add("zh", "basic", "numbers", "vocab", 13, "Angka 88 dalam Mandarin (pinyin)?", "bāshíbā")
_add("en", "intermediate", "phrasal-verbs", "vocab", 13, "Arti 'look forward to' dalam Bahasa Indonesia?", "menantikan")
_add("en", "intermediate", "conditionals-1", "grammar", 13, "Lengkapi (Conditional type 1): 'If it rains, I ___ (stay) home.'", "will stay")

_add("zh", "basic", "ba-sentence", "grammar", 14, "Lengkapi (kalimat 把): 'Wǒ ___ shū fàng zài zhuōzi shàng.' (Saya meletakkan buku di meja)", "bǎ")
_add("zh", "intermediate", "hsk2-hsk3-vocab", "vocab", 14, "'Bepergian / traveling' dalam Mandarin (pinyin)?", "lǚyóu")
_add("en", "intermediate", "conditionals-1", "grammar", 14, "Lengkapi: 'If I have time, I ___ (call) you.'", "will call")
_add("en", "basic", "daily-vocab", "vocab", 14, "Bahasa Inggris dari 'jendela'?", "window")

_add("zh", "intermediate", "business-basics", "vocab", 15, "'Rapat / meeting' dalam Mandarin (pinyin)?", "huìyì")
_add("zh", "basic", "pinyin-tones", "tone", 15, "Nada pada 'xiè' (谢, dalam xièxie)?", "4")
_add("en", "intermediate", "business-email-vocab", "vocab", 15, "Ungkapan formal untuk membuka email bisnis: 'Dear ___,' lalu kalimat pembuka umum: 'I am writing to ___ .' (isi kata kerja)", "inquire")
_add("en", "basic", "daily-vocab", "vocab", 15, "Bahasa Inggris dari 'lemari'?", "cupboard")

# ==============================================================================
# 4. DATABASE LAYER
# ==============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lang        TEXT NOT NULL,          -- 'en' | 'zh'
    level       TEXT NOT NULL,          -- 'basic' | 'intermediate' | 'advanced'
    topic       TEXT NOT NULL,
    item_type   TEXT NOT NULL,          -- 'vocab' | 'grammar' | 'tone' | 'translate'
    day_seed    INTEGER,                -- hari asal (untuk dataset 15 hari), NULL jika materi lanjutan
    prompt      TEXT NOT NULL,
    answer      TEXT NOT NULL,
    hint        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS progress (
    material_id     INTEGER PRIMARY KEY REFERENCES materials(id),
    times_seen      INTEGER DEFAULT 0,
    times_correct   INTEGER DEFAULT 0,
    times_wrong     INTEGER DEFAULT 0,
    last_metacog    INTEGER,            -- skor 1-4 terakhir
    interval_stage  TEXT DEFAULT 'new', -- 'new' | 'H1' | 'H3' | 'H7' | 'H14' | 'mastered'
    next_review     TEXT,               -- ISO date YYYY-MM-DD, NULL jika belum pernah dipelajari
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    items_reviewed  INTEGER,
    avg_metacog     REAL,
    session_rating  INTEGER             -- kesulitan sesi keseluruhan (1-4), input user
);

CREATE TABLE IF NOT EXISTS session_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(id),
    material_id     INTEGER REFERENCES materials(id),
    correct         INTEGER,
    metacog_score   INTEGER
);
"""

# Skor metakognisi -> tahap interval berikutnya.
# 1 = "Saya lupa total / salah"      -> ulang besok  (H+1)
# 2 = "Ingat tapi susah / ragu"      -> ulang 3 hari lagi (H+3)
# 3 = "Ingat, cukup yakin"           -> ulang 7 hari lagi (H+7)
# 4 = "Sangat yakin / mudah"         -> ulang 14 hari lagi (H+14), lalu 'mastered'
METACOG_TO_STAGE = {1: "H1", 2: "H3", 3: "H7", 4: "H14"}
STAGE_TO_DAYS = {"H1": 1, "H3": 3, "H7": 7, "H14": 14}


class _RowDict(dict):
    """Wrapper supaya hasil query bisa diakses row['kolom'] ATAUPUN row[0],
    persis seperti sqlite3.Row -- tapi dibuat manual dari nama kolom
    supaya SAMA PERSIS perilakunya baik saat backend-nya sqlite3 lokal
    maupun saat backend-nya libsql_client/Turso (remote)."""
    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Database:
    """
    Dua mode koneksi, dipilih otomatis:

    1. MODE LOKAL (default) -> file SQLite biasa di komputer/server.
       Dipakai kalau env var TURSO_DATABASE_URL tidak diisi.

    2. MODE TURSO (cloud) -> data tersimpan permanen di Turso, aman dari
       reset/redeploy. Aktif otomatis kalau 2 env var ini diisi:
         - TURSO_DATABASE_URL   (contoh: libsql://nama-db-anda.turso.io)
         - TURSO_AUTH_TOKEN

       Dipakai lewat paket `libsql-client` -- ini paket MURNI PYTHON
       (cuma ngobrol ke Turso lewat HTTP/WebSocket), BUKAN paket
       `libsql-experimental` yang berbasis Rust dan sering gagal
       ter-install di server cloud (perlu compiler Rust, sering tidak
       ada wheel siap-pakai untuk arsitektur server seperti ARM).

    Semua query SQL di file ini (q/q1/exec dan seluruh SRS/Interleaver)
    TIDAK PERLU DIUBAH sama sekali -- keduanya memakai dialek SQLite yang
    sama persis, hanya cara connect-nya yang beda.
    """

    def __init__(self, path=DB_PATH, turso_url=None, turso_token=None):
        turso_url = turso_url or os.environ.get("TURSO_DATABASE_URL")
        turso_token = turso_token or os.environ.get("TURSO_AUTH_TOKEN")

        self.using_turso = bool(turso_url and turso_token)

        if self.using_turso:
            import libsql_client
            # PENTING: paksa skema URL ke https:// (HTTP biasa), bukan
            # libsql://. Turso memberi URL dengan skema 'libsql://' yang
            # oleh libsql-client diterjemahkan jadi koneksi WebSocket --
            # dan koneksi WebSocket ini sering GAGAL handshake di server
            # cloud/serverless seperti Streamlit Cloud (bug yang sudah
            # dilaporkan resmi di GitHub tursodatabase/libsql-client-py
            # issue #34). Skema https:// memakai HTTP request biasa per
            # query, jauh lebih andal di lingkungan seperti ini, dan
            # didukung resmi oleh Turso untuk kebutuhan yang sama.
            https_url = turso_url.replace("libsql://", "https://", 1)
            self.client = libsql_client.create_client_sync(url=https_url, auth_token=turso_token)
            self.conn = None
        else:
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.client = None

        for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
            self._raw_execute(stmt)
        self._migrate_add_streak_column()
        self._seed_if_empty()

    def _migrate_add_streak_column(self):
        """Migrasi kecil: kolom 'streak' (jumlah jawaban benar berturut-turut)
        ditambahkan belakangan untuk kebutuhan UI Lovable (tipe Progress-nya
        mengharapkan field ini). Dibungkus try/except karena SQLite tidak
        punya 'ADD COLUMN IF NOT EXISTS' -- aman dijalankan berkali-kali,
        akan gagal diam-diam kalau kolomnya sudah ada."""
        try:
            self._raw_execute("ALTER TABLE progress ADD COLUMN streak INTEGER DEFAULT 0")
        except Exception:
            pass

    # -- lapisan paling bawah: satu-satunya tempat yang tahu bedanya
    #    sqlite3 lokal vs libsql_client (Turso) --
    def _raw_execute(self, sql, params=()):
        if self.using_turso:
            rs = self.client.execute(sql, list(params))
            cols = list(rs.columns) if rs.columns else []
            rows = [tuple(r) for r in rs.rows]
            return cols, rows
        else:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            self.conn.commit()
            return cols, rows

    def _seed_if_empty(self):
        cols, rows = self._raw_execute("SELECT COUNT(*) AS c FROM materials")
        if rows and rows[0][0] > 0:
            return
        for (lang, level, topic, item_type, day, prompt, answer, hint) in SEED_DATA_15_DAYS:
            self._raw_execute(
                "INSERT INTO materials (lang, level, topic, item_type, day_seed, prompt, answer, hint) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (lang, level, topic, item_type, day, prompt, answer, hint),
            )
        print(f"[Setup] {len(SEED_DATA_15_DAYS)} materi bawaan (15 hari pertama) berhasil dimuat ke database.")

    # -- helper generik (dipakai di seluruh file, tidak berubah dari luar) --
    def q(self, sql, params=()):
        cols, rows = self._raw_execute(sql, params)
        return [_RowDict(cols, r) for r in rows]

    def q1(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def exec(self, sql, params=()):
        self._raw_execute(sql, params)

    def insert_and_get_id(self, sql, params=()):
        """Insert lalu kembalikan id baris baru lewat klausa SQL 'RETURNING id'
        -- dipilih karena berperilaku identik di mode lokal maupun mode Turso,
        beda dengan last_insert_rowid() yang tidak selalu konsisten lintas
        koneksi/backend. Catatan: sql yang dikirim ke method ini HARUS berupa
        INSERT ke tabel yang punya kolom 'id', dan TANPA klausa RETURNING
        sendiri -- klausa itu ditambahkan otomatis di sini."""
        cols, rows = self._raw_execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        return rows[0][0]


# ==============================================================================
# 5. SPACED REPETITION ENGINE
# ==============================================================================

class SRSEngine:
    """
    Mengelola progress tiap materi dan menghitung next_review berdasarkan
    skor metakognisi (1-4) yang diberikan user setelah menjawab.
    """

    def __init__(self, db: Database):
        self.db = db

    def ensure_progress_row(self, material_id):
        row = self.db.q1("SELECT * FROM progress WHERE material_id=?", (material_id,))
        if row is None:
            self.db.exec(
                "INSERT INTO progress (material_id, interval_stage) VALUES (?, 'new')",
                (material_id,),
            )

    def record_answer(self, material_id, correct: bool, metacog_score: int, today=None):
        """
        Update progress sebuah materi setelah dijawab.
        metacog_score: 1 (lupa total) .. 4 (sangat yakin/mudah)
        Aturan H+1/H+3/H+7/H+14:
          - Jika jawaban SALAH -> otomatis dianggap skor 1 -> reset ke H+1,
            berapapun metacog_score yang diinput (desirable difficulty:
            kesalahan harus segera direview lagi).
          - Jika jawaban BENAR -> pakai metacog_score untuk menentukan
            tahap berikut (1->H1, 2->H3, 3->H7, 4->H14).
          - Jika sudah di tahap H14 dan kembali skor 4 -> ditandai 'mastered'
            (tetap direview sesekali lewat mekanisme low-priority, lihat
            Interleaver.pick_due()).
        """
        today = today or datetime.date.today()
        self.ensure_progress_row(material_id)
        row = self.db.q1("SELECT * FROM progress WHERE material_id=?", (material_id,))

        effective_score = metacog_score if correct else 1
        stage = row["interval_stage"]

        if not correct:
            new_stage = "H1"
        elif stage == "H14" and effective_score == 4:
            new_stage = "mastered"
        else:
            new_stage = METACOG_TO_STAGE[effective_score]

        if new_stage == "mastered":
            next_review = today + datetime.timedelta(days=30)  # cek ringan tiap bulan
        else:
            next_review = today + datetime.timedelta(days=STAGE_TO_DAYS[new_stage])

        self.db.exec(
            """UPDATE progress SET
                 times_seen = times_seen + 1,
                 times_correct = times_correct + ?,
                 times_wrong = times_wrong + ?,
                 last_metacog = ?,
                 interval_stage = ?,
                 next_review = ?,
                 last_seen = ?,
                 streak = CASE WHEN ? THEN COALESCE(streak, 0) + 1 ELSE 0 END
               WHERE material_id = ?""",
            (
                1 if correct else 0,
                0 if correct else 1,
                metacog_score,
                new_stage,
                next_review.isoformat(),
                today.isoformat(),
                1 if correct else 0,
                material_id,
            ),
        )
        return new_stage, next_review


# ==============================================================================
# 6. INTERLEAVING ENGINE
# ==============================================================================

class Interleaver:
    """
    Menyusun sesi harian dengan mencampur:
      - materi yang jatuh tempo (due) dari SRS queue,
      - materi baru (belum pernah dipelajari) sesuai level yang terbuka,
    lalu MENGACAK urutan bahasa/topik/tipe supaya tidak ada 2 soal berturut2
    dari bahasa & topik yang sama (interleaving sejati, bukan cuma random).
    """

    def __init__(self, db: Database):
        self.db = db

    def get_unlocked_levels(self, lang):
        """Level 'intermediate' terbuka setelah >=70% materi basic dikuasai
        (interval_stage bukan 'new'/'H1'), 'advanced' setelah >=70% intermediate."""
        def mastery_ratio(level):
            total = self.db.q1(
                "SELECT COUNT(*) c FROM materials WHERE lang=? AND level=?", (lang, level)
            )["c"]
            if total == 0:
                return 0
            done = self.db.q1(
                """SELECT COUNT(*) c FROM materials m JOIN progress p ON p.material_id=m.id
                   WHERE m.lang=? AND m.level=? AND p.interval_stage IN ('H7','H14','mastered')""",
                (lang, level),
            )["c"]
            return done / total

        levels = ["basic"]
        if mastery_ratio("basic") >= 0.7:
            levels.append("intermediate")
        if mastery_ratio("intermediate") >= 0.7:
            levels.append("advanced")
        return levels

    def pick_due(self, lang, today, limit=20):
        return self.db.q(
            """SELECT m.* FROM materials m JOIN progress p ON p.material_id = m.id
               WHERE m.lang=? AND p.next_review IS NOT NULL AND p.next_review <= ?
               ORDER BY p.next_review ASC LIMIT ?""",
            (lang, today.isoformat(), limit),
        )

    def pick_new(self, lang, levels, today, limit=10):
        placeholders = ",".join("?" * len(levels))
        return self.db.q(
            f"""SELECT m.* FROM materials m LEFT JOIN progress p ON p.material_id = m.id
                WHERE m.lang=? AND m.level IN ({placeholders})
                  AND (p.material_id IS NULL OR p.interval_stage='new')
                ORDER BY m.day_seed ASC, RANDOM() LIMIT ?""",
            (lang, *levels, limit),
        )

    def build_session(self, target_size=12, today=None):
        today = today or datetime.date.today()
        pool = []
        for lang in ("en", "zh"):
            levels = self.get_unlocked_levels(lang)
            due = self.pick_due(lang, today, limit=target_size)
            pool.extend(due)
            remaining_slots = max(0, (target_size // 2) - len(due))
            if remaining_slots > 0:
                pool.extend(self.pick_new(lang, levels, today, limit=remaining_slots))

        if not pool:
            # tidak ada due/baru -> ambil review acak ringan supaya tetap ada retrieval practice
            pool = self.db.q(
                "SELECT * FROM materials ORDER BY RANDOM() LIMIT ?", (target_size,)
            )

        pool = pool[:max(target_size, len(pool))]
        return self._interleave(pool)

    @staticmethod
    def _interleave(items):
        """Susun ulang urutan supaya item berurutan TIDAK sama lang & topic-nya
        (algoritma round-robin per-bucket), true interleaving bukan cuma shuffle."""
        buckets = {}
        for it in items:
            key = (it["lang"], it["item_type"])
            buckets.setdefault(key, []).append(it)
        for k in buckets:
            random.shuffle(buckets[k])

        keys = list(buckets.keys())
        random.shuffle(keys)
        result = []
        while any(buckets[k] for k in keys):
            for k in keys:
                if buckets[k]:
                    # hindari 2 item berturut-turut dgn key identik jika ada alternatif
                    if result and (result[-1]["lang"], result[-1]["item_type"]) == k and \
                       any(buckets[k2] for k2 in keys if k2 != k):
                        continue
                    result.append(buckets[k].pop())
        return result


# ==============================================================================
# 7. RETRIEVAL PRACTICE CLI (Antarmuka Interaktif)
# ==============================================================================

LANG_LABEL = {"en": "🇬🇧 ENGLISH", "zh": "🇨🇳 MANDARIN"}
TYPE_LABEL = {
    "vocab": "Kosakata (Vocab)",
    "grammar": "Tata Bahasa (Grammar)",
    "tone": "Nada (Tone)",
    "translate": "Terjemahan (Translate)",
}


def clear_line():
    print("-" * 60)


def run_session(db: Database, srs: SRSEngine, interleaver: Interleaver):
    today = datetime.date.today()
    session_items = interleaver.build_session(target_size=12, today=today)

    if not session_items:
        print("Tidak ada materi tersedia. Pastikan database sudah ter-seed.")
        return

    print("\n" + "=" * 60)
    print(f" SESI RETRIEVAL PRACTICE - {today.isoformat()}")
    print(f" Jumlah soal (interleaved EN/ZH, grammar/vocab): {len(session_items)}")
    print("=" * 60)

    session_id = db.insert_and_get_id(
        "INSERT INTO sessions (date, items_reviewed, avg_metacog) VALUES (?,0,0)",
        (today.isoformat(),),
    )

    metacog_scores = []

    for idx, item in enumerate(session_items, start=1):
        clear_line()
        print(f"[{idx}/{len(session_items)}] {LANG_LABEL[item['lang']]} "
              f"| {item['level'].upper()} | {TYPE_LABEL[item['item_type']]} | topik: {item['topic']}")
        print(f"\nSOAL: {item['prompt']}")
        if item["hint"]:
            print(f"(hint: {item['hint']})")

        user_answer = input("Jawaban Anda >> ").strip()

        correct = _check_answer(user_answer, item["answer"])
        print("✅ BENAR!" if correct else f"❌ Kurang tepat. Jawaban benar: {item['answer']}")

        metacog_score = _ask_metacognition()
        metacog_scores.append(metacog_score)

        new_stage, next_review = srs.record_answer(item["id"], correct, metacog_score, today)
        print(f"   -> Dijadwalkan ulang: {new_stage} (tanggal {next_review.isoformat()})")

        db.exec(
            "INSERT INTO session_log (session_id, material_id, correct, metacog_score) VALUES (?,?,?,?)",
            (session_id, item["id"], int(correct), metacog_score),
        )

    avg = sum(metacog_scores) / len(metacog_scores) if metacog_scores else 0
    session_rating = _ask_session_difficulty()

    db.exec(
        "UPDATE sessions SET items_reviewed=?, avg_metacog=?, session_rating=? WHERE id=?",
        (len(session_items), avg, session_rating, session_id),
    )

    clear_line()
    print(f"Sesi selesai! Rata-rata metakognisi: {avg:.2f}/4 | "
          f"Kesulitan sesi (self-rated): {session_rating}/4")
    print("Materi yang skornya rendah / salah sudah otomatis dijadwalkan H+1 "
          "supaya diulang besok (spaced repetition).")


def _check_answer(user_answer, correct_answer):
    """Pencocokan longgar: case-insensitive, spasi ekstra & tanda baca diabaikan
    untuk beberapa tipe jawaban singkat (angka/pinyin/grammar sederhana)."""
    def norm(s):
        return " ".join(s.lower().replace(".", "").replace(",", "").split())
    return norm(user_answer) == norm(correct_answer)


def _ask_metacognition():
    print("\nSeberapa yakin Anda dengan jawaban tadi? (metakognisi)")
    print("  1 = Lupa total / menebak      2 = Ingat tapi ragu-ragu")
    print("  3 = Ingat, cukup yakin        4 = Sangat yakin / mudah")
    while True:
        val = input("Nilai (1-4) >> ").strip()
        if val in ("1", "2", "3", "4"):
            return int(val)
        print("Masukkan angka 1-4.")


def _ask_session_difficulty():
    print("\nSecara keseluruhan, seberapa menantang sesi hari ini? (1=terlalu mudah, 4=sangat menantang)")
    while True:
        val = input("Nilai (1-4) >> ").strip()
        if val in ("1", "2", "3", "4"):
            return int(val)
        print("Masukkan angka 1-4.")


# ==============================================================================
# 8. DASHBOARD / PROGRESS TRACKER
# ==============================================================================

def show_progress(db: Database):
    print("\n=== PROGRESS TRACKER ===")
    for lang in ("en", "zh"):
        print(f"\n-- {LANG_LABEL[lang]} --")
        for level in ("basic", "intermediate", "advanced"):
            total = db.q1(
                "SELECT COUNT(*) c FROM materials WHERE lang=? AND level=?", (lang, level)
            )["c"]
            mastered = db.q1(
                """SELECT COUNT(*) c FROM materials m JOIN progress p ON p.material_id=m.id
                   WHERE m.lang=? AND m.level=? AND p.interval_stage IN ('H7','H14','mastered')""",
                (lang, level),
            )["c"]
            print(f"   {level:<12}: {mastered}/{total} materi sudah di tahap H7+ / mastered")

    due_today = db.q1(
        "SELECT COUNT(*) c FROM progress WHERE next_review <= ?",
        (datetime.date.today().isoformat(),),
    )["c"]
    print(f"\nMateri jatuh tempo (due) untuk direview hari ini: {due_today}")

    last_sessions = db.q("SELECT * FROM sessions ORDER BY id DESC LIMIT 5")
    if last_sessions:
        print("\n5 sesi terakhir:")
        for s in last_sessions:
            print(f"   {s['date']} | {s['items_reviewed']} soal | "
                  f"avg metakognisi {s['avg_metacog']:.2f} | kesulitan sesi {s['session_rating']}")


# ==============================================================================
# 9. MAIN MENU
# ==============================================================================

def main():
    db = Database()
    srs = SRSEngine(db)
    interleaver = Interleaver(db)

    banner = """
    ============================================
      LINGO-SRS : English & Mandarin Learning
      Retrieval x Spaced Repetition x Interleaving
    ============================================
    """
    print(textwrap.dedent(banner))

    while True:
        print("\nMENU:")
        print("  1) Mulai sesi belajar hari ini (Retrieval Practice)")
        print("  2) Lihat progress / dashboard")
        print("  3) Lihat rekomendasi jalur Coursera (Financial Aid)")
        print("  4) Lihat peta kurikulum (Pemula -> Menengah -> Mahir)")
        print("  0) Keluar")
        choice = input("Pilih menu >> ").strip()

        if choice == "1":
            run_session(db, srs, interleaver)
        elif choice == "2":
            show_progress(db)
        elif choice == "3":
            print_coursera_tracks()
        elif choice == "4":
            print("\n=== PETA KURIKULUM ===")
            for lang, label in (("en", "BAHASA INGGRIS"), ("zh", "BAHASA MANDARIN")):
                print(f"\n-- {label} --")
                for level in ("basic", "intermediate", "advanced"):
                    topics = ", ".join(CURRICULUM_MAP[lang][level])
                    print(f"  {level.upper():<13}: {topics}")
        elif choice == "0":
            print("Sampai jumpa! Progres tersimpan otomatis di lingo_srs.db")
            sys.exit(0)
        else:
            print("Pilihan tidak dikenali.")


if __name__ == "__main__":
    main()
