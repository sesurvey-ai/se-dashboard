# SE Dashboard

Dashboard สรุปรายงานเซอร์เวย์ (Survey Report) จากระบบ [iSurvey](https://cloud.isurvey.mobi) — Flask web app แสดง Summary cards และ Inspector cards แบบ real-time พร้อม Auto Refresh

## Tech Stack

| Layer    | Technology                                         |
| -------- | -------------------------------------------------- |
| Backend  | Python 3.13 / Flask                                |
| Frontend | Vanilla HTML + CSS + JavaScript                    |
| Charts   | Chart.js v4 (+ treemap, datalabels plugins)        |
| API      | iSurvey REST API                                   |

## Features

### Data Pipeline

- **SSE streaming fetch** — ดึงข้อมูลแบบ pagination อัตโนมัติพร้อม progress bar + ปุ่ม Cancel
- **Date range chunking** — แบ่ง date range ใหญ่ ๆ เป็น chunks ละ 14 วัน (`CHUNK_DAYS`) ป้องกัน server-side read timeout เมื่อ query ช่วงยาว
- **Batch streaming** — ส่ง records ทีละ page ผ่าน `event: batch` แทนการรวมทั้งหมดส่งครั้งเดียว → กัน `RangeError: Invalid string length` ของ JS (string > 512MB)
- **Auto retry** — retry 3 ครั้งเมื่อเจอ 502/503/504
- **Auto re-login** — ต่อ session ใหม่อัตโนมัติเมื่อ iSurvey หมดอายุระหว่างดึงข้อมูล
- **Dedup** — ลบแถวซ้ำ (key: `survey_no` → `notify_no` → `claim_no`) เก็บแถวที่มีข้อมูลครบที่สุด
- **Fill Supervisor** — เติมชื่อผู้ตรวจสอบงานอัตโนมัติ:
  - `survey_no` ขึ้นต้น `SEMS` / `SETP` → บังคับเป็น "นายสราวุธ บุญคุ้ม"
  - อื่น ๆ → reverse lookup จาก `mapping_supervisor_staff_.json`
- **Strip คำนำหน้าชื่อ** — ตัด นาย/นาง/นางสาว/น.ส./เด็กชาย/เด็กหญิง/ด.ช./ด.ญ. ออกจากชื่อที่แสดงใน Inspector Cards
- **ตัด "ยกเลิกเคลม"** ออกจากผลการนับทุก card

### Dashboard UI

**Summary Cards (Enquiry)**
- Total Claims — ทุกสถานะ ยกเว้น "ยกเลิกเคลม"
- Completed — เฉพาะสถานะ "จบงาน"
- Pending — ที่เหลือ

**Inspector Cards**
- การ์ดรายผู้ตรวจสอบงาน (checkByName) เรียงตามโหมดที่เลือก
- แสดง Total / Completed / Pending + progress bar ต่อคน (Pending ใช้สีม่วง)
- 3 อันดับแรกติดเหรียญ 🥇🥈🥉 + **กรอบเรืองแสงพัลส์** + **ลูกไฟวิ่งรอบกรอบ** (conic-gradient หมุนด้วย `@property --angle`) ปรับ tone เงินเข้มอัตโนมัติเมื่อสลับเป็น light mode
- ใช้สูตร `minmax(min(100%, 420px), 1fr)` → responsive ตั้งแต่ desktop ถึงมือถือโดยไม่ต้องใช้ media query
- `align-items: start` → การ์ด "(ว่าง)" ที่สูงกว่าไม่ยืดการ์ดเพื่อนในแถวเดียวกัน
- การ์ด "(ว่าง)" รวมงานที่ไม่มีผู้ตรวจสอบ พร้อมรายชื่อพนักงาน (empcode) จัดกลุ่มตามจำนวน — ต่อท้ายสุดเสมอ

**Sort modes** (dropdown + ปุ่ม `?` tooltip อธิบายสูตร)
- `score` *(default)* — `completed × pct` ถ่วงน้ำหนักปริมาณ × คุณภาพ
- `total` / `completed` / `pending` — มาก → น้อย
- `dispatch_speed` / `report_speed` — เวลาเฉลี่ย `checker_dt − dispatch_dt` / `− sendReport_dt`, น้อย → มาก

### Header

- **Collapsible toolbar** — ปุ่ม chevron หมุน ซ่อน/แสดง toolbar
- **Date range display** — แสดงช่วง from → to ตรงกลาง (พ.ศ.)
- **Real-time clock** — นาฬิกาอัปเดตทุกวินาที

### Toolbar

- **Date range From/To** พร้อม checkbox `Today`
  - ติ๊ก Today → ล็อค field ให้เป็นวันปัจจุบัน และ Auto Refresh จะ re-set เป็นวันปัจจุบันในแต่ละรอบ (แก้ปัญหาหน้าเว็บเปิดค้างข้ามคืน)
- **Auto Refresh** — ดึงข้อมูลใหม่ทุก 5 นาที (เช็ค Today-lock ก่อน submit)
- **Theme toggle** — Dark / Light (จำค่าใน localStorage)
- **Sort dropdown + Info tooltip** — เลือกวิธีเรียง Inspector Cards + hover ปุ่ม `?` ดูสูตรและเงื่อนไขของแต่ละโหมด
- **Floating Action Button** — ปุ่มวงกลมมุมขวาล่าง ไปหน้า `/page2`

### Page 2 — Inspector Rankings Charts

- **6 กราฟแท่งแนวนอน** ต่อ 1 report ใช้ข้อมูลจาก `sessionStorage` ไม่ต้อง fetch ซ้ำ
  - แถวบน: 🏆 คะแนนรวม · 🚀 จบงานเร็ว (วันจ่าย→ตรวจ) · 📝 จบงานเร็ว (ส่งรายงาน→ตรวจ)
  - แถวล่าง: 📦 จำนวนเคสทั้งหมด · ✅ จบงาน · ⏳ งานค้าง
- แท่ง top-3 ใช้สี **ทอง/เงิน/ทองแดง** เหมือนเหรียญ ที่เหลือเป็นเทา
- กราฟ `score` แสดง 🥇🥈🥉 แทนตัวเลข + ซ่อน tick แกน X (เน้นที่อันดับ ไม่ใช่ค่า)
- กราฟ speed ทั้ง 2 แสดงผลเป็น **จำนวนวัน** (`<1 วัน` ทศนิยม 2 ตำแหน่ง, `≥10 วัน` ปัดเต็ม)
- Responsive grid: 3 cols → 2 cols (≤1100px) → 1 col (≤720px) พร้อม `clamp()` font sizing
- Auto-sync เมื่อข้อมูลหน้า 1 เปลี่ยน (`pageshow` event + เทียบ `saved_at` timestamp)

### Session Cache (survive navigation)

- Fetch เสร็จ → strip records เหลือ **11 ฟิลด์ที่ใช้จริง** แล้วเก็บลง `sessionStorage['se_cache']`
- ลดขนาดจาก ~7 MB เหลือ ~0.7 MB (กัน `QuotaExceededError`)
- เก็บ snapshot ของ `date_from`, `date_to`, `report_type` จาก FormData ตอน submit (กัน race condition)
- กลับหน้า 1 → restore form + records + re-render ทันที ไม่ต้อง fetch ใหม่
- เปลี่ยน report_type หรือ fetch ใหม่ → ล้าง/เขียนทับ cache อัตโนมัติ

### Security

- Basic Authentication (optional) ผ่าน environment variables

## Project Structure

```
se-dashboard/
├── app.py                          # Flask backend + iSurvey client + SSE streaming
├── mapping_supervisor_staff_.json  # Supervisor → Staff mapping (reverse lookup)
├── templates/
│   ├── index.html                  # Frontend (Dashboard UI + session cache)
│   └── page2.html                  # Inspector Rankings — 6 bar charts per report
├── requirements.txt                # Python dependencies
├── .gitignore
└── .env                            # Environment variables (not tracked)
```

## Setup

### 1. Environment Variables

สร้างไฟล์ `.env`:

```env
ISURVEY_USER=<username>
ISURVEY_PASS=<password>
AUTH_USER=<basic_auth_user>      # optional
AUTH_PASS=<basic_auth_password>  # optional
```

### 2. Run

```bash
pip install -r requirements.txt
python app.py
```

เปิดเบราว์เซอร์ไปที่ `http://localhost:5000`

## API Endpoints

| Route           | Method | Description                                       |
| --------------- | ------ | ------------------------------------------------- |
| `/`             | GET    | Dashboard UI                                      |
| `/page2`        | GET    | Inspector Rankings — 6 bar charts (reads sessionStorage cache) |
| `/fetch-stream` | POST   | SSE streaming fetch (pagination + progress)      |
| `/fetch`        | POST   | Non-streaming fetch (single response)            |

**Form parameters:**
- `date_from`, `date_to` — `YYYY-MM-DD`
- `report_type` — `enquiry` (default) / `closeClaim` / `claim`

**SSE events** (`/fetch-stream`):
- `event: batch` — `{records: [...]}` — ชุด records ต่อ page
- `event: progress` — `{fetched, total, page, chunk, totalChunks}`
- `event: done` — `{total, columns}` (ไม่มี data แล้ว — รวม batches ฝั่ง frontend)
- `event: error` — `{error: "..."}`

## Configuration

| Constant / ค่า        | Default | หมายเหตุ                                   |
| --------------------- | ------- | ------------------------------------------ |
| `CHUNK_DAYS`          | 14      | จำนวนวันสูงสุดต่อ 1 request ไป iSurvey     |
| Per-request timeout   | 60s     | timeout ต่อ HTTP call                       |
| Overall deadline      | 60 นาที | timeout รวมของ SSE stream                   |
| Page size (`limit`)   | 1000    | records ต่อ page                            |
| Max date range        | 730 วัน (2 ปี) | เกินนี้ backend ตอบ error               |

## Notes

- Backend รองรับ 3 ประเภทรายงาน (enquiry / closeClaim / claim) — ปัจจุบัน UI ซ่อน toggle ไว้และใช้ `enquiry` เป็นค่าเริ่มต้น
- ช่วงวันที่จำกัดไม่เกิน 2 ปี (backend จะ return error ถ้าเกิน)
- Gunicorn timeout 600s สำหรับการดึงข้อมูลช่วงยาว
- **ดึง 1 ปีขึ้นไป:** record อาจถึงหลักล้าน กินหน่วยความจำเบราว์เซอร์ >1GB — แนะนำแบ่งดึงทีละไตรมาสถ้าเจอปัญหา memory
