"""
청라 식물공장 재배대 현황 추출 스크립트
v2 - gspread 문자열 날짜 형식 처리 추가
"""

import json
import re
import os
from datetime import date, datetime

SPREADSHEET_ID = "19iY6VNhe4T2RVOsIX4vS5vIqHnaw3eWLGts27n17vNE"
SHEET_NAME     = "2026_청라"
OUTPUT_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bed_status.json")

MODELS = {
    "버터헤드": {"b0": -240.3315, "b1": 5.6829, "b2": 4.0142},
    "카이피라": {"b0": -221.4281, "b1": 5.8540, "b2": 2.4109},
}
TARGET_WEIGHT = 130.0


def parse_work_date(raw, ref_year=2026):
    """A열 작업 날짜 파싱 - gspread 문자열 형식 포함"""
    if raw is None:
        return None
    if hasattr(raw, "year"):
        return raw.date() if hasattr(raw, "date") else raw

    s = str(raw).strip()
    if not s:
        return None

    # ISO: "2026-03-19" or "2026-03-19 00:00:00"
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        pass

    # 공백 제거: "3 /19 목" → "3/19목"
    s_clean = re.sub(r'\s+', '', s)

    # "M/D" or "M/D요일": "3/19", "3/19목", "2/21토"
    m = re.match(r'^(\d{1,2})/(\d{1,2})', s_clean)
    if m:
        try:
            return date(ref_year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    return None


def parse_seed_dates(raw, ref_year=2026):
    """파종일 문자열 → 평균 date 반환"""
    if raw is None:
        return None
    if hasattr(raw, "year"):
        return raw.date() if hasattr(raw, "date") else raw

    s = str(raw).strip()
    if not s or s in ["x", "X", "-", ""]:
        return None

    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        pass

    m = re.match(r"^(\d{6})\.?\d*$", s)
    if m:
        code = m.group(1)
        try:
            return date(2000 + int(code[:2]), int(code[2:4]), int(code[4:6]))
        except Exception:
            pass

    s_clean = re.sub(r"\s*\.\s*", ",", s)
    tokens  = re.split(r"[\s,]+", s_clean)

    collected = []
    cur_month = None
    ry        = ref_year

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if re.match(r'^[월화수목금토일]$', token):
            continue

        m = re.match(r"^(\d{1,2})/(\d{1,2})[~-](\d{1,2})$", token)
        if m:
            mo, d1, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            cur_month = mo
            for dd in range(d1, d2 + 1):
                try: collected.append(date(ry, mo, dd))
                except: pass
            continue

        m = re.match(r"^(\d{1,2})[~-](\d{1,2})$", token)
        if m and cur_month:
            d1, d2 = int(m.group(1)), int(m.group(2))
            for dd in range(d1, d2 + 1):
                try: collected.append(date(ry, cur_month, dd))
                except: pass
            continue

        m = re.match(r"^(\d{1,2})/(\d{1,2})$", token)
        if m:
            mo, day = int(m.group(1)), int(m.group(2))
            if cur_month and mo < cur_month:
                ry += 1
            cur_month = mo
            try: collected.append(date(ry, cur_month, day))
            except: pass
            continue

        m = re.match(r"^(0[1-9]|1[0-2])(\d{2})$", token)
        if m:
            mo, day = int(m.group(1)), int(m.group(2))
            cur_month = mo
            try: collected.append(date(ry, cur_month, day))
            except: pass
            continue

        m = re.match(r"^(\d{1,2})$", token)
        if m and cur_month:
            day = int(m.group(1))
            if collected and day < collected[-1].day and cur_month == collected[-1].month:
                cur_month = cur_month % 12 + 1
                if cur_month == 1:
                    ry += 1
            try: collected.append(date(ry, cur_month, day))
            except: pass

    if not collected:
        return None
    avg_ord = sum(d.toordinal() for d in collected) / len(collected)
    return date.fromordinal(round(avg_ord))


def predict_harvest(seed_date, plant_date, today=None):
    if today is None:
        today = date.today()
    x1 = (today - seed_date).days
    x2 = (today - plant_date).days
    result = {}
    for variety, coef in MODELS.items():
        current_weight = coef["b0"] + coef["b1"] * x1 + coef["b2"] * x2
        daily_gain     = coef["b1"] + coef["b2"]
        if daily_gain > 0:
            days_to_target = (TARGET_WEIGHT - current_weight) / daily_gain
            if days_to_target <= 0:
                target_date    = today
                days_remaining = 0
            else:
                days_remaining = round(days_to_target)
                target_date    = date.fromordinal(today.toordinal() + days_remaining)
        else:
            days_remaining = None
            target_date    = None
        result[variety] = {
            "current_weight_g": round(max(0, current_weight), 1),
            "days_remaining":   days_remaining,
            "target_date":      str(target_date) if target_date else None,
        }
    return x1, x2, result


def load_rows_from_gspread():
    import gspread
    from google.oauth2.service_account import Credentials
    creds_path = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_KEY",
        os.path.join(os.path.dirname(__file__), "service-account-key.json"),
    )
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc    = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    return sheet.get_all_values()


def load_rows_from_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[SHEET_NAME]
    return [row for row in ws.iter_rows(values_only=True)]


SKIP_PATTERNS = ["x", "X", "정식 X", "정식X", "0.0", "고정", "누락", "수확", "???"]

def is_skip(text):
    t = str(text).strip()
    return any(p in t for p in SKIP_PATTERNS) or not t

def parse_bed_numbers(text):
    return [int(n) for n in re.findall(r"(\d+)번", str(text)) if 1 <= int(n) <= 20]


def extract_bed_status(rows):
    today        = date.today()
    found        = {}
    current_date = None

    # 진단: A열 샘플 5개 출력
    samples = [repr(str(r[0]).strip()) for r in rows if r and str(r[0]).strip()][:5]
    print(f"  [진단] A열 샘플: {samples}")

    for i in range(len(rows) - 1, 2, -1):
        row   = rows[i]
        a_val = row[0] if row else None
        r_val = row[17] if len(row) > 17 else None

        # ★ 핵심 수정: parse_work_date 사용
        if a_val and str(a_val).strip():
            parsed = parse_work_date(a_val)
            if parsed:
                current_date = parsed

        if not current_date:
            continue

        r_str = str(r_val).strip() if r_val else ""
        if not r_str or is_skip(r_str):
            continue

        bed_nums = parse_bed_numbers(r_str)
        if not bed_nums:
            continue

        for bed_num in bed_nums:
            if bed_num in found:
                continue

            seed_raw = None
            for j in range(i + 1, min(i + 5, len(rows))):
                nr   = rows[j]
                nr_r = nr[17] if len(nr) > 17 else None
                nr_m = nr[12] if len(nr) > 12 else None

                if nr_r and str(nr_r).strip() and not parse_bed_numbers(str(nr_r)):
                    candidate = str(nr_r).strip()
                    if not is_skip(candidate):
                        seed_raw = candidate
                        break
                if nr_m and str(nr_m).strip() and not parse_bed_numbers(str(nr_m)):
                    candidate = str(nr_m).strip()
                    if not is_skip(candidate):
                        seed_raw = candidate
                        break

            seed_date  = parse_seed_dates(seed_raw)
            plant_date = current_date

            prediction = None
            if seed_date:
                x1, x2, pred = predict_harvest(seed_date, plant_date, today)
                prediction = {
                    "seed_days":  x1,
                    "plant_days": x2,
                    "varieties":  pred,
                }

            found[bed_num] = {
                "bed_id":     bed_num,
                "plant_date": str(plant_date),
                "seed_date":  str(seed_date) if seed_date else None,
                "prediction": prediction,
                "updated_at": str(today),
            }

        if len(found) == 20:
            break

    return found


def main(use_local_xlsx=None):
    print("=" * 50)
    print("  청라 식물공장 재배대 현황 추출")
    print("=" * 50)

    if use_local_xlsx and os.path.exists(use_local_xlsx):
        print(f"📂 로컬 파일 로딩: {use_local_xlsx}")
        rows = load_rows_from_xlsx(use_local_xlsx)
    else:
        print("📡 Google Sheets 연결 중...")
        rows = load_rows_from_gspread()

    print(f"✅ {len(rows)}행 로드 완료")
    print("\n🔍 재배대 현황 파싱 중...")
    status = extract_bed_status(rows)

    print(f"\n{'재배대':6s} {'정식일':12s} {'파종일':12s} {'파종후':6s} {'정식후':6s} {'버터헤드':12s} {'카이피라':12s}")
    print("-" * 75)
    for bed_id in sorted(status.keys()):
        b = status[bed_id]
        p = b.get("prediction")
        if p:
            bh = p["varieties"].get("버터헤드", {})
            kp = p["varieties"].get("카이피라", {})
            print(
                f"  {bed_id:2d}번   {b['plant_date']:12s} {str(b['seed_date']):12s}"
                f" {p['seed_days']:4d}일  {p['plant_days']:4d}일"
                f"  {bh.get('current_weight_g',0):5.1f}g ({bh.get('days_remaining','?')}일후)"
                f"  {kp.get('current_weight_g',0):5.1f}g ({kp.get('days_remaining','?')}일후)"
            )
        else:
            print(f"  {bed_id:2d}번   {b['plant_date']:12s} {str(b['seed_date']):12s}  파종일 미확인")

    missing = [i for i in range(1, 21) if i not in status]
    if missing:
        print(f"\n⚠️  파싱 실패 재배대: {missing}")
    else:
        print(f"\n✅ 20개 재배대 모두 파싱 성공!")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n💾 저장 완료: {OUTPUT_PATH}")
    print(f"📅 기준일: {date.today()}")


if __name__ == "__main__":
    import sys
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else None
    main(use_local_xlsx=xlsx_path)
