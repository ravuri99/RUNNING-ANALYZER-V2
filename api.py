"""Local FastAPI backend for split and Apple Fitness workout screenshot OCR."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent
MAX_SIZE = 10 * 1024 * 1024
app = FastAPI(title="Running Analyzer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500", "http://127.0.0.1:3000", "http://localhost:3000"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Running Analyzer API is working!"}

@app.get("/health")
def health():
    return {"status": "ok"}

async def validated_image(file: UploadFile):
    if file.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(400, "Upload a JPEG or PNG image.")
    contents = await file.read(MAX_SIZE + 1)
    if not contents or len(contents) > MAX_SIZE:
        raise HTTPException(413, "Image must be between 1 byte and 10 MB.")
    # Verify actual image data, not just filename or Content-Type.
    from PIL import Image, UnidentifiedImageError
    import io
    try:
        with Image.open(io.BytesIO(contents)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "The uploaded file is not a valid image.")
    return contents

def run_script(script: str, work: Path, args: list[str]):
    source = ROOT / script
    if not source.is_file():
        raise HTTPException(500, f"Missing {script} beside api.py")
    # Per-request working directory prevents overlapping uploads from overwriting files.
    (work / script).write_bytes(source.read_bytes())
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    try:
        process = subprocess.run(
            [sys.executable, str(work / script), *args],
            cwd=str(work), env=env, capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "OCR processing timed out.")
    if process.returncode != 0:
        print(f"{script} failed:\n{process.stdout[-3000:]}\n{process.stderr[-6000:]}", flush=True)
        raise HTTPException(500, f"{script} failed; inspect the FastAPI terminal.")

@app.post("/extract")
async def extract_splits(file: UploadFile = File(...)):
    image = await validated_image(file)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # Current working main.py expects exactly this filename.
        (work / "photo.jpeg").write_bytes(image)
        run_script("main.py", work, [])
        output = work / "output.json"
        if not output.exists():
            raise HTTPException(500, "main.py did not create output.json")
        try:
            splits = json.loads(output.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise HTTPException(500, "Invalid output.json created by main.py")
        if not isinstance(splits, list):
            raise HTTPException(500, "Expected a JSON list of split records")
        return {"success": True, "totalMiles": len(splits), "data": splits}

@app.post("/history")
async def extract_history(file: UploadFile = File(...), year: int = Form(...)):
    if not 2000 <= year <= 2100:
        raise HTTPException(400, "Enter a valid workout year.")
    image = await validated_image(file)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "workout.jpeg").write_bytes(image)
        run_script("history.py", work, ["workout.jpeg", "--year", str(year)])
        output = work / "history.json"
        if not output.is_file():
            raise HTTPException(500, "history.py did not create history.json")
        try:
            result = json.loads(output.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise HTTPException(500, "Invalid history.json created by history.py")
        if isinstance(result, list):
            if not result:
                raise HTTPException(422, "No workout found in image")
            workout = result[0]
        elif isinstance(result, dict):
            workout = result
        else:
            raise HTTPException(500, "Unexpected history.json format")
        if not isinstance(workout, dict):
            raise HTTPException(500, "Invalid workout record")
        workout["sourceHash"] = hashlib.sha256(image).hexdigest()
        return {"success": True, "workout": workout}

@app.post("/extract-pages")
async def extract_split_pages(files: list[UploadFile] = File(...)):
    """Read 1–2 split-table screenshots and combine them into one workout."""
    if not 1 <= len(files) <= 2:
        raise HTTPException(400, "Upload one or two splits screenshots.")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        names = []
        for i, file in enumerate(files, 1):
            name = f"splits_page_{i}.png"
            (work / name).write_bytes(await validated_image(file))
            names.append(name)
        run_script("splits_pages.py", work, names)
        output = work / "splits_pages.json"
        if not output.is_file():
            raise HTTPException(500, "No combined splits output was generated.")
        try:
            result = json.loads(output.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise HTTPException(500, "Invalid combined splits output.")
        if not result.get("success"):
            raise HTTPException(422, "No split rows recognized. Verify the screenshots show the expanded splits table.")
        return result

# Local persistence API. Run only on your computer; no user authentication.
from pydantic import BaseModel
from store import all_workouts, save, remove, import_workouts

class WorkoutBody(BaseModel):
    workout: dict

class ImportBody(BaseModel):
    workouts: list[dict]

@app.get('/workouts')
def list_workouts():
    return {'workouts':all_workouts()}

@app.post('/workouts')
def create_workout(body: WorkoutBody):
    try:
        return {'workout':save(body.workout)}
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.put('/workouts/{workout_id}')
def update_workout(workout_id: str, body: WorkoutBody):
    try:
        return {'workout':save(body.workout,workout_id=workout_id)}
    except KeyError:
        raise HTTPException(404,'Workout not found')
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.delete('/workouts/{workout_id}')
def delete_workout(workout_id: str):
    if not remove(workout_id):
        raise HTTPException(404,'Workout not found')
    return {'deleted':True}

@app.post('/workouts/import')
def import_records(body: ImportBody):
    try:
        return import_workouts(body.workouts)
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.get('/workouts/export')
def export_records():
    return {'format':'running-analyzer-v2','workouts':all_workouts()}
