# Running Analyzer — short workout OCR fix

This patch addresses an Apple Fitness 2.09-mile walk being read as **9 miles**, a missing average heart rate, and warnings for splits that do not exist.

## Replace files

Copy **history.py** and **app.js** into the existing `RUNNING-ANALYZER-COMPLETE` folder. Back up the originals first. Leave `api.py`, `splits_pages.py`, `index.html`, `store.py`, `main.py` and `data/workouts.sqlite3` untouched.

Restart the API (`Ctrl+C` in the Uvicorn terminal; then `python3.12 -m uvicorn api:app --reload --port 8000`) and hard-refresh the webpage (`Cmd+Shift+R`). Re-upload the original screenshots; code changes do not edit workouts already saved in SQLite.

## Changes

- **Distance OCR:** tries to recover complete numeric readings from the distance field, including split OCR tokens. If the distance is missing or wildly inconsistent with duration and average pace, suggests a two-decimal estimate (`duration / pace`) and marks it **estimated** for verification. Do not treat this estimate as a measured GPS distance.
- **Average HR:** can read a bare 2–3 digit heart-rate number directly beneath the `Avg. Heart Rate` label even if OCR misses `BPM`.
- **Time range:** reads both the start and end time from the header when present. If only one time is legible, it leaves the start time unknown rather than assuming the end time is the start.
- **Split validation:** validates against the workout's actual distance instead of using the largest detected mile number. Changes to the distance or split rows recheck the warning; two full miles plus a partial final mile counts as 3 rows, not 9.

## Validate the November 12 workout

| Field | Expected |
|---|---|
| Date | 2026-11-12 |
| Activity | Outdoor Walk |
| Start | 12:20 PM |
| Duration | 00:42:01 |
| Distance | 2.09 mi |
| Avg pace | 20:05/mi |
| Avg HR | 111 BPM |
| Active calories | 174 |
| Total calories | 244 |
| Elevation | 72 ft |

Splits: 1 = 20:53, 106 BPM; 2 = 19:04, 115 BPM; 3 = 02:02, pace 21:50, 132 BPM (partial).

Note that screenshot-derived split HR values may still require manual review, and the actual local EasyOCR result needs checking. Do not overwrite correct values with an estimate when reviewing.
