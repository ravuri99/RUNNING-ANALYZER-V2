"""Local, single-user SQLite persistence. Data lives alongside the project in data/.
Set RUNNING_ANALYZER_DB to choose another file (absolute or relative path).
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = Path(os.environ.get('RUNNING_ANALYZER_DB', str(ROOT / 'data' / 'workouts.sqlite3')))

def connection():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('''CREATE TABLE IF NOT EXISTS workouts (
        id TEXT PRIMARY KEY, date TEXT NOT NULL, start_time TEXT,
        activity TEXT NOT NULL, source_hash TEXT,
        payload TEXT NOT NULL, updated_at TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_date ON workouts(date DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_hash ON workouts(source_hash)')
    return conn

def all_workouts():
    with connection() as conn:
        return [json.loads(r[0]) for r in conn.execute('SELECT payload FROM workouts ORDER BY date DESC, start_time DESC, updated_at DESC')]

def clean_workout(data):
    if not isinstance(data, dict):
        raise ValueError('Workout must be a JSON object')
    date = data.get('date')
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except (TypeError, ValueError):
        raise ValueError('Workout date must use YYYY-MM-DD')
    distance = data.get('distanceMiles')
    if distance is not None and (isinstance(distance, bool) or not isinstance(distance, (int,float)) or not 0 < distance < 1000):
        raise ValueError('Distance must be a positive number under 1000 miles')
    if not isinstance(data.get('activity'), str) or not data['activity'].strip():
        raise ValueError('Workout activity is required')
    if not isinstance(data.get('splits', []), list) or len(data.get('splits', [])) > 1000:
        raise ValueError('Invalid split rows')
    # Limit the amount of JSON being written locally.
    if len(json.dumps(data)) > 400_000:
        raise ValueError('Workout data is too large')
    return data

def save(data, workout_id=None):
    data = dict(clean_workout(data))
    data['id'] = workout_id or data.get('id') or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    data['savedAt'] = now
    with connection() as conn:
        # New records only: avoid duplicate screenshots or duplicate date+start+activity.
        if not workout_id:
            target = None
            source_hash = data.get('sourceHash')
            if source_hash:
                row = conn.execute('SELECT id FROM workouts WHERE source_hash=? ORDER BY updated_at DESC LIMIT 1', (source_hash,)).fetchone()
                if row: target = row[0]
            if target is None and data.get('startTime'):
                row = conn.execute('SELECT id FROM workouts WHERE date=? AND start_time=? AND activity=? LIMIT 1',
                                   (data['date'],data['startTime'],data['activity'])).fetchone()
                if row: target = row[0]
            if target:
                data['id'] = target
        # PUT only updates a known ID; never silently creates a new one.
        if workout_id and not conn.execute('SELECT 1 FROM workouts WHERE id=?', (workout_id,)).fetchone():
            raise KeyError('Workout not found')
        conn.execute('''INSERT INTO workouts(id,date,start_time,activity,source_hash,payload,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            date=excluded.date,start_time=excluded.start_time,activity=excluded.activity,
            source_hash=excluded.source_hash,payload=excluded.payload,updated_at=excluded.updated_at''',
            (data['id'],data['date'],data.get('startTime'),data['activity'],data.get('sourceHash'),json.dumps(data),now))
    return data

def remove(workout_id):
    with connection() as conn:
        return conn.execute('DELETE FROM workouts WHERE id=?',(workout_id,)).rowcount > 0

def import_workouts(items):
    if not isinstance(items,list) or len(items)>10000:
        raise ValueError('Expected an array of up to 10000 workouts')
    saved=0
    errors=[]
    for i,item in enumerate(items):
        try:
            copy=dict(item)
            # Keep stable IDs so re-importing the same backup does not duplicate records.
            save(copy)
            saved+=1
        except (TypeError,ValueError) as exc:
            errors.append(f'Record {i+1}: {exc}')
    return {'processed':saved,'errors':errors[:20]}
