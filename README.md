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
- แสดง Total / Completed / Pending + progress bar ต่อคน
- 3 อันดับแรกติดเหรียญ 🥇🥈🥉 + **กรอบเรืองแสงพัลส์** (ทอง/เงิน/ทองแดง) ปรับ tone เงินเข้มอัตโนมัติเมื่อสลับเป็น light mode
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

### Security

- Basic Authentication (optional) ผ่าน environment variables

## Project Structure

```
se-dashboard/
├── app.py                          # Flask backend + iSurvey client + SSE streaming
├── mapping_supervisor_staff_.json  # Supervisor → Staff mapping (reverse lookup)
├── templates/
│   ├── index.html                  # Frontend (Dashboard UI)
│   └── page2.html                  # Placeholder page (linked from FAB)
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
| `/page2`        | GET    | Placeholder page (navigated via FAB)             |
| `/fetch-stream` | POST   | SSE streaming fetch (pagination + progress)      |
| `/fetch`        | POST   | Non-streaming fetch (single response)            |

**Form parameters:**
- `date_from`, `date_to` — `YYYY-MM-DD`
- `report_type` — `enquiry` (default) / `closeClaim` / `claim`

## Notes

- Backend รองรับ 3 ประเภทรายงาน (enquiry / closeClaim / claim) — ปัจจุบัน UI ซ่อน toggle ไว้และใช้ `enquiry` เป็นค่าเริ่มต้น
- ช่วงวันที่จำกัดไม่เกิน 2 ปี (backend จะ return error ถ้าเกิน)
- Gunicorn timeout 600s สำหรับการดึงข้อมูลช่วงยาว
