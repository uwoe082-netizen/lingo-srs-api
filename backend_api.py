#!/usr/bin/env python3
"""
================================================================================
 LINGO-SRS - Backend API (FastAPI)
================================================================================
Ini adalah "otak" yang dipanggil oleh frontend React (hasil Lovable) lewat
HTTP, menggantikan panggilan langsung ke Supabase yang tadinya dipakai
Lovable. Semua ALGORITMA (spaced repetition H+1/H+3/H+7/H+14, interleaving,
database Turso) tetap 100% dari lingo_srs.py yang sudah teruji -- file ini
HANYA lapisan penerjemah HTTP <-> Python, tidak ada logika baru di sini
selain migrasi label/nama kolom supaya cocok dengan tipe data TypeScript
yang sudah dipakai frontend (lihat MAPPING DATA di bawah).

Jalankan lokal:
    pip install -r requirements-api.txt
    uvicorn backend_api:app --reload --port 8000

Deploy: lihat instruksi terpisah (Railway/Render/Fly.io -- Streamlit Cloud
TIDAK cocok untuk API generik seperti ini, cuma untuk app Streamlit).

--------------------------------------------------------------------------
MAPPING DATA (kenapa perlu -- supaya frontend TIDAK perlu diubah):

  Level     : basic/intermediate/advanced  <->  pemula/menengah/mahir
  Item type : vocab/grammar/tone/translate <->  kosakata/tata bahasa/nada/terjemahan
  Progress  : kolom 'material_id' (kita)   <->  'item_id' (frontend)
              kolom 'last_metacog'          <->  'last_confidence'
              kolom 'interval_stage' (enum) <->  'interval_days' (angka)
--------------------------------------------------------------------------
"""

import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import lingo_srs as core

app = FastAPI(title="Lingo-SRS API")

# PENTING: ganti allow_origins ke domain frontend Anda yang sebenarnya
# (misal ["https://saber-lingo.lovable.app"]) sebelum benar-benar dipakai
# jangka panjang -- "*" dipakai dulu supaya gampang disambungkan & dites.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = core.Database()
srs = core.SRSEngine(db)

LEVEL_TO_FE = {"basic": "pemula", "intermediate": "menengah", "advanced": "mahir"}
TYPE_TO_FE = {
    "vocab": "kosakata",
    "grammar": "tata bahasa",
    "tone": "nada",
    "translate": "terjemahan",
}
STAGE_TO_DAYS = {"new": 0, "H1": 1, "H3": 3, "H7": 7, "H14": 14, "mastered": 30}


def _iso_datetime(date_str, fallback_today=False):
    if not date_str:
        if not fallback_today:
            return None
        date_str = datetime.date.today().isoformat()
    return f"{date_str}T00:00:00.000Z"


@app.get("/items")
def get_items():
    rows = db.q(
        "SELECT id, lang, level, topic, item_type, prompt, answer, hint FROM materials"
    )
    return [
        {
            "id": str(r["id"]),
            "language": r["lang"],
            "level": LEVEL_TO_FE[r["level"]],
            "topic": r["topic"],
            "type": TYPE_TO_FE[r["item_type"]],
            "prompt": r["prompt"],
            "answer": r["answer"],
            "hint": r["hint"] or "",
        }
        for r in rows
    ]


@app.get("/progress")
def get_progress():
    rows = db.q("SELECT * FROM progress")
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r["material_id"]),
                "item_id": str(r["material_id"]),
                "times_seen": r["times_seen"] or 0,
                "times_correct": r["times_correct"] or 0,
                "times_wrong": r["times_wrong"] or 0,
                "last_confidence": r["last_metacog"],
                "streak": r["streak"] or 0,
                "interval_days": STAGE_TO_DAYS.get(r["interval_stage"], 0),
                "due_at": _iso_datetime(r["next_review"], fallback_today=True),
            }
        )
    return out


@app.get("/sessions")
def get_sessions():
    rows = db.q(
        "SELECT * FROM sessions WHERE items_reviewed > 0 ORDER BY id DESC LIMIT 20"
    )
    out = []
    for r in rows:
        correct_count = db.q1(
            "SELECT COUNT(*) c FROM session_log WHERE session_id=? AND correct=1",
            (r["id"],),
        )["c"]
        out.append(
            {
                "id": str(r["id"]),
                "started_at": _iso_datetime(r["date"]),
                "finished_at": _iso_datetime(r["date"]),
                "question_count": r["items_reviewed"],
                "correct_count": correct_count,
                "avg_confidence": r["avg_metacog"],
                "difficulty_rating": r["session_rating"],
            }
        )
    return out


@app.get("/trend")
def get_confidence_trend():
    """Tren rata-rata keyakinan diri per tanggal, dipakai grafik batang
    di halaman Progress (dibuat karena frontend/Lovable memanggil
    endpoint ini secara eksplisit -- lihat catatan di lingo-data.ts)."""
    rows = db.q(
        "SELECT date, avg_metacog FROM sessions "
        "WHERE items_reviewed > 0 AND avg_metacog IS NOT NULL "
        "ORDER BY date ASC"
    )
    by_date: dict[str, list[float]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r["avg_metacog"])
    return [
        {"date": d, "avg": sum(vals) / len(vals)}
        for d, vals in sorted(by_date.items())
    ]


class CreateSessionResponse(BaseModel):
    id: str


@app.post("/sessions", response_model=CreateSessionResponse)
def create_session():
    today = datetime.date.today()
    sid = db.insert_and_get_id(
        "INSERT INTO sessions (date, items_reviewed, avg_metacog) VALUES (?,0,0)",
        (today.isoformat(),),
    )
    return {"id": str(sid)}


class AnswerIn(BaseModel):
    session_id: str
    item_id: str
    user_answer: str
    correct: bool
    confidence: int


class AnswerOut(BaseModel):
    interval_stage: str
    due_at: str
    interval_days: int


@app.post("/answers", response_model=AnswerOut)
def record_answer(payload: AnswerIn):
    try:
        material_id = int(payload.item_id)
        session_id = int(payload.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="item_id/session_id harus angka")

    today = datetime.date.today()
    new_stage, next_review = srs.record_answer(
        material_id, payload.correct, payload.confidence, today
    )
    db.exec(
        "INSERT INTO session_log (session_id, material_id, correct, metacog_score) "
        "VALUES (?,?,?,?)",
        (session_id, material_id, int(payload.correct), payload.confidence),
    )
    return {
        "interval_stage": new_stage,
        "due_at": _iso_datetime(next_review.isoformat()),
        "interval_days": STAGE_TO_DAYS.get(new_stage, 0),
    }


class FinishIn(BaseModel):
    items_reviewed: int
    avg_confidence: float
    difficulty_rating: int


@app.post("/sessions/{session_id}/finish")
def finish_session(session_id: str, payload: FinishIn):
    try:
        sid = int(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id harus angka")
    db.exec(
        "UPDATE sessions SET items_reviewed=?, avg_metacog=?, session_rating=? WHERE id=?",
        (payload.items_reviewed, payload.avg_confidence, payload.difficulty_rating, sid),
    )
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "using_turso": db.using_turso}
