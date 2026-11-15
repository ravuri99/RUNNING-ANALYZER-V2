"""Extract one Apple Fitness individual workout-detail screenshot to history.json.

Usage: python3.12 history.py IMG_2512.PNG --year 2026 --debug
Layout is calibrated to the dark Apple Fitness detail screenshots shown by the user.
OCR is not infallible: inspect all generated values, especially partial splits.
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import easyocr
from PIL import Image, ImageOps


def clean(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def compact(s):
    return clean(s).upper().replace("’", "'").replace("′", "'").replace("″", '"').replace(" ", "")


def clock(s):
    """Read workout duration (H:MM:SS), tolerating common OCR separators."""
    s = compact(s).replace("O", "0").replace("I", "1").replace("L", "1")
    s = re.sub(r"(?<=\d)[.;](?=\d)", ":", s)
    # Some OCR engines use a single quotation mark instead of a colon.
    s = re.sub(r"(?<=\d)'(?=\d)", ":", s)
    m = re.search(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?!\d)", s)
    if m:
        hours, minutes, seconds = map(int, m.groups())
        if minutes < 60 and seconds < 60:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", s)
    if m and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def workout_duration(items):
    """Extract time from the top-left metric tile, including split OCR tokens."""
    candidates = region(items, .035, .49, .322, .400)
    # The metric may have been split into multiple OCR boxes.
    for r in sorted(candidates, key=lambda r: (-len(r["text"]), r["y"])):
        duration = clock(r["text"])
        if duration is not None and duration.count(":") == 2:
            return duration
    # Join by physical reading order (not arbitrary OCR order).
    ordered = sorted(candidates, key=lambda r: (round(r["y"] / .016), r["x"]))
    text = " ".join(r["text"] for r in ordered)
    duration = clock(text)
    if duration is not None and duration.count(":") == 2:
        return duration
    # If EasyOCR omitted the colon entirely, reconstruct H MM SS from digits
    # only when the result is unambiguous and lies inside the duration tile.
    for r in ordered:
        raw = compact(r["text"]).replace("O", "0").replace("I", "1")
        digits = re.sub(r"[^0-9]", "", raw)
        if len(digits) in (5, 6) and re.fullmatch(r"[0-9:.';]+", raw):
            hr = int(digits[:-4]); mm = int(digits[-4:-2]); ss = int(digits[-2:])
            if hr < 48 and mm < 60 and ss < 60:
                return f"{hr:02d}:{mm:02d}:{ss:02d}"
    return duration


def calories(s):
    """Read whole calories, preserving thousands separators (1,478 -> 1478)."""
    text = compact(s).replace("O", "0")
    # 1,478CAL / 1.478CAL / 1478CAL / 1 478 CAL
    # Some OCR creates a separate token for leading digit, handled by caller.
    matches = list(re.finditer(r"(?<!\d)(\d{1,3}(?:[,\.]\d{3})+|\d{1,6})\s*C[A4]L\b", text))
    if not matches:
        matches = list(re.finditer(r"(?<!\d)(\d{1,3}(?:[,\.]\d{3})+|\d{1,6})\s*C[A4]L", text))
    if not matches:
        return None
    values = []
    for match in matches:
        raw = match.group(1).replace(",", "").replace(".", "")
        value = int(raw)
        if 0 <= value <= 100000:
            values.append(value)
    return max(values) if values else None


def tile_calories(items, x0, x1):
    """Prefer the whole numeric value, even if EasyOCR split 1,478CAL."""
    values = region(items, x0, x1, .397, .481)
    # Standalone label tokens can be near, above or below the number.
    candidates = [calories(item["text"]) for item in values]
    candidates = [value for value in candidates if value is not None]
    # Combine tokens within the calorie-value row. For example, "1" + "478CAL".
    bottom = [item for item in values if item["y"] >= .423]
    bottom.sort(key=lambda item: item["x"])
    for collection in (bottom, values):
        joined = "".join(item["text"] for item in collection)
        parsed = calories(joined)
        if parsed is not None:
            candidates.append(parsed)
    return max(candidates) if candidates else None

def pace(s):
    s = compact(s).replace("O", "0")
    match = re.search(r"(\d{1,2})\s*[:'\"]\s*(\d{2})", s)
    if not match:
        return None
    mins, secs = map(int, match.groups())
    return round(mins + secs / 60, 2) if mins <= 60 and secs < 60 else None


def measure(s, unit, lower=0, upper=100000):
    text = compact(s).replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:" + unit + r")", text)
    if not m:
        return None
    value = float(m.group(1))
    return value if lower <= value <= upper else None


def ocr_items(reader, image_path):
    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    w, h = image.size
    results = reader.readtext(str(image_path), detail=1, paragraph=False)
    items = []
    for polygon, value, confidence in results:
        xs = [pt[0] for pt in polygon]
        ys = [pt[1] for pt in polygon]
        items.append({"text": clean(value), "x": (min(xs)+max(xs))/(2*w), "y": (min(ys)+max(ys))/(2*h), "confidence": round(float(confidence),3)})
    return sorted(items, key=lambda x: x["y"])


def region(items, x0, x1, y0, y1):
    return sorted([r for r in items if x0 <= r["x"] <= x1 and y0 <= r["y"] <= y1], key=lambda r:(r["y"],r["x"]))


def region_text(items, x0, x1, y0, y1):
    return " ".join(v["text"] for v in region(items,x0,x1,y0,y1))


def first_value(items, x0,x1,y0,y1, parser):
    values = region(items,x0,x1,y0,y1)
    # Try separate OCR tokens, then joined text in reading order.
    for item in values:
        result = parser(item["text"])
        if result is not None:
            return result
    return parser(" ".join(item["text"] for item in values))


def metric_by_label(items, labels, parser, *, card_top=.295, card_bottom=.655):
    """Read values relative to actual Apple Fitness metric labels.

    Apple changes the number of metric rows depending on whether elevation
    exists. Hardcoded pace/heart-rate coordinates therefore fail on indoor
    workouts. Only use a value in the same metric column just below its label.
    """
    labels = tuple(re.compile(pattern, re.I) for pattern in labels)
    body = [r for r in items if .025 <= r["x"] <= .98
            and card_top <= r["y"] <= card_bottom]
    anchors = [r for r in body if any(p.search(clean(r["text"])) for p in labels)]
    for anchor in anchors:
        x, y = anchor["x"], anchor["y"]
        # First check if EasyOCR included label and value in the same token.
        direct = parser(anchor["text"])
        if direct is not None:
            return direct
        # Two-column grid, left metric x~.1-.4, right metric x~.6-.9.
        left, right = (.035, .48) if x < .49 else (.49, .98)
        nearby = [r for r in body if left <= r["x"] <= right
                  and y + .003 < r["y"] <= min(y + .075, card_bottom)]
        nearby.sort(key=lambda r: (round(r["y"]/.012), r["x"]))
        for cell in nearby:
            value = parser(cell["text"])
            if value is not None:
                return value
        # Some OCR tokenizers split the number and unit.
        for i in range(len(nearby)):
            band = [r for r in nearby if abs(r["y"] - nearby[i]["y"]) <= .014]
            joined = "".join(r["text"] for r in sorted(band,key=lambda r:r["x"]))
            value = parser(joined)
            if value is not None:
                return value
    return None


def distance_miles(text):
    """Handle 1.00MI, 1.00 Ml, and OCR's occasional 1.OOMI."""
    t = compact(text)
    t = re.sub(r'(?<=\d)[O](?=[0-9O]*M[I1L])', '0', t)
    t = t.replace('O0MI','00MI').replace('OOMI','00MI')
    match = re.search(r'(?<!\d)(\d{1,3}[.,]\d{1,3}|\d{1,3})M[I1L]\b',t)
    if match:
        n = float(match.group(1).replace(',', '.'))
        return n if 0 < n <= 1000 else None
    return None


def heart_rate_bpm(text):
    # Only parse bare numbers when reading the specifically located heart-rate tile.
    return measure(text,r'B[P]?M',40,220)


def seconds_from_duration(value):
    if not value:
        return None
    parts = [int(p) for p in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def distance_from_label(items):
    """Try all tokens on the distance VALUE line; do not accept a lone trailing digit.

    OCR can split 2.09MI as [2.] [09] [MI] or even just [9].
    A bare 9 with no MI unit is not a confirmed distance.
    """
    labels = [r for r in items if .30 <= r['y'] <= .43
              and re.search(r'\bDistance\b', r['text'], re.I)]
    for anchor in labels:
        left,right = (.035,.48) if anchor['x'] < .49 else (.49,.98)
        nearby = [r for r in items if left <= r['x'] <= right
                  and anchor['y'] + .006 <= r['y'] <= anchor['y'] + .065]
        if not nearby:
            continue
        # Prefer a single complete token, e.g. 2.09MI.
        for t in nearby:
            value=distance_miles(t['text'])
            if value is not None:
                return value
        for y in sorted(set(round(r['y']/.015) for r in nearby)):
            row=[r for r in nearby if abs(r['y']-y*.015) <= .017]
            joined=''.join(t['text'] for t in sorted(row,key=lambda t:t['x']))
            value=distance_miles(joined)
            if value is not None:
                return value
    return None


def bare_heart_rate_near_label(items):
    labels=[r for r in items if .35 <= r['y'] <= .67
            and re.search(r'Avg\.?\s*Heart\s*Rate|Average\s*Heart\s*Rate',r['text'],re.I)]
    for anchor in labels:
        lo,hi=(.03,.48) if anchor['x']<.49 else (.49,.98)
        values=[r for r in items if lo<=r['x']<=hi
                and anchor['y']+.005 < r['y'] <= anchor['y']+.069]
        for token in sorted(values,key=lambda v:v['y']):
            found=heart_rate_bpm(token['text'])
            if found is not None: return int(found)
            value=re.fullmatch(r'\s*(\d{2,3})\s*',token['text'])
            if value and 40 <= int(value.group(1)) <= 220:
                return int(value.group(1))
    return None


def time_range_from_header(items):
    # Restrict to workout heading rather than status-bar clock at the top.
    tokens=[r for r in items if .14<=r['y']<=.245 and .17<=r['x']<=.85]
    tokens.sort(key=lambda r:(round(r['y']/.019),r['x']))
    raw=' '.join(t['text'] for t in tokens)
    matches=re.findall(r'(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})\s*([AP]\s*M)',raw,re.I)
    out=[]
    for hour,minute,ampm in matches:
        h,m=int(hour),int(minute)
        if 1<=h<=12 and m<60:
            out.append(f'{h}:{m:02d}{ampm.upper().replace(" ","")}')
    # Require two matches: a lone end time must not be used as start time.
    return (out[0],out[1]) if len(out)>=2 else (None,None)


def distance_consistency(distance,duration,avg_pace):
    """Recover a *provisional* distance only if OCR is absent or wildly inconsistent.

    Timing and average pace are independent numbers: 42:01 at 20:05/mi
    predicts about 2.09 miles. This is a fallback, not an exact GPS reading.
    """
    seconds=seconds_from_duration(duration)
    if not seconds or not avg_pace or avg_pace<=0:
        return distance,False
    estimate=seconds/(avg_pace*60)
    if not .01 <= estimate <= 200:
        return distance,False
    if distance is not None and abs(distance-estimate)<=max(.08,estimate*.10):
        return distance,False
    return round(estimate,2),True

def parse_date(items, year):
    header = region_text(items,.25,.78,.055,.13)
    match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[, ]+(\d{1,2})",header,re.I)
    if not match:
        return None,None
    label=f"{match.group(1).title()} {int(match.group(2))}"
    try:
        return datetime.strptime(f"{label} {year}","%b %d %Y").strftime("%Y-%m-%d"),label
    except ValueError:
        return None,label


def parse_split_rows(items):
    # Identify where the split table starts. OCR often misses the heading, so
    # fall back to the typical lower region of this Apple Fitness page.
    heading = next((i for i in items if "SPLITS" in compact(i["text"]) and i["y"]>.65),None)
    start = heading["y"] + .055 if heading else .79
    table = region(items,.02,.98,start,.94)
    anchors = [r for r in table if r["x"] < .16 and re.fullmatch(r"\d{1,2}",compact(r["text"]))]
    splits=[]
    seen=set()
    for anchor in anchors:
        n=int(anchor["text"])
        if not (1<=n<=100) or n in seen:
            continue
        seen.add(n)
        y=anchor["y"]
        row=[r for r in table if abs(r["y"]-y)<.013]
        time=clock(" ".join(r["text"] for r in row if .14<=r["x"]<.36))
        p=pace(" ".join(r["text"] for r in row if .36<=r["x"]<.65))
        hr_text=" ".join(r["text"] for r in row if .65<=r["x"]<.99)
        hr=measure(hr_text,r"B[P]?M",40,220)
        if hr is None:
            nums=re.findall(r"\d{2,3}",hr_text)
            for s in nums:
                if 40<=int(s)<=220:
                    hr=int(s)
                    break
        splits.append({"mile":n,"time":time,"pace":p,"heartRate":int(hr) if hr is not None else None})
    return sorted(splits,key=lambda r:r["mile"])


def extract_workout(image_path,year,debug=False):
    print("Loading EasyOCR model...")
    reader=easyocr.Reader(["en"],gpu=False)
    print("Recognizing workout details...")
    items=ocr_items(reader,image_path)
    if debug:
        debug_path=image_path.with_name(image_path.stem+"_ocr_debug.json")
        debug_path.write_text(json.dumps(items,indent=2),encoding="utf-8")
        print("OCR debug:",debug_path)

    date,date_label=parse_date(items,year)
    activity_items=region(items,.18,.72,.12,.20)
    activity=next((v["text"] for v in activity_items if re.search(r"Walk|Run|Cycl|Hik|Swim",v["text"],re.I)),None)
    start_time,end_time=time_range_from_header(items)
    # City is free text and must not be guessed from the user's location.
    loc_items=region(items,.18,.72,.185,.245)
    location=next((r["text"] for r in loc_items if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,40}",r["text"]) and not re.search(r"walk|run|PM|AM",r["text"],re.I)),None)

    # Workout Details card occupies approximately y=.31-.62 on supplied screenshots.
    duration=workout_duration(items)
    distance=distance_from_label(items)
    active=tile_calories(items,.04,.48)
    total=tile_calories(items,.49,.94)
    elevation=metric_by_label(items,(r"Elevation\s*Gain",),lambda s:measure(s,r"FT",0,100000))
    avg_pace=metric_by_label(items,(r"Avg\.?\s*Pace",r"Average\s*Pace"),pace)
    if avg_pace is None:
        avg_pace=first_value(items,.49,.97,.485,.55,pace)
    avg_hr=metric_by_label(items,(r"Avg\.?\s*Heart\s*Rate",r"Average\s*Heart\s*Rate"),heart_rate_bpm)
    if avg_hr is None:
        avg_hr=bare_heart_rate_near_label(items)
    if avg_hr is None:
        avg_hr=first_value(items,.04,.48,.565,.625,heart_rate_bpm)
    distance, distance_estimated = distance_consistency(distance,duration,avg_pace)

    effort_items=region(items,.04,.58,.655,.738)
    effort_score=next((int(t["text"]) for t in effort_items if re.fullmatch(r"\d{1,2}",t["text"]) and 1<=int(t["text"])<=10),None)
    effort_label=next((t["text"] for t in effort_items if re.search(r"Easy|Moderate|Hard|All.Out",t["text"],re.I)),None)

    return {
        "date":date,"dateLabel":date_label,"activity":activity,
        "startTime":start_time,"endTime":end_time,"location":location,
        "duration":duration,"distanceMiles":distance,
        "distanceEstimatedFromPace":distance_estimated,
        "activeCalories":int(active) if active is not None else None,
        "totalCalories":int(total) if total is not None else None,
        "elevationGainFt":int(elevation) if elevation is not None else None,
        "averagePace":avg_pace,"averageHeartRate":int(avg_hr) if avg_hr is not None else None,
        "effort":{"score":effort_score,"label":effort_label},
        "splits":parse_split_rows(items),
    }


def main():
    parser=argparse.ArgumentParser(description="Extract Apple Fitness workout-detail screenshot")
    parser.add_argument("image",nargs="?",default="history.jpeg")
    parser.add_argument("--year",type=int,default=datetime.now().year)
    parser.add_argument("--debug",action="store_true")
    args=parser.parse_args()
    image_path=Path(args.image)
    if not image_path.is_absolute():image_path=Path(__file__).resolve().parent/image_path
    if not image_path.is_file():raise FileNotFoundError(f"Image not found: {image_path}")
    workout=extract_workout(image_path,args.year,args.debug)
    output=Path(__file__).resolve().parent/"history.json"
    # The API invokes this inside an isolated temporary folder; it then saves
    # workouts in browser history. Local CLI calls replace this one-off file.
    output.write_text(json.dumps(workout,indent=2),encoding="utf-8")
    print("Workout extraction completed. Splits:",len(workout["splits"]))
    print("Saved:",output)
    print("Review OCR output against the screenshot.")

if __name__=="__main__":main()
