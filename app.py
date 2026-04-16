import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from queue import Empty, Queue

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()


def check_basic_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_user = os.getenv('AUTH_USER')
        auth_pass = os.getenv('AUTH_PASS')
        if not auth_user or not auth_pass:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != auth_user or auth.password != auth_pass:
            return Response(
                'Login required.', 401,
                {'WWW-Authenticate': 'Basic realm="SE Report"'},
            )
        return f(*args, **kwargs)
    return decorated

BASE_URL = 'https://cloud.isurvey.mobi/web/php'

# Maximum days per API chunk – keeps each request small enough to avoid
# server-side read timeouts on large date ranges.
CHUNK_DAYS = 30

# Records per page from iSurvey API
PAGE_LIMIT = 5000

# Number of chunks fetched in parallel from iSurvey
PARALLEL_CHUNKS = 4

# Fields kept on the server before streaming to the browser. iSurvey returns ~48
# fields per record but the dashboard only reads a small subset (dedup keys,
# inspector identifiers, status, timestamps, and a few display values). Stripping
# here cuts SSE payload ~70% and keeps accumulated[] memory in check on large
# ranges (1 year = 200k+ records).
KEEP_FIELDS = frozenset({
    # dedup keys
    'survey_no', 'notify_no', 'claim_no',
    # inspector identifiers (differ per report_type)
    'checkByName', 'empName', 'empname', 'empcode',
    # status (used to filter ยกเลิกเคลม and count จบงาน)
    'stt_desc',
    # timestamps used by speed metrics
    'dispatch_dt', 'sendReport_dt', 'checker_dt', 'close_dt',
    # display values on closeClaim / claim views
    'travel_time', 'D_TOTAL_COST',
})


def _slim_record(r):
    if not isinstance(r, dict):
        return r
    return {k: v for k, v in r.items() if k in KEEP_FIELDS}


def _date_chunks(start: datetime, end: datetime, max_days: int = CHUNK_DAYS):
    """Yield (chunk_start, chunk_end) datetime pairs covering *start* → *end*
    in segments of at most *max_days* days each."""
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


class ISurveyClient:
    def __init__(self):
        self.session = requests.Session()
        # Allow enough pool connections for parallel chunk fetching
        adapter_kwargs = dict(
            pool_connections=PARALLEL_CHUNKS * 2,
            pool_maxsize=PARALLEL_CHUNKS * 2,
            max_retries=Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[502, 503, 504],
                allowed_methods=["GET", "POST"],
            ),
        )
        self.session.mount("https://", HTTPAdapter(**adapter_kwargs))
        self.session.mount("http://", HTTPAdapter(**adapter_kwargs))
        self._logged_in = False
        self._login_lock = threading.Lock()

    def login(self):
        # Double-check pattern: avoid acquiring the lock on every call
        if self._logged_in:
            return
        with self._login_lock:
            if self._logged_in:
                return
            res = self.session.post(
                f'{BASE_URL}/login.php',
                data={
                    'username': os.getenv('ISURVEY_USER'),
                    'password': os.getenv('ISURVEY_PASS'),
                },
                timeout=15,
            )
            res.raise_for_status()
            self._logged_in = True

    def _force_relogin(self):
        with self._login_lock:
            self._logged_in = False
        self.login()

    def get_report_page(self, params, timeout=60):
        """Fetch a single report page. Auto re-login once on 401/403 or invalid
        JSON (which usually means the session expired and iSurvey returned an
        HTML login page instead of the expected JSON payload)."""
        self.login()

        def _do_request():
            res = self.session.get(
                f'{BASE_URL}/report/get_data_report.php',
                params=params,
                timeout=timeout,
            )
            res.raise_for_status()
            return res.json()

        try:
            return _do_request()
        except requests.exceptions.HTTPError as e:
            if e.response is None or e.response.status_code not in (401, 403):
                raise
            self._force_relogin()
            return _do_request()
        except ValueError:
            # JSONDecodeError — session likely expired and we got HTML back
            self._force_relogin()
            return _do_request()

    def fetch_all_pages(self, date_from, date_to, report_type='enquiry'):
        """Fetch all records, splitting large date ranges into monthly chunks
        so the remote server doesn't time out."""
        df_start = datetime.strptime(date_from, '%d/%m/%Y')
        dt_end = datetime.strptime(date_to, '%d/%m/%Y')

        all_records = []

        for chunk_start, chunk_end in _date_chunks(df_start, dt_end):
            c_from = chunk_start.strftime('%d/%m/%Y')
            c_to = chunk_end.strftime('%d/%m/%Y')
            page = 1
            start = 0
            limit = PAGE_LIMIT

            while True:
                params = {
                    'con_date': 2,
                    'date_from': c_from,
                    'date_to': c_to,
                    'report_type': report_type,
                    'page': page,
                    'start': start,
                    'limit': limit,
                }
                body = self.get_report_page(params, timeout=60)

                if isinstance(body, dict):
                    records = body.get('arr_data', body.get('data', []))
                    total = body.get('total', body.get('totalCount', 0))
                else:
                    records = body
                    total = len(body)

                all_records.extend(records)

                if not records or start + limit >= int(total):
                    break

                page += 1
                start += limit

        return all_records, len(all_records)


COLUMN_MAP = {
    'enquiry': [
        ('claim_no', 'เลขเคลม'),
        ('preNotifyNo', 'preNotifyNo'),
        ('notify_no', 'เลขรับแจ้ง'),
        ('survey_no', 'เลขเซอเวย์'),
        ('policy_Type', 'ประเภทเคลม'),
        ('policy_no', 'เลขกรมธรรม์'),
        ('plate_no', 'ทะเบียนรถ'),
        ('acc_detail', 'ลักษณะเหตุ'),
        ('acc_place', 'สถานที่เกิดเหตุ'),
        ('acc_amphur', 'อำเภอที่เกิดเหตุ'),
        ('acc_province', 'จังหวัดที่เกิดเหตุ'),
        ('survey_amphur', 'อำเภอที่ออกตรวจสอบ'),
        ('survey_province', 'จังหวัดที่ออกตรวจสอบ'),
        ('police_station', 'พิ้นที่สน.'),
        ('acc_verdict_desc', 'ถูก/ผิด/ร่วม/ไม่พบ/ไม่ยุติ'),
        ('empcode', 'พนักงานตรวจสอบ'),
        ('assign_reason', 'เหตุผลการจ่ายงาน'),
        ('emp_phone', 'เบอร์โทรศัพท์พนักงาน'),
        ('useOSS', 'ใช้เซอร์เวย์นอก'),
        ('branch', 'ศูนย์'),
        ('tp_insure', '(คู่กรณี) มี/ไม่มี ประกัน/ไม่มีคู่กรณี'),
        ('acc_zone', 'เขต (กท./ปม/ตจว)'),
        ('claim_Type', 'ประเภทเคลม(ว.4/นัดหมาย)'),
        ('wrkTime', 'ใน/นอก(เวลางาน)'),
        ('COArea', 'นอกพื้นที่'),
        ('service_type', 'ประเภทบริการ'),
        ('extraReq', 'ว.7'),
        ('notified_dt', 'วันที่/เวลารับแจ้ง'),
        ('dispatch_dt', 'วันที่/เวลาจ่ายงาน'),
        ('confirm_dt', 'วันที่/เวลารับงาน'),
        ('arrive_dt', 'วันที่/เวลาถึง ว.22'),
        ('cmp_arrive', 'ถึงที่เกิดเหตุ(ก่อน/หลัง คู่กรณี)'),
        ('finish_dt', 'วันที่/เวลาเสร็จงาน ว.14'),
        ('sendReport_dt', 'วันที่/เวลาส่งรายงาน'),
        ('travel_time', 'สรุปเวลา'),
        ('veh', 'การชน(รถ)'),
        ('ast', 'ทรัพย์สิน'),
        ('inj', 'ผู้บาดเจ็บ'),
        ('ctotal', 'รวม'),
        ('recover_dmg_pymt', 'จำนวนเงินเรียกร้อง'),
        ('remark', 'หมายเหตุ'),
        ('notified_name', 'ผู้รับแจ้ง'),
        ('dispatch_name', 'ผู้จ่ายงาน'),
        ('checkByName', 'ผู้ตรวจสอบงาน'),
        ('checker_dt', 'วันที่/เวลาตรวจสอบ'),
        ('stt_desc', 'สถานะงาน'),
        ('EMCSstatus', 'EMCSstatus'),
        ('EMCSby', 'EMCSby'),
        ('EMCSdate', 'EMCSdate'),
    ],
    'closeClaim': [
        ('empname', 'ผู้ปิดงาน'),
        ('close_dt', 'วันที่/เวลาตรวจสอบ'),
        ('claim_no', 'เลขเคลม'),
        ('notify_no', 'เลขรับแจ้ง'),
        ('survey_no', 'เลขเซอเวย์'),
        ('plate_no', 'ทะเบียนรถ'),
        ('acc_detail', 'ลักษณะเหตุ'),
        ('acc_place', 'สถานที่เกิดเหตุ'),
        ('notified_name', 'ผู้รับแจ้ง'),
        ('notified_dt', 'เวลารับแจ้ง'),
        ('dispatch_dt', 'เวลาจ่ายงาน'),
        ('arrive_dt', 'ถึงที่เกิดเหตุ ว.22'),
        ('finish_dt', 'เสร็จงาน ว.14'),
        ('sendReport_dt', 'ส่งรายงาน'),
        ('travel_time', 'สรุปเวลา'),
    ],
    'claim': [
        ('sttcase_ID', 'สถานะเคส'),
        ('empName', 'พนักงานตรวจสอบ'),
        ('claim_no', 'เลขเคลม'),
        ('notify_no', 'เลขรับแจ้ง'),
        ('survey_no', 'เลขเซอเวย์'),
        ('policy_Type', 'ประเภทกรมธรรม์'),
        ('plate_no', 'ทะเบียนรถ'),
        ('tp', 'คู่กรณี'),
        ('tp_insured', 'ผู้เอาประกันคู่กรณี'),
        ('tp_policy_type', 'ประเภทกรมธรรม์คู่กรณี'),
        ('tp_policy_no', 'เลขกรมธรรม์คู่กรณี'),
        ('tp_insure', '(คู่กรณี) มี/ไม่มี ประกัน/ไม่มีคู่กรณี'),
        ('tp_type', 'ประเภทคู่กรณี'),
        ('D_TOTAL_COST', 'ค่าเสียหายรวม'),
        ('tp_cost', 'ค่าเสียหายคู่กรณี'),
        ('inj', 'ผู้บาดเจ็บ'),
        ('acc_detail', 'ลักษณะเหตุ'),
        ('acc_place', 'สถานที่เกิดเหตุ'),
        ('acc_type_desc', 'ประเภทเหตุ'),
        ('acc_verdict_desc', 'ถูก/ผิด/ร่วม/ไม่พบ/ไม่ยุติ'),
        ('claim_Type', 'ประเภทเคลม(ว.4/นัดหมาย)'),
        ('wrkTime', 'ใน/นอก(เวลางาน)'),
        ('acc_zone', 'เขต (กท./ปม/ตจว)'),
        ('survey_amphur_th', 'อำเภอที่ออกตรวจสอบ'),
        ('survey_province_th', 'จังหวัดที่ออกตรวจสอบ'),
        ('TOTAL_SUM', 'ยอดรวม'),
        ('INS_INVEST', 'ค่าตรวจสอบ'),
        ('INS_TRANS', 'ค่าเดินทาง'),
        ('INS_OTHER', 'ค่าใช้จ่ายอื่น'),
        ('INS_PHOTO', 'ค่าถ่ายรูป'),
        ('INS_DAILY', 'ค่าเบี้ยเลี้ยง'),
        ('INS_CLAIM', 'ค่าเคลม'),
        ('UNITPRICE', 'ราคาต่อหน่วย'),
        ('dispatch_dt', 'วันที่/เวลาจ่ายงาน'),
        ('close_dt', 'วันที่/เวลาปิดเคส'),
        ('review_dt', 'วันที่/เวลาตรวจทาน'),
        ('appv_dt', 'วันที่/เวลาอนุมัติ'),
        ('memo', 'หมายเหตุ'),
        ('EMCSstatus', 'EMCSstatus'),
        ('EMCSby', 'EMCSby'),
        ('EMCSdate', 'EMCSdate'),
    ],
}

app = Flask(__name__)
client = ISurveyClient()

# Build reverse lookup: staff name → supervisor name
_mapping_path = os.path.join(os.path.dirname(__file__), 'mapping_supervisor_staff_.json')
STAFF_SUPERVISOR_MAP = {}
if os.path.exists(_mapping_path):
    with open(_mapping_path, encoding='utf-8') as f:
        _mapping = json.load(f)
    for supervisor, staff_list in _mapping.items():
        for staff in staff_list:
            STAFF_SUPERVISOR_MAP[staff.strip()] = supervisor.strip()


@app.route('/')
@check_basic_auth
def index():
    return render_template('index.html', column_map=COLUMN_MAP,
                           staff_supervisor_map=STAFF_SUPERVISOR_MAP)


@app.route('/page2')
@check_basic_auth
def page2():
    return render_template('page2.html')


@app.route('/fetch', methods=['POST'])
@check_basic_auth
def fetch():
    date_from = request.form.get('date_from', '')
    date_to = request.form.get('date_to', '')
    report_type = request.form.get('report_type', 'enquiry')

    try:
        df = datetime.strptime(date_from, '%Y-%m-%d').strftime('%d/%m/%Y')
        dt = datetime.strptime(date_to, '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return jsonify({'error': 'รูปแบบวันที่ไม่ถูกต้อง'}), 400

    try:
        records, total = client.fetch_all_pages(df, dt, report_type)
    except Exception as e:
        client._logged_in = False
        return jsonify({'error': str(e)}), 500

    columns = COLUMN_MAP.get(report_type)
    return jsonify({'total': total, 'data': records, 'columns': columns})


@app.route('/fetch-stream', methods=['POST'])
@check_basic_auth
def fetch_stream():
    date_from = request.form.get('date_from', '')
    date_to = request.form.get('date_to', '')
    report_type = request.form.get('report_type', 'enquiry')

    try:
        df_date = datetime.strptime(date_from, '%Y-%m-%d')
        dt_date = datetime.strptime(date_to, '%Y-%m-%d')
    except ValueError:
        def error_gen():
            yield f"event: error\ndata: {json.dumps({'error': 'รูปแบบวันที่ไม่ถูกต้อง'})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream')

    if (dt_date - df_date).days > 730:
        def range_error():
            yield f"event: error\ndata: {json.dumps({'error': 'ช่วงวันที่เกิน 2 ปี กรุณาเลือกช่วงที่สั้นกว่านี้'})}\n\n"
        return Response(range_error(), mimetype='text/event-stream')

    chunks = list(_date_chunks(df_date, dt_date))
    total_chunks = len(chunks)

    def generate():
        try:
            client.login()
        except Exception as e:
            client._logged_in = False
            yield f"event: error\ndata: {json.dumps({'error': f'Login failed: {e}'})}\n\n"
            return

        deadline = time.monotonic() + 3600  # 60 minutes
        event_queue = Queue()
        stop_event = threading.Event()

        def fetch_one_chunk(chunk_idx, chunk_start, chunk_end):
            c_from = chunk_start.strftime('%d/%m/%Y')
            c_to = chunk_end.strftime('%d/%m/%Y')
            page = 1
            start = 0
            limit = PAGE_LIMIT
            try:
                while not stop_event.is_set():
                    params = {
                        'con_date': 2,
                        'date_from': c_from,
                        'date_to': c_to,
                        'report_type': report_type,
                        'page': page,
                        'start': start,
                        'limit': limit,
                    }
                    body = client.get_report_page(params, timeout=60)

                    if isinstance(body, dict):
                        records = body.get('arr_data', body.get('data', []))
                        total = body.get('total', body.get('totalCount', 0))
                    else:
                        records = body
                        total = len(body)

                    records = [_slim_record(r) for r in records]
                    event_queue.put(('page', chunk_idx, page, records, int(total)))

                    if not records or start + limit >= int(total):
                        break
                    page += 1
                    start += limit
            except Exception as e:
                event_queue.put(('error', chunk_idx, str(e)))
            finally:
                event_queue.put(('chunk_done', chunk_idx))

        executor = ThreadPoolExecutor(max_workers=min(PARALLEL_CHUNKS, total_chunks))
        for idx, (cs, ce) in enumerate(chunks, 1):
            executor.submit(fetch_one_chunk, idx, cs, ce)

        fetched_count = 0
        chunks_completed = 0
        try:
            while chunks_completed < total_chunks:
                if time.monotonic() > deadline:
                    stop_event.set()
                    yield f"event: error\ndata: {json.dumps({'error': 'Request timed out (เกิน 60 นาที) ลองเลือกช่วงวันที่สั้นลง'})}\n\n"
                    return

                try:
                    ev = event_queue.get(timeout=1.0)
                except Empty:
                    continue

                kind = ev[0]
                if kind == 'page':
                    _, chunk_idx, page, records, total = ev
                    fetched_count += len(records)
                    if records:
                        yield f"event: batch\ndata: {json.dumps({'records': records})}\n\n"
                    yield f"event: progress\ndata: {json.dumps({'fetched': fetched_count, 'total': total, 'page': page, 'chunk': chunks_completed + 1, 'totalChunks': total_chunks})}\n\n"
                elif kind == 'chunk_done':
                    chunks_completed += 1
                elif kind == 'error':
                    _, chunk_idx, err = ev
                    stop_event.set()
                    client._logged_in = False
                    yield f"event: error\ndata: {json.dumps({'error': err})}\n\n"
                    return
        finally:
            stop_event.set()
            executor.shutdown(wait=False)

        columns = COLUMN_MAP.get(report_type)
        yield f"event: done\ndata: {json.dumps({'total': fetched_count, 'columns': columns})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
