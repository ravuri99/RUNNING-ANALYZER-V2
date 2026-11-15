"""Extract variable-length Apple Fitness split tables from 1–2 screenshots.

Usage: python3 splits_pages.py page1.png [page2.png]
Writes splits_pages.json. Review OCR output before relying on exact paces.
"""
import json
import re
import sys
from pathlib import Path

import easyocr
import numpy as np
from PIL import Image, ImageOps


def clean(value):
    return re.sub(r"\s+", " ", str(value)).strip()


def digits(value):
    return clean(value).upper().replace('O', '0').replace('I', '1').replace('L', '1')


def clock(value):
    v = digits(value).replace('’', "'").replace('′', "'").replace('″', '"')
    match = re.search(r"(?<!\d)(\d{1,2})\s*[:'\".;]\s*(\d{2})(?!\d)", v)
    if match and int(match.group(2)) < 60:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return None


def bpm(value):
    v = digits(value)
    found = re.search(r"(?<!\d)(\d{2,3})\s*B?P?M?\b", v)
    if found and 40 <= int(found.group(1)) <= 220:
        return int(found.group(1))
    return None


def _read_page_at_scale(reader, image, scale=1):
    width, height = image.size
    scan = image if scale == 1 else image.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)
    results = reader.readtext(np.asarray(scan), detail=1, paragraph=False)
    items = []
    for corners, content, confidence in results:
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        items.append({"x": (min(xs) + max(xs)) / (2 * width * scale),
                      "y": (min(ys) + max(ys)) / (2 * height * scale),
                      "text": clean(content), "confidence": float(confidence)})
    # Anchor rows by the printed mile number at the left of the Apple Fitness table.
    anchors = []
    for item in items:
        raw = digits(item['text'])
        if item['x'] <= .20 and item['y'] >= .15 and re.fullmatch(r'\d{1,2}', raw):
            mile = int(raw)
            if 1 <= mile <= 99:
                anchors.append((mile, item))
    rows = []
    for mile, anchor in anchors:
        near = [item for item in items if abs(item['y'] - anchor['y']) <= .018]
        def column(left, right):
            return ' '.join(item['text'] for item in sorted(near,key=lambda item:item['x'])
                            if left <= item['x'] < right)
        time = clock(column(.15, .37))
        pace = clock(column(.34, .66))
        rate = bpm(column(.64, .99))
        # Ignore non-table numbers: a row must have at least 2 plausible values.
        if sum(v is not None for v in (time, pace, rate)) < 2:
            continue
        pace_minutes = None
        if pace:
            mm, ss = map(int, pace.split(':'))
            pace_minutes = round(mm + ss / 60, 4)
        rows.append({"mile":mile, "time":time, "pace":pace_minutes,
                     "heartRate":rate})
    # Duplicate mile anchors in one screenshot can arise from OCR artifacts.
    unique = {}
    for row in rows:
        mile = row['mile']
        if mile not in unique or score(row) > score(unique[mile]):
            unique[mile] = row
    return [unique[n] for n in sorted(unique)]


def _row_crop(reader, image, y, mile):
    """Retry one predicted *visible* row, rather than OCRing the whole page again.

    Row position is derived from neighboring numbered rows. The program never
    fills in pace/heart-rate from an interpolation of neighboring values.
    """
    width, height = image.size
    cy = int(y * height)
    half = max(18, int(height * .019))
    top, bottom = max(0, cy - half), min(height, cy + half)
    crop = image.crop((0, top, width, bottom))
    # The Apple Fitness table has very small, low-contrast text. Upscaling only
    # this row often catches digits missed by a whole-screenshot OCR pass.
    crop = crop.resize((width * 3, (bottom - top) * 3), Image.Resampling.LANCZOS)
    scans = [np.asarray(crop)]
    # Different OCR inputs help with faint gray mile numbers and colored text.
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    scans.append(np.asarray(gray.convert('RGB')))
    best = None
    for scan in scans:
        detected = reader.readtext(scan, detail=1, paragraph=False)
        cells = []
        for polygon, text, confidence in detected:
            xs = [p[0] for p in polygon]
            cells.append(((min(xs) + max(xs)) / (2 * width * 3), clean(text)))
        def col(left, right):
            return ' '.join(txt for x, txt in sorted(cells) if left <= x < right)
        time = clock(col(.13, .33))
        pace = clock(col(.31, .57))
        rate = bpm(col(.55, .90))
        count = sum(v is not None for v in (time, pace, rate))
        # Require 2 independently recognized fields. Never synthesize fields.
        if count >= 2:
            minutes = None
            if pace:
                mm, ss = map(int, pace.split(':'))
                minutes = round(mm + ss / 60, 4)
            candidate = {'mile': mile, 'time': time, 'pace': minutes,
                         'heartRate': rate}
            if best is None or score(candidate) > score(best):
                best = candidate
    return best


def _recover_missing_rows(reader, image, merged):
    """Re-read gaps inferred from *consistent* numbered-row positions.

    Crucially, positions and mile indices come from recognized neighboring
    rows, not hardcoded screenshot ranges or an assumed upload order.
    """
    if len(merged) < 2:
        return [], []
    # Find y coordinates of known miles from a whole-page OCR run. Re-reading
    # the row at high resolution is attempted only after a gap is established.
    w, h = image.size
    raw = reader.readtext(np.asarray(image), detail=1, paragraph=False)
    points = {}
    for polygon, text, confidence in raw:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        x, y = (min(xs)+max(xs))/(2*w), (min(ys)+max(ys))/(2*h)
        match = re.fullmatch(r'\d{1,2}', digits(text))
        if x < .17 and y > .16 and match:
            number = int(match.group())
            if number in merged:
                points[number] = y
    if len(points) < 2:
        return [], []
    scales = [(points[b]-points[a])/(b-a)
              for a,b in zip(sorted(points), sorted(points)[1:])
              if b>a and points[b] > points[a]]
    if not scales:
        return [], []
    scales.sort()
    step = scales[len(scales)//2]
    # Reject layout-based inference if rows do not have a plausible spacing.
    if not .018 <= step <= .10:
        return [], []
    recovered, warnings = [], []
    first, last = min(merged), max(merged)
    # Infer leading mile 1 when OCR has started at mile 2 (or a few later).
    for missing in range(1, last + 1):
        if missing in merged:
            continue
        previous = max((m for m in points if m < missing), default=None)
        following = min((m for m in points if m > missing), default=None)
        if previous is None and following is None:
            continue
        if previous is not None and following is not None:
            local_step = (points[following]-points[previous])/(following-previous)
            if abs(local_step-step) > step * .25:
                continue
            y = points[previous] + (missing-previous)*local_step
        elif following is not None and following-missing <= 2:
            y = points[following]-(following-missing)*step
        else:
            continue
        if y < .17 or y > .98:
            continue
        candidate = _row_crop(reader, image, y, missing)
        if candidate is not None:
            merged[missing] = candidate
            recovered.append(missing)
            warnings.append(f'Mile {missing}: recovered from table row position; verify OCR values.')
    return recovered, warnings


def read_page(reader, image_path):
    with Image.open(image_path) as im:
        image = ImageOps.exif_transpose(im).convert('RGB')
    first = _read_page_at_scale(reader, image)
    merged = {row['mile']: row for row in first}
    # Always run a second pass: missing rows can occur at the beginning, end,
    # or inside a table, even if the first recognized rows are consecutive.
    second = _read_page_at_scale(reader, image, 1.7)
    for row in second:
        old = merged.get(row['mile'])
        if old is None or score(row) > score(old):
            merged[row['mile']] = row
    recovered, warnings = _recover_missing_rows(reader, image, merged)
    return [merged[m] for m in sorted(merged)], warnings


def score(row):
    return sum(row.get(key) is not None for key in ('time','pace','heartRate'))


def main(paths):
    if not 1 <= len(paths) <= 2:
        raise ValueError('Provide one or two splits screenshots.')
    reader = easyocr.Reader(['en'], gpu=False)
    combined = {}
    warnings = []
    per_page = []
    conflicts = []
    for number, path in enumerate(paths, 1):
        found, row_warnings = read_page(reader, path)
        warnings.extend(f"Screenshot {number}: {message}" for message in row_warnings)
        per_page.append(len(found))
        if not found:
            warnings.append(f'Screenshot {number}: no recognized split rows.')
        for row in found:
            mile = row['mile']
            if mile in combined:
                old = combined[mile]
                mismatch = [key for key in ('time', 'pace', 'heartRate')
                            if old.get(key) is not None and row.get(key) is not None
                            and old.get(key) != row.get(key)]
                if mismatch:
                    warnings.append(f'Mile {mile}: screenshots disagree on {", ".join(mismatch)}; verify against both images.')
                    conflicts.append({'mile': mile, 'fields': mismatch,
                                      'existing': dict(old), 'incoming': dict(row)})
                # Prefer more complete OCR, preserving original on ties.
                if score(row) > score(old):
                    combined[mile] = row
            else:
                combined[mile] = row
    ordered = [combined[m] for m in sorted(combined)]
    if ordered:
        gaps = [mile for mile in range(ordered[0]['mile'], ordered[-1]['mile'] + 1)
                if mile not in combined]
        if gaps:
            warnings.append('Missing mile rows: ' + ', '.join(map(str,gaps)) + '.')
        if len(ordered) > 1 and ordered[0]['mile'] != 1:
            warnings.append(f'First recognized mile is {ordered[0]["mile"]}, not 1.')
    result = {'success':bool(ordered), 'data':ordered, 'perPage':per_page, 'conflicts':conflicts,
              'totalMiles':len(ordered), 'warnings':warnings}
    Path('splits_pages.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Extracted',len(ordered),'unique splits across',len(paths),'image(s).')


if __name__ == '__main__':
    main(sys.argv[1:])
