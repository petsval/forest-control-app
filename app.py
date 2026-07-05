import csv
import html
import io
import os
import sqlite3
import hashlib
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', APP_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.environ.get('DB_PATH', os.path.join(DATA_DIR, 'forest_control.db'))
PORT = int(os.environ.get('PORT', '8010'))
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'muuda-see-parool')
ADMIN_TOKEN = hashlib.sha256(('mets-' + ADMIN_PASSWORD).encode('utf-8')).hexdigest()


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def esc(value):
    return html.escape('' if value is None else str(value))


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS stands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        client TEXT,
        location TEXT,
        status TEXT NOT NULL DEFAULT 'aktiivne',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS assortments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        machine TEXT,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        machine_type TEXT NOT NULL DEFAULT 'veduk',
        serial_no TEXT,
        owner_type TEXT NOT NULL DEFAULT 'oma',
        contractor TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS machine_drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(machine_id, driver_id)
    );
    CREATE TABLE IF NOT EXISTS stand_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stand_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        machine TEXT,
        note TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(stand_id, driver_id)
    );
    CREATE TABLE IF NOT EXISTS harvester_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stand_id INTEGER NOT NULL,
        assortment_id INTEGER NOT NULL,
        work_date TEXT,
        shift TEXT,
        machine TEXT,
        operator TEXT,
        quantity REAL NOT NULL,
        source_file TEXT,
        imported_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS forwarder_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stand_id INTEGER NOT NULL,
        assortment_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        entry_date TEXT NOT NULL,
        shift TEXT,
        machine TEXT,
        quantity REAL NOT NULL,
        loads INTEGER,
        haul_distance REAL,
        difficulty TEXT,
        note TEXT,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'salvestatud',
        overage_amount REAL NOT NULL DEFAULT 0,
        remaining_before REAL,
        updated_at TEXT,
        correction_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS extra_work_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stand_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        entry_date TEXT NOT NULL,
        shift TEXT,
        machine TEXT,
        work_name TEXT NOT NULL,
        hours REAL NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        correction_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id INTEGER,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    ''')
    driver_cols = [r[1] for r in cur.execute('PRAGMA table_info(drivers)').fetchall()]
    if 'company' not in driver_cols:
        cur.execute('ALTER TABLE drivers ADD COLUMN company TEXT')
    if 'driver_type' not in driver_cols:
        cur.execute("ALTER TABLE drivers ADD COLUMN driver_type TEXT NOT NULL DEFAULT 'oma'")
    existing_cols = [r[1] for r in cur.execute('PRAGMA table_info(forwarder_entries)').fetchall()]
    if 'updated_at' not in existing_cols:
        cur.execute('ALTER TABLE forwarder_entries ADD COLUMN updated_at TEXT')
    if 'correction_count' not in existing_cols:
        cur.execute('ALTER TABLE forwarder_entries ADD COLUMN correction_count INTEGER NOT NULL DEFAULT 0')
    if 'haul_distance' not in existing_cols:
        cur.execute('ALTER TABLE forwarder_entries ADD COLUMN haul_distance REAL')
    if 'difficulty' not in existing_cols:
        cur.execute('ALTER TABLE forwarder_entries ADD COLUMN difficulty TEXT')
    for code, client, location in [('L-124', 'Klient A', 'Objekt 1'), ('L-125', 'Klient A', 'Objekt 2'), ('L-130', 'Klient B', 'Objekt 3')]:
        cur.execute('INSERT OR IGNORE INTO stands(code, client, location, status, created_at) VALUES (?, ?, ?, ?, ?)', (code, client, location, 'aktiivne', now()))
    for name, machine in [('Jaan', 'Forwarder 1'), ('Mart', 'Forwarder 2'), ('Peeter', 'Forwarder 3')]:
        cur.execute('INSERT OR IGNORE INTO drivers(name, machine, active) VALUES (?, ?, 1)', (name, machine))
    for name, mtype, serial_no in [('Forwarder 1', 'veduk', ''), ('Forwarder 2', 'veduk', ''), ('Forwarder 3', 'veduk', ''), ('Ponsse Scorpion King', 'harvester', '')]:
        cur.execute('INSERT OR IGNORE INTO machines(name, machine_type, serial_no, owner_type, contractor, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)', (name, mtype, serial_no, 'oma', '', now()))
    for row in cur.execute('SELECT id, machine FROM drivers WHERE machine IS NOT NULL AND machine<>""').fetchall():
        m = cur.execute('SELECT id FROM machines WHERE name=?', (row['machine'],)).fetchone()
        if m:
            cur.execute('INSERT OR IGNORE INTO machine_drivers(machine_id, driver_id, active, created_at) VALUES (?, ?, 1, ?)', (m['id'], row['id'], now()))
    conn.commit()
    conn.close()


def get_or_create(cur, table, field, value):
    value = (value or '').strip()
    if not value:
        raise ValueError(f'{field} puudub')
    row = cur.execute(f'SELECT id FROM {table} WHERE {field}=?', (value,)).fetchone()
    if row:
        return row['id']
    if table == 'stands':
        cur.execute('INSERT INTO stands(code, status, created_at) VALUES (?, ?, ?)', (value, 'aktiivne', now()))
    elif table == 'assortments':
        cur.execute('INSERT INTO assortments(name) VALUES (?)', (value,))
    else:
        raise ValueError('Tundmatu tabel')
    return cur.lastrowid


def totals_for(cur, stand_id, assortment_id):
    harv = cur.execute('SELECT COALESCE(SUM(quantity),0) total FROM harvester_imports WHERE stand_id=? AND assortment_id=?', (stand_id, assortment_id)).fetchone()['total']
    fwd = cur.execute('SELECT COALESCE(SUM(quantity),0) total FROM forwarder_entries WHERE stand_id=? AND assortment_id=?', (stand_id, assortment_id)).fetchone()['total']
    return float(harv or 0), float(fwd or 0)


def classify_entry(cur, stand_id, assortment_id, quantity):
    harv, fwd_before = totals_for(cur, stand_id, assortment_id)
    remaining_before = harv - fwd_before
    if harv <= 0:
        return 'PRD puudub', 0.0, remaining_before
    after = fwd_before + quantity
    overage = max(0.0, after - harv)
    if overage > 0:
        return 'ületus', round(overage, 3), remaining_before
    return 'OK', 0.0, remaining_before



def recalc_entries(cur, stand_id, assortment_id):
    harv = cur.execute('SELECT COALESCE(SUM(quantity),0) total FROM harvester_imports WHERE stand_id=? AND assortment_id=?', (stand_id, assortment_id)).fetchone()['total']
    harv = float(harv or 0)
    running = 0.0
    entries = cur.execute('SELECT id, quantity FROM forwarder_entries WHERE stand_id=? AND assortment_id=? ORDER BY created_at, id', (stand_id, assortment_id)).fetchall()
    for e in entries:
        remaining_before = harv - running
        qty = float(e['quantity'] or 0)
        if harv <= 0:
            status = 'PRD puudub'
            overage = 0.0
        else:
            after = running + qty
            overage = max(0.0, after - harv)
            status = 'ületus' if overage > 0 else 'OK'
        cur.execute('UPDATE forwarder_entries SET status=?, overage_amount=?, remaining_before=? WHERE id=?', (status, round(overage, 3), round(remaining_before, 3), e['id']))
        running += qty

def overview_rows():
    conn = db()
    rows = conn.execute('''
        WITH h AS (SELECT stand_id, assortment_id, SUM(quantity) harvester_total FROM harvester_imports GROUP BY stand_id, assortment_id),
             f AS (SELECT stand_id, assortment_id, SUM(quantity) forwarder_total FROM forwarder_entries GROUP BY stand_id, assortment_id),
             keys AS (SELECT stand_id, assortment_id FROM h UNION SELECT stand_id, assortment_id FROM f)
        SELECT s.code stand_code, s.client, a.name assortment,
               COALESCE(h.harvester_total,0) harvester_total,
               COALESCE(f.forwarder_total,0) forwarder_total,
               COALESCE(f.forwarder_total,0)-COALESCE(h.harvester_total,0) diff
        FROM keys k
        JOIN stands s ON s.id=k.stand_id
        JOIN assortments a ON a.id=k.assortment_id
        LEFT JOIN h ON h.stand_id=k.stand_id AND h.assortment_id=k.assortment_id
        LEFT JOIN f ON f.stand_id=k.stand_id AND f.assortment_id=k.assortment_id
        ORDER BY s.code, a.name
    ''').fetchall()
    result = []
    for r in rows:
        d = dict(r)
        diff = float(d['diff'])
        harv = float(d['harvester_total'])
        fwd = float(d['forwarder_total'])
        if harv <= 0 and fwd > 0:
            status = 'PRD puudub'
        elif diff > 0:
            status = 'ületus'
        elif abs(diff) <= max(1.0, harv * 0.03):
            status = 'OK'
        else:
            status = 'jääk langil'
        d['status'] = status
        result.append(d)
    conn.close()
    return result




def stand_summary_rows():
    """Koondab andmed langi kaupa: yks rida iga langi kohta."""
    detail = overview_rows()
    grouped = {}
    for r in detail:
        code = r['stand_code']
        if code not in grouped:
            grouped[code] = {
                'stand_code': code,
                'client': r['client'],
                'harvester_total': 0.0,
                'forwarder_total': 0.0,
                'sortiments': 0,
                'has_overage': False,
                'has_missing_prd': False,
            }
        g = grouped[code]
        g['harvester_total'] += float(r['harvester_total'] or 0)
        g['forwarder_total'] += float(r['forwarder_total'] or 0)
        g['sortiments'] += 1
        if r['status'] == 'ületus':
            g['has_overage'] = True
        elif r['status'] == 'PRD puudub':
            g['has_missing_prd'] = True
    result = []
    for g in grouped.values():
        g['diff'] = g['forwarder_total'] - g['harvester_total']
        if g['has_overage'] or g['diff'] > 0:
            g['status'] = 'ületus'
        elif g['has_missing_prd']:
            g['status'] = 'PRD puudub'
        elif abs(g['diff']) <= max(1.0, g['harvester_total'] * 0.03):
            g['status'] = 'OK'
        else:
            g['status'] = 'jääk langil'
        result.append(g)
    result.sort(key=lambda x: x['stand_code'])
    return result


def haul_summary_by_stand():
    conn = db()
    rows = conn.execute("""SELECT s.code stand_code, AVG(fe.haul_distance) avg_haul_distance, GROUP_CONCAT(DISTINCT fe.difficulty) difficulties
                           FROM forwarder_entries fe
                           JOIN stands s ON s.id=fe.stand_id
                           GROUP BY s.id, s.code""").fetchall()
    conn.close()
    return {r['stand_code']: dict(r) for r in rows}


def haul_rows_for_stand(stand_code):
    conn = db()
    rows = conn.execute("""SELECT fe.created_at, d.name driver, fe.shift, fe.machine, fe.quantity, fe.haul_distance, fe.difficulty, fe.loads, fe.note
                           FROM forwarder_entries fe
                           JOIN stands s ON s.id=fe.stand_id
                           JOIN drivers d ON d.id=fe.driver_id
                           WHERE s.code=?
                           ORDER BY fe.created_at DESC""", (stand_code,)).fetchall()
    conn.close()
    return rows


def detail_rows_for_stand(stand_code):
    return [r for r in overview_rows() if r['stand_code'] == stand_code]


def extra_work_summary_by_stand():
    conn = db()
    rows = conn.execute('''SELECT s.code stand_code, s.client, COALESCE(SUM(e.hours),0) total_hours, COUNT(e.id) entries
                           FROM extra_work_entries e
                           JOIN stands s ON s.id=e.stand_id
                           GROUP BY s.id, s.code, s.client
                           ORDER BY s.code''').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def extra_work_rows_for_stand(stand_code):
    conn = db()
    rows = conn.execute('''SELECT e.*, s.code stand_code, d.name driver
                           FROM extra_work_entries e
                           JOIN stands s ON s.id=e.stand_id
                           JOIN drivers d ON d.id=e.driver_id
                           WHERE s.code=?
                           ORDER BY e.created_at DESC''', (stand_code,)).fetchall()
    conn.close()
    return rows


def assignment_count(cur=None):
    close = False
    if cur is None:
        conn = db(); cur = conn.cursor(); close = True
    row = cur.execute('SELECT COUNT(*) c FROM stand_assignments WHERE active=1').fetchone()
    c = int(row['c'] if row else 0)
    if close:
        conn.close()
    return c


def is_driver_allowed_for_stand(cur, driver_id, stand_id):
    # Kui tööjaotusi pole üldse tehtud, lubame vana lihtsa töövoo.
    if assignment_count(cur) == 0:
        return True
    # Kui konkreetsele langile pole kedagi määratud, lubame samuti, et testimine ei jääks kinni.
    stand_rows = cur.execute('SELECT COUNT(*) c FROM stand_assignments WHERE stand_id=? AND active=1', (stand_id,)).fetchone()['c']
    if int(stand_rows or 0) == 0:
        return True
    row = cur.execute('SELECT id FROM stand_assignments WHERE stand_id=? AND driver_id=? AND active=1', (stand_id, driver_id)).fetchone()
    return row is not None


def assigned_stands_for_driver(driver_id):
    conn = db(); cur = conn.cursor()
    if assignment_count(cur) == 0:
        rows = cur.execute("SELECT * FROM stands WHERE status='aktiivne' ORDER BY code").fetchall()
    else:
        rows = cur.execute("""SELECT DISTINCT s.* FROM stands s
                              JOIN stand_assignments sa ON sa.stand_id=s.id AND sa.active=1
                              WHERE s.status='aktiivne' AND sa.driver_id=?
                              ORDER BY s.code""", (driver_id,)).fetchall()
    conn.close()
    return rows


def active_machines(machine_type=None):
    conn = db()
    if machine_type:
        rows = conn.execute('SELECT * FROM machines WHERE active=1 AND machine_type=? ORDER BY name', (machine_type,)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM machines WHERE active=1 ORDER BY machine_type, name').fetchall()
    conn.close()
    return rows


def machine_driver_links():
    conn = db()
    rows = conn.execute('''SELECT md.*, m.name machine_name, m.machine_type, m.serial_no, m.owner_type, m.contractor, d.name driver_name, d.company, d.driver_type
                           FROM machine_drivers md
                           JOIN machines m ON m.id=md.machine_id
                           JOIN drivers d ON d.id=md.driver_id
                           WHERE md.active=1 AND m.active=1 AND d.active=1
                           ORDER BY m.machine_type, m.name, d.name''').fetchall()
    conn.close()
    return rows


def drivers_for_machine(machine_id):
    conn = db()
    rows = conn.execute('''SELECT d.* FROM drivers d
                           JOIN machine_drivers md ON md.driver_id=d.id AND md.active=1
                           WHERE d.active=1 AND md.machine_id=?
                           ORDER BY d.name''', (machine_id,)).fetchall()
    conn.close()
    return rows


def assignments_for_admin():
    conn = db()
    rows = conn.execute("""SELECT sa.*, s.code stand_code, s.client, d.name driver, d.machine driver_machine
                           FROM stand_assignments sa
                           JOIN stands s ON s.id=sa.stand_id
                           JOIN drivers d ON d.id=sa.driver_id
                           WHERE sa.active=1
                           ORDER BY s.code, d.name""").fetchall()
    conn.close()
    return rows


def layout(title, body, msg=''):
    message = f'<div class="flash">{esc(msg)}</div>' if msg else ''
    return f'''<!doctype html>
<html lang="et"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><link rel="stylesheet" href="/static/style.css"></head>
<body><nav><a href="/">Koond</a><a href="/vedukamees">Vedukamehe sisestus</a><a href="/admin/import">PRD import</a><a href="/admin/masinad">Masinad ja juhid</a><a href="/admin/lingid">Lingid</a><a href="/logout">Logi välja</a><a href="/admin/jaotus">Tööjaotus</a><a href="/admin/entries">Kanded</a><a href="/admin/drivers">Juhi raport</a><a href="/admin/lisatoode">Lisatööd</a><a href="/admin/export/excellent">Excellent eksport</a></nav>
<main>{message}{body}</main></body></html>'''.encode('utf-8')


def table(headers, rows, row_class=None):
    out = ['<table><thead><tr>']
    for h in headers:
        out.append(f'<th>{esc(h)}</th>')
    out.append('</tr></thead><tbody>')
    for r in rows:
        cls = row_class(r) if row_class else ''
        out.append(f'<tr class="{esc(cls)}">')
        for h in headers:
            out.append(f'<td>{r.get(h, "")}</td>')
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def status_class(status):
    return 'status-' + status.lower().replace(' ', '-')



def parse_multipart_file(content_type, body, field_name):
    marker = 'boundary='
    if marker not in content_type:
        return '', b''
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    delimiter = ('--' + boundary).encode('utf-8')
    for part in body.split(delimiter):
        if not part or part in (b'--\r\n', b'--'):
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        header_blob, sep, data = part.partition(b'\r\n\r\n')
        if not sep:
            continue
        headers = header_blob.decode('utf-8', errors='replace')
        if f'name="{field_name}"' not in headers:
            continue
        filename = ''
        if 'filename="' in headers:
            filename = headers.split('filename="', 1)[1].split('"', 1)[0]
        if data.endswith(b'\r\n'):
            data = data[:-2]
        if data.endswith(b'--'):
            data = data[:-2]
        return filename, data
    return '', b''


def prd_text_value(records, tag, subtag):
    rec = records.get((str(tag), str(subtag)))
    if rec is None:
        return ''
    parts = rec.split(None, 2)
    return parts[2].strip() if len(parts) >= 3 else ''


def prd_number_values(records, tag, subtag):
    rec = records.get((str(tag), str(subtag)))
    if rec is None:
        return []
    parts = rec.replace('\n', ' ').split()
    vals = []
    for x in parts[2:]:
        try:
            vals.append(int(x))
        except ValueError:
            pass
    return vals


def prd_parse_datetime(value):
    value = ''.join(ch for ch in (value or '') if ch.isdigit())
    for fmt in ('%Y%m%d%H%M%S', '%y%m%d%H%M%S'):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            pass
    return None


def normalize_prd_assortment(product_code):
    # V6: kasutame PRD failis olevat originaalset sortimendi/tootekoodi.
    # Vedukamehe valikus peab nimi olema sama, mis harvesteri raportis.
    return (product_code or '').strip() or 'Tundmatu'


def parse_ponsse_prd(file_bytes, filename):
    """Best-effort parser for Ponsse/StanForD Classic PRD text files.

    This reads stand and metadata from header records and production volume from
    record 236 + decimal companion 1236, subtype 1. Quantities are interpreted
    as cubic metres with three decimals and aggregated into broad assortments.
    """
    text = file_bytes.decode('iso-8859-1', errors='replace')
    if 'PRD' not in text[:200] or '~' not in text:
        return []
    records = {}
    for rec in text.split('~'):
        parts = rec.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit():
            records[(parts[0], parts[1])] = rec.strip()

    stand = prd_text_value(records, 21, 1) or prd_text_value(records, 21, 2) or os.path.splitext(filename)[0]
    client = prd_text_value(records, 2, 1) or prd_text_value(records, 31, 1)
    machine_brand = prd_text_value(records, 3, 5)
    machine_model = prd_text_value(records, 3, 6)
    machine = ' '.join(x for x in [machine_brand, machine_model] if x).strip() or prd_text_value(records, 21, 1)
    operator = prd_text_value(records, 212, 1)
    if operator and '.' in operator:
        operator = operator.split('.', 1)[1].strip()
    start_dt = prd_parse_datetime(prd_text_value(records, 11, 4) or prd_text_value(records, 11, 3))
    end_dt = prd_parse_datetime(prd_text_value(records, 12, 4) or prd_text_value(records, 12, 3))
    work_date = start_dt.date().isoformat() if start_dt else ''
    shift = ''
    if start_dt and end_dt:
        shift = start_dt.strftime('%d.%m %H:%M') + '-' + end_dt.strftime('%d.%m %H:%M')

    # Product codes are newline-separated. Record 121 1 has full names like
    # 4D-KUPA43611018; record 121 2 has short product codes like 4D.
    product_full = prd_text_value(records, 121, 1).split()
    product_short = prd_text_value(records, 121, 2).split()
    product_codes = product_full or product_short

    whole = prd_number_values(records, 236, 1)
    decimals = prd_number_values(records, 1236, 1)
    if not whole:
        whole = prd_number_values(records, 236, 10)
        decimals = prd_number_values(records, 1236, 10)
    if not whole:
        raise ValueError('PRD failist ei leidnud tootmismahu rida 236/1236')

    n = min(len(product_codes), len(whole))
    grouped = {}
    for i in range(n):
        q = float(whole[i]) + (float(decimals[i]) / 1000.0 if i < len(decimals) else 0.0)
        if q <= 0:
            continue
        assortment = normalize_prd_assortment(product_codes[i])
        grouped[assortment] = grouped.get(assortment, 0.0) + q

    if not grouped:
        raise ValueError('PRD failis olid mahuread nullis')

    rows = []
    for assortment, quantity in sorted(grouped.items()):
        rows.append({
            'lank': stand,
            'client': client,
            'sortiment': assortment,
            'kogus': round(quantity, 3),
            'kuupaev': work_date,
            'vahetus': shift,
            'masin': machine,
            'operaator': operator,
            'source_note': 'Ponsse PRD 236/1236'
        })
    return rows


class Handler(BaseHTTPRequestHandler):
    def is_admin(self):
        cookie = self.headers.get('Cookie', '')
        return ('admin_token=' + ADMIN_TOKEN) in cookie

    def admin_required(self, path):
        return path == '/' or path == '/stand' or path.startswith('/admin')

    def page_login(self, msg='', next_path='/'):
        body = f"""<h1>Kontori sisselogimine</h1>
<p>Kontori vaated on parooliga kaitstud. Vedukamehe sisestuslink töötab eraldi.</p>
<form method="post" action="/login">
  <input type="hidden" name="next" value="{esc(next_path)}">
  <label>Parool</label>
  <input type="password" name="password" autofocus required>
  <button type="submit">Logi sisse</button>
</form>"""
        self.send_html('Kontori sisselogimine', body, msg)

    def post_login(self):
        length = int(self.headers.get('Content-Length', '0'))
        data = parse_qs(self.rfile.read(length).decode('utf-8', errors='replace'))
        password = data.get('password', [''])[0]
        next_path = data.get('next', ['/'])[0] or '/'
        if password == ADMIN_PASSWORD:
            self.send_response(303)
            self.send_header('Location', next_path)
            self.send_header('Set-Cookie', 'admin_token=' + ADMIN_TOKEN + '; Path=/; HttpOnly; SameSite=Lax')
            self.end_headers()
        else:
            self.page_login('Vale parool', next_path)

    def logout(self):
        self.send_response(303)
        self.send_header('Location', '/login?msg=Välja%20logitud')
        self.send_header('Set-Cookie', 'admin_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
        self.end_headers()

    def send_html(self, title, body, msg=''):
        data = layout(title, body, msg)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path):
        self.send_response(303)
        self.send_header('Location', path)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        msg = qs.get('msg', [''])[0]
        if path == '/login':
            self.page_login(msg, qs.get('next', ['/'])[0])
            return
        if path == '/logout':
            self.logout()
            return
        if self.admin_required(path) and not self.is_admin():
            self.redirect('/login?next=' + quote(self.path))
            return
        if path == '/static/style.css':
            css_path = os.path.join(APP_DIR, 'static', 'style.css')
            with open(css_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/css; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/':
            self.page_index(msg)
        elif path in ('/forwarder', '/vedukamees'):
            self.page_forwarder(msg)
        elif path == '/stand':
            self.page_stand_detail(msg, qs)
        elif path == '/admin/import':
            self.page_import(msg)
        elif path == '/admin/jaotus':
            self.page_assignments(msg)
        elif path == '/admin/masinad':
            self.page_machines(msg)
        elif path == '/admin/lingid':
            self.page_links(msg)
        elif path == '/vedukamees/minu':
            self.page_my_entries(msg, qs)
        elif path == '/vedukamees/edit':
            self.page_edit_entry(msg, qs)
        elif path == '/admin/entries':
            self.page_entries(msg)
        elif path == '/admin/drivers':
            self.page_drivers(msg)
        elif path == '/admin/lisatoode':
            self.page_extra_work(msg)
        elif path == '/admin/export/lisatoode':
            self.export_extra_work()
        elif path == '/admin/export/excellent':
            self.export_excellent()
        elif path == '/admin/seed-demo':
            self.seed_demo()
        else:
            self.send_error(404, 'Not found')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/login':
            self.post_login()
            return
        if self.admin_required(parsed.path) and not self.is_admin():
            self.redirect('/login?next=' + quote(parsed.path))
            return
        if parsed.path in ('/forwarder', '/vedukamees'):
            self.post_forwarder()
        elif parsed.path == '/vedukamees/lisatoo':
            self.post_extra_work()
        elif parsed.path == '/vedukamees/edit':
            self.post_edit_entry()
        elif parsed.path == '/admin/import':
            self.post_import()
        elif parsed.path == '/admin/jaotus':
            self.post_assignment()
        elif parsed.path == '/admin/masinad/add_machine':
            self.post_add_machine()
        elif parsed.path == '/admin/masinad/add_driver':
            self.post_add_driver()
        elif parsed.path == '/admin/masinad/link':
            self.post_link_machine_driver()
        elif parsed.path == '/admin/masinad/delete_machine':
            self.post_delete_machine()
        elif parsed.path == '/admin/masinad/delete_driver':
            self.post_delete_driver()
        elif parsed.path == '/admin/masinad/unlink':
            self.post_unlink_machine_driver()
        elif parsed.path == '/admin/jaotus/kustuta':
            self.post_assignment_delete()
        else:
            self.send_error(404, 'Not found')

    def page_index(self, msg=''):
        summary = stand_summary_rows()
        extra_map = {r['stand_code']: r for r in extra_work_summary_by_stand()}
        haul_map = haul_summary_by_stand()
        total_harv = sum(float(r['harvester_total'] or 0) for r in summary)
        total_fwd = sum(float(r['forwarder_total'] or 0) for r in summary)
        total_diff = total_fwd - total_harv
        total_extra_hours = sum(float(r.get('total_hours') or 0) for r in extra_map.values())
        rows = []
        for r in summary:
            extra = extra_map.get(r['stand_code'], {})
            haul = haul_map.get(r['stand_code'], {})
            avg_haul = '' if haul.get('avg_haul_distance') is None else f"{float(haul.get('avg_haul_distance')):.0f} m"
            rows.append({
                'Klient': esc(r['client']),
                'Lank': f'<a href="/stand?code={esc(r["stand_code"])}"><strong>{esc(r["stand_code"])}</strong></a>',
                'Sortimente': r['sortiments'],
                'Harvester kokku': f"{r['harvester_total']:.3f}",
                'Vedukid kokku': f"{r['forwarder_total']:.3f}",
                'Vahe': f"{r['diff']:.3f}",
                'Lisatöö tunnid': f"{float(extra.get('total_hours') or 0):.2f}",
                'Keskm. veo pikkus': avg_haul,
                'Raskusastmed': esc(haul.get('difficulties') or ''),
                'Staatus': f"<strong>{esc(r['status'])}</strong>",
                '_status': r['status']
            })
        body = f"""<h1>Lankide koond</h1>
<div class="summary-cards">
  <div class="summary-card"><span>Kõik langid kokku - harvester</span><strong>{total_harv:.3f} tm</strong></div>
  <div class="summary-card"><span>Kõik langid kokku - vedukid</span><strong>{total_fwd:.3f} tm</strong></div>
  <div class="summary-card"><span>Üldvahe</span><strong>{total_diff:.3f} tm</strong></div>
  <div class="summary-card"><span>Lisatööd kokku</span><strong>{total_extra_hours:.2f} h</strong></div>
</div>
<p>Siin on iga lank ühe reana. Vajuta langi nime peale, siis avaneb sama langi kogu sortiment.</p>
<p><a class="button" href="/admin/seed-demo">Lisa demo PRD kogus</a></p>"""
        body += table(['Klient','Lank','Sortimente','Harvester kokku','Vedukid kokku','Vahe','Lisatöö tunnid','Keskm. veo pikkus','Raskusastmed','Staatus'], rows, lambda r: status_class(r['_status']))
        self.send_html('Lankide koond', body, msg)

    def page_stand_detail(self, msg='', qs=None):
        qs = qs or {}
        stand_code = qs.get('code', [''])[0]
        rows_raw = detail_rows_for_stand(stand_code)
        if not stand_code or not rows_raw:
            self.send_html('Lanki ei leitud', '<h1>Lanki ei leitud</h1><p><a href="/">Tagasi koondisse</a></p>', msg)
            return
        total_harv = sum(float(r['harvester_total'] or 0) for r in rows_raw)
        total_fwd = sum(float(r['forwarder_total'] or 0) for r in rows_raw)
        total_diff = total_fwd - total_harv
        rows = []
        for r in rows_raw:
            rows.append({
                'Sortiment': esc(r['assortment']),
                'Harvester': f"{r['harvester_total']:.3f}",
                'Vedukid': f"{r['forwarder_total']:.3f}",
                'Vahe': f"{r['diff']:.3f}",
                'Staatus': f"<strong>{esc(r['status'])}</strong>",
                '_status': r['status']
            })
        client = rows_raw[0]['client'] or ''
        body = f"""<h1>Lank {esc(stand_code)}</h1>
<p><a href="/">← Tagasi lankide koondisse</a></p>
<div class="summary-cards">
  <div class="summary-card"><span>Klient</span><strong>{esc(client)}</strong></div>
  <div class="summary-card"><span>Harvester kokku</span><strong>{total_harv:.3f} tm</strong></div>
  <div class="summary-card"><span>Vedukid kokku</span><strong>{total_fwd:.3f} tm</strong></div>
  <div class="summary-card"><span>Vahe</span><strong>{total_diff:.3f} tm</strong></div>
</div>
<h2>Sortimendid</h2>"""
        body += table(['Sortiment','Harvester','Vedukid','Vahe','Staatus'], rows, lambda r: status_class(r['_status']))
        haul_rows_raw = haul_rows_for_stand(stand_code)
        haul_rows = []
        for x in haul_rows_raw:
            haul_rows.append({'Aeg': esc(x['created_at']), 'Juht': esc(x['driver']), 'Masin': esc(x['machine']), 'Vahetus': esc(x['shift']), 'Kogus': f"{float(x['quantity'] or 0):.3f}", 'Veo pikkus': ((str(x['haul_distance']) + ' m') if x['haul_distance'] is not None else ''), 'Raskus': esc(x['difficulty'] or ''), 'Koormad': x['loads'] or '', 'Märkus': esc(x['note'])})
        body += '<h2>Vedude tingimused sellel langil</h2>'
        body += table(['Aeg','Juht','Masin','Vahetus','Kogus','Veo pikkus','Raskus','Koormad','Märkus'], haul_rows)
        extra_rows_raw = extra_work_rows_for_stand(stand_code)
        extra_total = sum(float(x['hours'] or 0) for x in extra_rows_raw)
        extra_rows = [{'Aeg': esc(x['created_at']), 'Juht': esc(x['driver']), 'Vahetus': esc(x['shift']), 'Lisatöö': esc(x['work_name']), 'Tunnid': f"{float(x['hours'] or 0):.2f}", 'Märkus': esc(x['note'])} for x in extra_rows_raw]
        body += f'<h2>Lisatööd sellel langil</h2><p><strong>Kokku: {extra_total:.2f} h</strong></p>'
        body += table(['Aeg','Juht','Vahetus','Lisatöö','Tunnid','Märkus'], extra_rows)
        self.send_html('Langi detail', body, msg)

    def page_forwarder(self, msg=''):
        qs = parse_qs(urlparse(self.path).query)
        selected_driver = qs.get('driver_id', [''])[0]
        conn = db()
        drivers = conn.execute('SELECT * FROM drivers WHERE active=1 ORDER BY name').fetchall()
        selected = None
        if selected_driver:
            try:
                selected = conn.execute('SELECT * FROM drivers WHERE id=? AND active=1', (int(selected_driver),)).fetchone()
            except Exception:
                selected = None
        if selected:
            stands = assigned_stands_for_driver(int(selected['id']))
        else:
            stands = []
        assortments = conn.execute("""SELECT DISTINCT a.id, a.name FROM assortments a JOIN harvester_imports h ON h.assortment_id=a.id ORDER BY a.name""").fetchall()
        if not assortments:
            assortments = conn.execute('SELECT * FROM assortments ORDER BY name').fetchall()
        conn.close()
        def opts(rows, label, with_machine=False):
            out = []
            for r in rows:
                extra = ''
                if with_machine:
                    try:
                        machine = r['machine']
                    except Exception:
                        machine = ''
                    if machine:
                        extra = ' - ' + esc(machine)
                out.append(f'<option value="{r["id"]}">{esc(r[label])}{extra}</option>')
            return ''.join(out)
        driver_pick = f'''<div class="phone-card"><h1>Vedukamehe sisestus</h1><p class="muted">Vali enda nimi. Seejärel näed ainult neid lanke, mille kontor on sulle määranud.</p>
<form method="get" action="/vedukamees"><label>Juht</label><select name="driver_id" required>{opts(drivers, 'name', True)}</select><button type="submit">Ava minu langid</button></form></div>'''
        if not selected:
            self.send_html('Vedukamehe sisestus', driver_pick, msg)
            return
        if not stands:
            body = f'''<div class="phone-card"><h1>{esc(selected['name'])}</h1><p class="warn">Sulle pole hetkel ühtegi aktiivset lanki määratud.</p><p>Võta kontoriga ühendust või lase kontoril lisada sind menüüs <strong>Tööjaotus</strong> õige langi peale.</p><p><a class="button" href="/vedukamees">Vali teine juht</a></p></div>'''
            self.send_html('Vedukamehe sisestus', body, msg)
            return
        hidden_driver = f'<input type="hidden" name="driver_id" value="{int(selected["id"])}">'
        body = f'''<div class="phone-card"><h1>Vedukamehe sisestus</h1><p><strong>{esc(selected['name'])}</strong> / {esc(selected['machine'])}</p><p class="muted">Näed ainult kontori poolt sulle määratud lanke. Harvesteri kogust ei näidata.</p>
<form method="post">{hidden_driver}<label>Lank</label><select name="stand_id" required>{opts(stands, 'code')}</select>
<label>Sortiment</label><select name="assortment_id" required>{opts(assortments, 'name')}</select>
<label>Vahetus</label><select name="shift"><option value="päev">Päev</option><option value="öö">Öö</option><option value="muu">Muu</option></select>
<label>Veetud kogus, tm</label><input name="quantity" inputmode="decimal" placeholder="nt 42,5" required>
<label>Koormate arv</label><input name="loads" inputmode="numeric" placeholder="nt 5">
<label>Veo pikkus, m</label><input name="haul_distance" inputmode="decimal" placeholder="nt 350">
<label>Raskusaste</label><select name="difficulty"><option value="tavapärane">Tavapärane vedu</option><option value="pehme">Pehme pinnas</option><option value="mägine">Mägine/raske maastik</option><option value="väga raske">Väga raske</option></select>
<label>Märkus</label><textarea name="note" rows="2" placeholder="vajadusel"></textarea><button type="submit">Saada veokogus</button></form>
<hr><h2>Lisatöö samal langil</h2><p class="muted">Lisa tunnid tellijale edastamiseks ja masina tegevuse ülevaateks.</p>
<form method="post" action="/vedukamees/lisatoo">{hidden_driver}<label>Lank</label><select name="stand_id" required>{opts(stands, 'code')}</select>
<label>Vahetus</label><select name="shift"><option value="päev">Päev</option><option value="öö">Öö</option><option value="muu">Muu</option></select>
<label>Lisatöö nimetus</label><input name="work_name" placeholder="nt kolimine, tee parandus, oksavaal, ootamine" required>
<label>Tunnid</label><input name="hours" inputmode="decimal" placeholder="nt 1,5" required>
<label>Märkus</label><textarea name="note" rows="2" placeholder="vajadusel"></textarea><button type="submit">Saada lisatöö</button></form>
<hr><h2>Paranda enda kannet</h2><p class="muted">Parandus jääb logisse.</p><p><a class="button" href="/vedukamees/minu?driver_id={int(selected['id'])}">Näita minu kandeid</a></p><p><a href="/vedukamees">Vali teine juht</a></p></div>'''
        self.send_html('Vedukamehe sisestus', body, msg)

    def post_forwarder(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db()
        cur = conn.cursor()
        try:
            stand_id = int(form['stand_id']); assortment_id = int(form['assortment_id']); driver_id = int(form['driver_id'])
            if not is_driver_allowed_for_stand(cur, driver_id, stand_id):
                raise ValueError('See lank ei ole sellele juhile määratud')
            quantity = float(form['quantity'].replace(',', '.'))
            loads = int(form['loads']) if form.get('loads') else None
            haul_distance = float(form['haul_distance'].replace(',', '.')) if form.get('haul_distance') else None
            difficulty = form.get('difficulty', 'tavapärane')
            shift = form.get('shift', '')
            note = form.get('note', '')
            driver = cur.execute('SELECT machine FROM drivers WHERE id=?', (driver_id,)).fetchone()
            status, overage, remaining_before = classify_entry(cur, stand_id, assortment_id, quantity)
            cur.execute('''INSERT INTO forwarder_entries(stand_id, assortment_id, driver_id, entry_date, shift, machine, quantity, loads, haul_distance, difficulty, note, created_at, status, overage_amount, remaining_before)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (stand_id, assortment_id, driver_id, date.today().isoformat(), shift, driver['machine'] if driver else '', quantity, loads, haul_distance, difficulty, note, now(), status, overage, remaining_before))
            entry_id = cur.lastrowid
            cur.execute('INSERT INTO audit_log(event_type, entity, entity_id, message, created_at) VALUES (?, ?, ?, ?, ?)', ('create', 'forwarder_entries', entry_id, f'Vedu sisestatud. Staatus: {status}. Ületus: {overage}', now()))
            conn.commit()
            self.redirect('/vedukamees?msg=Andmed%20salvestatud')
        except Exception as e:
            conn.rollback()
            self.redirect('/vedukamees?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def post_extra_work(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            stand_id = int(form['stand_id']); driver_id = int(form['driver_id'])
            if not is_driver_allowed_for_stand(cur, driver_id, stand_id):
                raise ValueError('See lank ei ole sellele juhile määratud')
            hours = float(form['hours'].replace(',', '.'))
            if hours <= 0:
                raise ValueError('Tunnid peavad olema suuremad kui 0')
            work_name = (form.get('work_name') or '').strip()
            if not work_name:
                raise ValueError('Lisatöö nimetus puudub')
            shift = form.get('shift', '')
            note = form.get('note', '')
            driver = cur.execute('SELECT machine FROM drivers WHERE id=?', (driver_id,)).fetchone()
            cur.execute('''INSERT INTO extra_work_entries(stand_id, driver_id, entry_date, shift, machine, work_name, hours, note, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (stand_id, driver_id, date.today().isoformat(), shift, driver['machine'] if driver else '', work_name, hours, note, now()))
            entry_id = cur.lastrowid
            cur.execute('INSERT INTO audit_log(event_type, entity, entity_id, message, created_at) VALUES (?, ?, ?, ?, ?)',
                        ('create', 'extra_work_entries', entry_id, f'Lisatöö sisestatud: {work_name}, {hours} h', now()))
            conn.commit()
            self.redirect('/vedukamees?msg=Lisatöö%20salvestatud')
        except Exception as e:
            conn.rollback()
            self.redirect('/vedukamees?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def page_extra_work(self, msg=''):
        conn = db()
        summary = conn.execute('''SELECT s.code stand_code, s.client, d.name driver, e.work_name, SUM(e.hours) total_hours, COUNT(*) entries
                                  FROM extra_work_entries e
                                  JOIN stands s ON s.id=e.stand_id
                                  JOIN drivers d ON d.id=e.driver_id
                                  GROUP BY s.code, s.client, d.name, e.work_name
                                  ORDER BY s.code, d.name, e.work_name''').fetchall()
        details = conn.execute('''SELECT e.*, s.code stand_code, s.client, d.name driver
                                  FROM extra_work_entries e
                                  JOIN stands s ON s.id=e.stand_id
                                  JOIN drivers d ON d.id=e.driver_id
                                  ORDER BY e.created_at DESC LIMIT 300''').fetchall()
        total_hours = conn.execute('SELECT COALESCE(SUM(hours),0) total FROM extra_work_entries').fetchone()['total']
        conn.close()
        rows = [{'Lank': esc(r['stand_code']), 'Klient': esc(r['client']), 'Juht': esc(r['driver']), 'Lisatöö': esc(r['work_name']), 'Tunnid kokku': f"{float(r['total_hours'] or 0):.2f}", 'Kandeid': r['entries']} for r in summary]
        detail_rows = [{'Aeg': esc(r['created_at']), 'Lank': esc(r['stand_code']), 'Juht': esc(r['driver']), 'Masin': esc(r['machine']), 'Vahetus': esc(r['shift']), 'Lisatöö': esc(r['work_name']), 'Tunnid': f"{float(r['hours'] or 0):.2f}", 'Märkus': esc(r['note'])} for r in details]
        body = f'<h1>Lisatööde raport</h1><div class="summary-cards"><div class="summary-card"><span>Lisatööd kokku</span><strong>{float(total_hours or 0):.2f} h</strong></div></div>'
        body += '<p>Siit saab tellijale edasi saata langi, töö nimetuse ja tunnid.</p><p><a class="button" href="/admin/export/lisatoode">Laadi lisatööd CSV</a></p>'
        body += '<h2>Koond langi, juhi ja töö kaupa</h2>' + table(['Lank','Klient','Juht','Lisatöö','Tunnid kokku','Kandeid'], rows)
        body += '<h2>Kõik lisatöö kanded</h2>' + table(['Aeg','Lank','Juht','Masin','Vahetus','Lisatöö','Tunnid','Märkus'], detail_rows)
        self.send_html('Lisatööde raport', body, msg)

    def export_extra_work(self):
        conn = db()
        rows = conn.execute('''SELECT s.client, s.code stand_code, d.name driver, e.machine, e.entry_date, e.shift, e.work_name, e.hours, e.note, e.created_at
                               FROM extra_work_entries e
                               JOIN stands s ON s.id=e.stand_id
                               JOIN drivers d ON d.id=e.driver_id
                               ORDER BY s.code, e.created_at''').fetchall()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Klient','Lank','Juht','Masin','Kuupäev','Vahetus','Lisatöö','Tunnid','Märkus','Sisestatud'])
        for r in rows:
            writer.writerow([r['client'], r['stand_code'], r['driver'], r['machine'], r['entry_date'], r['shift'], r['work_name'], f"{float(r['hours'] or 0):.2f}", r['note'], r['created_at']])
        data = output.getvalue().encode('utf-8-sig')
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="lisatoode_koond.csv"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def page_my_entries(self, msg='', qs=None):
        qs = qs or {}
        driver_id = qs.get('driver_id', [''])[0]
        conn = db()
        drivers = conn.execute('SELECT * FROM drivers WHERE active=1 ORDER BY name').fetchall()
        rows_raw = []
        driver_name = ''
        if driver_id:
            d = conn.execute('SELECT name FROM drivers WHERE id=?', (driver_id,)).fetchone()
            driver_name = d['name'] if d else ''
            rows_raw = conn.execute('''SELECT fe.*, s.code stand_code, a.name assortment
                                       FROM forwarder_entries fe
                                       JOIN stands s ON s.id=fe.stand_id
                                       JOIN assortments a ON a.id=fe.assortment_id
                                       WHERE fe.driver_id=?
                                       ORDER BY fe.created_at DESC LIMIT 20''', (driver_id,)).fetchall()
        conn.close()
        opts = ''.join(f'<option value="{r["id"]}" {"selected" if str(r["id"])==str(driver_id) else ""}>{esc(r["name"])}{(" - " + esc(r["machine"])) if r["machine"] else ""}</option>' for r in drivers)
        body = f'''<div class="phone-card"><h1>Minu kanded</h1><form method="get" action="/vedukamees/minu"><label>Juht</label><select name="driver_id" required>{opts}</select><button type="submit">Näita</button></form>'''
        if driver_id:
            body += f'<h2>{esc(driver_name)} viimased kanded</h2>'
            if not rows_raw:
                body += '<p>Kandeid ei leitud.</p>'
            else:
                body += '<table><thead><tr><th>Aeg</th><th>Lank</th><th>Sortiment</th><th>Kogus</th><th>Veo pikkus</th><th>Raskus</th><th>Staatus</th><th></th></tr></thead><tbody>'
                for r in rows_raw:
                    haul_txt = (str(r["haul_distance"]) + " m") if r["haul_distance"] is not None else ""
                    diff_txt = esc(r["difficulty"] or "")
                    body += f'<tr class="{esc(status_class(r["status"]))}"><td>{esc(r["created_at"])}</td><td>{esc(r["stand_code"])}</td><td>{esc(r["assortment"])}</td><td>{float(r["quantity"]):.3f}</td><td>{haul_txt}</td><td>{diff_txt}</td><td>{esc(r["status"])}</td><td><a class="button small" href="/vedukamees/edit?id={r["id"]}&driver_id={driver_id}">Paranda</a></td></tr>'
                body += '</tbody></table>'
        body += '<p><a href="/vedukamees">Tagasi sisestusse</a></p></div>'
        self.send_html('Minu kanded', body, msg)

    def page_edit_entry(self, msg='', qs=None):
        qs = qs or {}
        entry_id = qs.get('id', [''])[0]
        conn = db()
        r = conn.execute('''SELECT fe.*, s.code stand_code, a.name assortment, d.name driver
                            FROM forwarder_entries fe
                            JOIN stands s ON s.id=fe.stand_id
                            JOIN assortments a ON a.id=fe.assortment_id
                            JOIN drivers d ON d.id=fe.driver_id
                            WHERE fe.id=?''', (entry_id,)).fetchone()
        conn.close()
        if not r:
            self.send_html('Kannet ei leitud', '<h1>Kannet ei leitud</h1><p><a href="/vedukamees">Tagasi</a></p>', msg)
            return
        shift_day = 'selected' if r['shift']=='päev' else ''
        shift_night = 'selected' if r['shift']=='öö' else ''
        shift_other = 'selected' if r['shift']=='muu' else ''
        loads_value = '' if r['loads'] is None else esc(r['loads'])
        haul_value = '' if r['haul_distance'] is None else esc(r['haul_distance'])
        diff = r['difficulty'] or 'tavapärane'
        diff_tava = 'selected' if diff=='tavapärane' else ''
        diff_pehme = 'selected' if diff=='pehme' else ''
        diff_magi = 'selected' if diff=='mägine' else ''
        diff_vraske = 'selected' if diff=='väga raske' else ''
        body = f'''<div class="phone-card"><h1>Paranda kanne</h1>
<p><strong>{esc(r['driver'])}</strong><br>{esc(r['stand_code'])} / {esc(r['assortment'])}<br>Sisestatud: {esc(r['created_at'])}</p>
<form method="post" action="/vedukamees/edit">
<input type="hidden" name="id" value="{r['id']}">
<label>Vahetus</label><select name="shift"><option value="päev" {shift_day}>Päev</option><option value="öö" {shift_night}>Öö</option><option value="muu" {shift_other}>Muu</option></select>
<label>Õige kogus, tm</label><input name="quantity" inputmode="decimal" value="{float(r['quantity']):.3f}" required>
<label>Koormate arv</label><input name="loads" inputmode="numeric" value="{loads_value}">
<label>Veo pikkus, m</label><input name="haul_distance" inputmode="decimal" value="{haul_value}">
<label>Raskusaste</label><select name="difficulty"><option value="tavapärane" {diff_tava}>Tavapärane vedu</option><option value="pehme" {diff_pehme}>Pehme pinnas</option><option value="mägine" {diff_magi}>Mägine/raske maastik</option><option value="väga raske" {diff_vraske}>Väga raske</option></select>
<label>Märkus / paranduse põhjus</label><textarea name="note" rows="3">{esc(r['note'])}</textarea>
<button type="submit">Salvesta parandus</button></form>
<p><a href="/vedukamees/minu?driver_id={r['driver_id']}">Tagasi minu kannetesse</a></p></div>'''
        self.send_html('Paranda kanne', body, msg)

    def post_edit_entry(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            entry_id = int(form['id'])
            old = cur.execute('SELECT * FROM forwarder_entries WHERE id=?', (entry_id,)).fetchone()
            if not old:
                raise ValueError('Kannet ei leitud')
            quantity = float(form['quantity'].replace(',', '.'))
            loads = int(form['loads']) if form.get('loads') else None
            haul_distance = float(form['haul_distance'].replace(',', '.')) if form.get('haul_distance') else None
            difficulty = form.get('difficulty', 'tavapärane')
            shift = form.get('shift', '')
            note = form.get('note', '')
            cur.execute('''UPDATE forwarder_entries
                           SET quantity=?, loads=?, haul_distance=?, difficulty=?, shift=?, note=?, updated_at=?, correction_count=COALESCE(correction_count,0)+1
                           WHERE id=?''', (quantity, loads, haul_distance, difficulty, shift, note, now(), entry_id))
            cur.execute('INSERT INTO audit_log(event_type, entity, entity_id, message, created_at) VALUES (?, ?, ?, ?, ?)',
                        ('edit', 'forwarder_entries', entry_id, f'Parandus: kogus {old["quantity"]} -> {quantity}, koormad {old["loads"]} -> {loads}, veo pikkus {old["haul_distance"]} -> {haul_distance}, raskus {old["difficulty"]} -> {difficulty}', now()))
            recalc_entries(cur, old['stand_id'], old['assortment_id'])
            conn.commit()
            self.redirect('/vedukamees/minu?driver_id=' + str(old['driver_id']) + '&msg=Parandus%20salvestatud')
        except Exception as e:
            conn.rollback()
            self.redirect('/vedukamees?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()


    def page_links(self, msg=''):
        conn = db()
        drivers = conn.execute("SELECT id, name FROM drivers WHERE active=1 ORDER BY name").fetchall()
        conn.close()
        host = self.headers.get('Host') or f'127.0.0.1:{PORT}'
        base = 'http://' + host
        rows = []
        for d in drivers:
            link = f"{base}/vedukamees?driver_id={int(d['id'])}"
            rows.append({
                'Juht': d['name'],
                'Link vedukamehele': f'<a href="{esc(link)}">{esc(link)}</a>',
                'Kopeeri': f'<input style="width:100%" readonly value="{esc(link)}">'
            })
        office = f'{base}/'
        driver_general = f'{base}/vedukamees'
        body = '<h1>Lingid töötajatele</h1>'
        body += f'<div class="panel"><h2>Kontori link</h2><p>Kontor kasutab seda aadressi samas võrgus:</p><p><input style="width:100%" readonly value="{esc(office)}"></p></div>'
        body += f'<div class="panel"><h2>Üldine vedukamehe link</h2><p>Selle lingiga valib vedukamees ise oma nime:</p><p><input style="width:100%" readonly value="{esc(driver_general)}"></p></div>'
        body += '<h2>Juhipõhised lingid</h2><p>Saada igale vedukamehele tema rida. Link avab kohe tema vaate ja näitab ainult talle tööjaotuses määratud lanke.</p>'
        body += table(['Juht','Link vedukamehele','Kopeeri'], rows)
        body += '<div class="panel"><h2>Oluline</h2><p>Need lingid töötavad telefonis siis, kui telefon ja kontori arvuti/server on samas WiFi/võrgus või kui programm on pandud pilveserverisse. Kui telefon on metsas mobiilse internetiga, on vaja server avalikku internetti panna või kasutada VPN-i.</p></div>'
        self.send_html('Lingid', body, msg)

    def page_machines(self, msg=''):
        conn = db()
        machines = conn.execute('SELECT * FROM machines WHERE active=1 ORDER BY machine_type, owner_type, name').fetchall()
        drivers = conn.execute('SELECT * FROM drivers WHERE active=1 ORDER BY name').fetchall()
        links = machine_driver_links()
        conn.close()
        def machine_opts():
            return ''.join([f'<option value="{int(m["id"])}">{esc(m["name"])} ({esc(m["machine_type"])}{", " + esc(m["serial_no"]) if m["serial_no"] else ""})</option>' for m in machines])
        def driver_opts():
            return ''.join([f'<option value="{int(d["id"])}">{esc(d["name"])}{(" - " + esc(d["company"])) if d["company"] else ""}</option>' for d in drivers])
        machine_rows=[]
        for m in machines:
            machine_rows.append({
                'Masin': esc(m['name']),
                'Tüüp': 'Harvester' if m['machine_type']=='harvester' else 'Veduk',
                'Seeria nr': esc(m['serial_no']),
                'Omanik': 'Alltöövõtt' if m['owner_type']=='alltöövõtt' else 'Oma',
                'Alltöövõtja': esc(m['contractor']),
                'Tegevus': f'''<form method="post" action="/admin/masinad/delete_machine" onsubmit="return confirm('Eemaldan masina valikutest? Varasemad kanded jäävad alles.')"><input type="hidden" name="id" value="{int(m['id'])}"><button type="submit">Eemalda</button></form>'''
            })
        driver_rows=[]
        for d in drivers:
            driver_rows.append({
                'Juht': esc(d['name']),
                'Tüüp': 'Alltöövõtt' if (d['driver_type'] or 'oma')=='alltöövõtt' else 'Oma',
                'Ettevõte': esc(d['company']),
                'Vaikimisi masin': esc(d['machine']),
                'Tegevus': f'''<form method="post" action="/admin/masinad/delete_driver" onsubmit="return confirm('Eemaldan juhi valikutest? Varasemad kanded jäävad alles.')"><input type="hidden" name="id" value="{int(d['id'])}"><button type="submit">Eemalda</button></form>'''
            })
        link_rows=[]
        for l in links:
            link_rows.append({
                'Masin': esc(l['machine_name']),
                'Tüüp': esc(l['machine_type']),
                'Seeria nr': esc(l['serial_no']),
                'Juht': esc(l['driver_name']),
                'Ettevõte': esc(l['company'] or l['contractor']),
                'Omanik': 'Alltöövõtt' if l['owner_type']=='alltöövõtt' else 'Oma',
                'Tegevus': f'''<form method="post" action="/admin/masinad/unlink" onsubmit="return confirm('Eemaldan juhi ja masina seose?')"><input type="hidden" name="id" value="{int(l['id'])}"><button type="submit">Eemalda seos</button></form>'''
            })
        body = '''<h1>Masinad ja juhid</h1>
<p>Siin saab kontor hoida eraldi nimekirja oma masinatest, alltöövõtu masinatest ja juhtidest. Tööjaotuses saab pärast valida, milline juht ja masin konkreetsele langile lubatakse.</p><p class="muted">Eemaldamine muudab juhi/masina mitteaktiivseks: vanad kanded ja ajalugu jäävad alles, aga valikutesse neid enam ei kuvata.</p>
<div class="grid-2">
<form method="post" action="/admin/masinad/add_machine" class="panel"><h2>Lisa masin</h2>
<label>Masina nimi</label><input name="name" placeholder="nt Ponsse Buffalo / Veduk 8" required>
<label>Tüüp</label><select name="machine_type"><option value="veduk">Veduk</option><option value="harvester">Harvester</option></select>
<label>Seeria nr</label><input name="serial_no" placeholder="nt seerianumber">
<label>Omanik</label><select name="owner_type"><option value="oma">Oma masin</option><option value="alltöövõtt">Alltöövõtt</option></select>
<label>Alltöövõtja nimi</label><input name="contractor" placeholder="kui on alltöövõtt">
<button type="submit">Lisa masin</button></form>
<form method="post" action="/admin/masinad/add_driver" class="panel"><h2>Lisa juht</h2>
<label>Juhi nimi</label><input name="name" placeholder="nt Mart Mets" required>
<label>Tüüp</label><select name="driver_type"><option value="oma">Oma töötaja</option><option value="alltöövõtt">Alltöövõtt</option></select>
<label>Ettevõte</label><input name="company" placeholder="kui alltöövõtja või firma nimi">
<button type="submit">Lisa juht</button></form>
</div>
<form method="post" action="/admin/masinad/link" class="panel"><h2>Seo juht masinaga</h2>
<label>Masin</label><select name="machine_id" required>'''+machine_opts()+'''</select>
<label>Juht</label><select name="driver_id" required>'''+driver_opts()+'''</select>
<button type="submit">Lisa seos</button></form>
<h2>Masinate register</h2>'''
        body += table(['Masin','Tüüp','Seeria nr','Omanik','Alltöövõtja','Tegevus'], machine_rows)
        body += '<h2>Juhtide register</h2>' + table(['Juht','Tüüp','Ettevõte','Vaikimisi masin','Tegevus'], driver_rows)
        body += '<h2>Juhtide seosed masinatega</h2>' + table(['Masin','Tüüp','Seeria nr','Juht','Ettevõte','Omanik','Tegevus'], link_rows)
        self.send_html('Masinad ja juhid', body, msg)

    def post_add_machine(self):
        length = int(self.headers.get('Content-Length', 0)); data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            name=(form.get('name') or '').strip(); serial=(form.get('serial_no') or '').strip(); mtype=form.get('machine_type') or 'veduk'; owner=form.get('owner_type') or 'oma'; contractor=(form.get('contractor') or '').strip()
            if not name: raise ValueError('Masina nimi puudub')
            cur.execute('INSERT OR IGNORE INTO machines(name, machine_type, serial_no, owner_type, contractor, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)', (name, mtype, serial, owner, contractor, now()))
            cur.execute('INSERT INTO audit_log(event_type, entity, message, created_at) VALUES (?, ?, ?, ?)', ('create','machines',f'Masin lisatud: {name}',now()))
            conn.commit(); self.redirect('/admin/masinad?msg=Masin%20lisatud')
        except Exception as e:
            conn.rollback(); self.redirect('/admin/masinad?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def post_add_driver(self):
        length = int(self.headers.get('Content-Length', 0)); data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            name=(form.get('name') or '').strip(); dtype=form.get('driver_type') or 'oma'; company=(form.get('company') or '').strip()
            if not name: raise ValueError('Juhi nimi puudub')
            cur.execute('INSERT OR IGNORE INTO drivers(name, machine, active, company, driver_type) VALUES (?, ?, 1, ?, ?)', (name, '', company, dtype))
            cur.execute('UPDATE drivers SET company=?, driver_type=? WHERE name=?', (company, dtype, name))
            cur.execute('INSERT INTO audit_log(event_type, entity, message, created_at) VALUES (?, ?, ?, ?)', ('create','drivers',f'Juht lisatud/uuendatud: {name}',now()))
            conn.commit(); self.redirect('/admin/masinad?msg=Juht%20lisatud')
        except Exception as e:
            conn.rollback(); self.redirect('/admin/masinad?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def post_link_machine_driver(self):
        length = int(self.headers.get('Content-Length', 0)); data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            machine_id=int(form['machine_id']); driver_id=int(form['driver_id'])
            m=cur.execute('SELECT name FROM machines WHERE id=?', (machine_id,)).fetchone()
            if not m: raise ValueError('Masinat ei leitud')
            cur.execute('INSERT OR IGNORE INTO machine_drivers(machine_id, driver_id, active, created_at) VALUES (?, ?, 1, ?)', (machine_id, driver_id, now()))
            cur.execute('UPDATE drivers SET machine=? WHERE id=? AND (machine IS NULL OR machine="")', (m['name'], driver_id))
            cur.execute('INSERT INTO audit_log(event_type, entity, message, created_at) VALUES (?, ?, ?, ?)', ('link','machine_drivers',f'Juht {driver_id} seotud masinaga {machine_id}',now()))
            conn.commit(); self.redirect('/admin/masinad?msg=Seos%20lisatud')
        except Exception as e:
            conn.rollback(); self.redirect('/admin/masinad?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def page_assignments(self, msg=''):
        conn = db()
        stands = conn.execute("SELECT * FROM stands WHERE status='aktiivne' ORDER BY code").fetchall()
        drivers = conn.execute('SELECT * FROM drivers WHERE active=1 ORDER BY name').fetchall()
        machines = conn.execute("SELECT * FROM machines WHERE active=1 AND machine_type='veduk' ORDER BY name").fetchall()
        existing = assignments_for_admin()
        conn.close()
        def opt_stands():
            return ''.join([f'<option value="{r["id"]}">{esc(r["code"])} {("- " + esc(r["client"])) if r["client"] else ""}</option>' for r in stands])
        def opt_drivers():
            return ''.join([f'<option value="{r["id"]}">{esc(r["name"])} {("- " + esc(r["company"])) if r["company"] else ""}</option>' for r in drivers])
        def opt_machines():
            return ''.join([f'<option value="{esc(r["name"])}">{esc(r["name"])} {("- " + esc(r["serial_no"])) if r["serial_no"] else ""} {("(" + esc(r["contractor"]) + ")") if r["contractor"] else ""}</option>' for r in machines])
        rows = []
        for r in existing:
            rows.append({
                'Lank': esc(r['stand_code']),
                'Klient': esc(r['client']),
                'Juht': esc(r['driver']),
                'Masin': esc(r['machine'] or r['driver_machine']),
                'Märkus': esc(r['note']),
                'Tegevus': f'''<form method="post" action="/admin/jaotus/kustuta" onsubmit="return confirm('Kustutan tööjaotuse?')"><input type="hidden" name="id" value="{int(r['id'])}"><button type="submit">Eemalda</button></form>'''
            })
        body = f'''<h1>Langi tööjaotus</h1>
<p>Siin määrab kontor, millised vedukamehed ja masinad tohivad konkreetse langi peal sisestada. Vedukamees näeb pärast enda nime valimist ainult talle määratud lanke.</p>
<form method="post" class="panel"><label>Lank</label><select name="stand_id" required>{opt_stands()}</select>
<label>Vedukamees / masin</label><select name="driver_id" required>{opt_drivers()}</select>
<label>Masin</label><select name="machine" required>{opt_machines()}</select>
<label>Märkus</label><input name="note" placeholder="nt veab põhja ladu, ainult päevane vahetus">
<button type="submit">Lisa tööjaotus</button></form>
<h2>Aktiivsed tööjaotused</h2>'''
        body += table(['Lank','Klient','Juht','Masin','Märkus','Tegevus'], rows)
        self.send_html('Tööjaotus', body, msg)

    def post_assignment(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            stand_id = int(form['stand_id']); driver_id = int(form['driver_id'])
            machine = (form.get('machine') or '').strip()
            if not machine:
                row = cur.execute('SELECT machine FROM drivers WHERE id=?', (driver_id,)).fetchone()
                machine = row['machine'] if row else ''
            note = (form.get('note') or '').strip()
            cur.execute("""INSERT INTO stand_assignments(stand_id, driver_id, machine, note, active, created_at)
                           VALUES (?, ?, ?, ?, 1, ?)
                           ON CONFLICT(stand_id, driver_id) DO UPDATE SET machine=excluded.machine, note=excluded.note, active=1""",
                        (stand_id, driver_id, machine, note, now()))
            cur.execute('INSERT INTO audit_log(event_type, entity, message, created_at) VALUES (?, ?, ?, ?)', ('assign', 'stand_assignments', f'Langi tööjaotus lisatud stand={stand_id}, driver={driver_id}', now()))
            conn.commit()
            self.redirect('/admin/jaotus?msg=Tööjaotus%20lisatud')
        except Exception as e:
            conn.rollback(); self.redirect('/admin/jaotus?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def post_assignment_delete(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8')
        form = {k: v[0] for k, v in parse_qs(data).items()}
        conn = db(); cur = conn.cursor()
        try:
            aid = int(form['id'])
            cur.execute('UPDATE stand_assignments SET active=0 WHERE id=?', (aid,))
            cur.execute('INSERT INTO audit_log(event_type, entity, entity_id, message, created_at) VALUES (?, ?, ?, ?, ?)', ('delete', 'stand_assignments', aid, 'Tööjaotus eemaldatud', now()))
            conn.commit()
            self.redirect('/admin/jaotus?msg=Tööjaotus%20eemaldatud')
        except Exception as e:
            conn.rollback(); self.redirect('/admin/jaotus?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def page_import(self, msg=''):
        body = '''<h1>Harvesteri PRD/CSV import</h1><p>Import oskab nüüd lugeda kahte varianti: <strong>Ponsse/StanForD PRD</strong> faili ja lihtsat CSV faili.</p><p>PRD puhul võtab süsteem langi, masina, operaatori ja kogused failist. Vedukamehed neid koguseid ei näe.</p>
<form method="post" enctype="multipart/form-data" class="panel"><input type="file" name="prd_file" required><button type="submit">Impordi</button></form>
<h2>CSV varuvariant</h2><pre>lank;sortiment;kogus;kuupaev;vahetus;masin;operaator
L-124;Palk;100;2026-07-05;päev;Harvester 1;Ants</pre>'''
        self.send_html('PRD import', body, msg)

    def post_import(self):
        content_type = self.headers.get('Content-Type', '')
        length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(length)
        filename, file_bytes = parse_multipart_file(content_type, body_bytes, 'prd_file')
        if not file_bytes:
            self.redirect('/admin/import?msg=Fail%20puudub')
            return
        filename = filename or 'upload.prd'
        imported = 0
        errors = []
        conn = db(); cur = conn.cursor()
        try:
            if filename.lower().endswith('.prd') or file_bytes[:80].decode('latin-1', errors='ignore').find('PRD') >= 0:
                parsed_rows = parse_ponsse_prd(file_bytes, filename)
                for row in parsed_rows:
                    stand_id = get_or_create(cur, 'stands', 'code', row.get('lank'))
                    if row.get('client'):
                        cur.execute('UPDATE stands SET client=COALESCE(NULLIF(client, ""), ?) WHERE id=?', (row.get('client'), stand_id))
                    assortment_id = get_or_create(cur, 'assortments', 'name', row.get('sortiment'))
                    cur.execute('''INSERT INTO harvester_imports(stand_id, assortment_id, work_date, shift, machine, operator, quantity, source_file, imported_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                (stand_id, assortment_id, row.get('kuupaev'), row.get('vahetus'), row.get('masin'), row.get('operaator'), float(row.get('kogus')), filename + ' / ' + row.get('source_note', 'PRD'), now()))
                    imported += 1
            else:
                raw = file_bytes.decode('utf-8-sig', errors='replace')
                try:
                    dialect = csv.Sniffer().sniff(raw[:2048], delimiters=';,\t,')
                except Exception:
                    dialect = csv.excel
                    dialect.delimiter = ';'
                reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
                headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}
                aliases = {'lank':['lank','stand','objekt','langi_kood'], 'sortiment':['sortiment','assortment','toode'], 'kogus':['kogus','quantity','maht','tm','m3'], 'kuupaev':['kuupaev','kuupäev','date','paev'], 'vahetus':['vahetus','shift'], 'masin':['masin','machine','harvester'], 'operaator':['operaator','operator','juht']}
                def val(row, key):
                    for a in aliases[key]:
                        if a in headers:
                            return (row.get(headers[a]) or '').strip()
                    return ''
                for i, row in enumerate(reader, start=2):
                    try:
                        stand_id = get_or_create(cur, 'stands', 'code', val(row, 'lank'))
                        assortment_id = get_or_create(cur, 'assortments', 'name', val(row, 'sortiment'))
                        quantity = float(val(row, 'kogus').replace(',', '.'))
                        cur.execute('''INSERT INTO harvester_imports(stand_id, assortment_id, work_date, shift, machine, operator, quantity, source_file, imported_at)
                                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                    (stand_id, assortment_id, val(row, 'kuupaev'), val(row, 'vahetus'), val(row, 'masin'), val(row, 'operaator'), quantity, filename, now()))
                        imported += 1
                    except Exception as e:
                        errors.append(f'Rida {i}: {e}')
            cur.execute('INSERT INTO audit_log(event_type, entity, message, created_at) VALUES (?, ?, ?, ?)', ('import', 'harvester_imports', f'Imporditud {imported} rida failist {filename}', now()))
            conn.commit()
            msg = f'Imporditud {imported} rida'
            if errors:
                msg += '; vigasid: ' + ' | '.join(errors[:3])
            self.redirect('/?msg=' + msg.replace(' ', '%20'))
        except Exception as e:
            conn.rollback(); self.redirect('/admin/import?msg=' + str(e).replace(' ', '%20'))
        finally:
            conn.close()

    def page_entries(self, msg=''):
        conn = db()
        rows_raw = conn.execute('''SELECT fe.*, s.code stand_code, a.name assortment, d.name driver FROM forwarder_entries fe JOIN stands s ON s.id=fe.stand_id JOIN assortments a ON a.id=fe.assortment_id JOIN drivers d ON d.id=fe.driver_id ORDER BY fe.created_at DESC LIMIT 300''').fetchall()
        conn.close()
        rows = []
        for r in rows_raw:
            rows.append({'Aeg':esc(r['created_at']),'Juht':esc(r['driver']),'Lank':esc(r['stand_code']),'Sortiment':esc(r['assortment']),'Kogus':f"{r['quantity']:.3f}",'Veo pikkus':((str(r['haul_distance']) + ' m') if r['haul_distance'] is not None else ''),'Raskus':esc(r['difficulty'] or ''),'Jääk enne':f"{(r['remaining_before'] or 0):.3f}",'Ületus':f"{(r['overage_amount'] or 0):.3f}",'Staatus':f"<strong>{esc(r['status'])}</strong>",'Märkus':esc(r['note']),'_status':r['status']})
        body = '<h1>Vedukameeste kanded</h1>' + table(['Aeg','Juht','Lank','Sortiment','Kogus','Veo pikkus','Raskus','Jääk enne','Ületus','Staatus','Märkus'], rows, lambda r: status_class(r['_status']))
        self.send_html('Kanded', body, msg)

    def page_drivers(self, msg=''):
        conn = db()
        summary = conn.execute('''SELECT d.name driver, COUNT(*) entries, SUM(fe.quantity) total_qty, SUM(CASE WHEN fe.status='ületus' THEN 1 ELSE 0 END) overage_entries, SUM(fe.overage_amount) overage_total FROM forwarder_entries fe JOIN drivers d ON d.id=fe.driver_id GROUP BY d.id, d.name ORDER BY overage_total DESC, total_qty DESC''').fetchall()
        details = conn.execute('''SELECT d.name driver, s.code stand_code, a.name assortment, fe.quantity, fe.remaining_before, fe.overage_amount, fe.created_at, fe.status FROM forwarder_entries fe JOIN drivers d ON d.id=fe.driver_id JOIN stands s ON s.id=fe.stand_id JOIN assortments a ON a.id=fe.assortment_id WHERE fe.status IN ('ületus', 'PRD puudub') ORDER BY fe.created_at DESC''').fetchall()
        conn.close()
        rows = [{'Juht':esc(r['driver']),'Kandeid':r['entries'],'Kogus kokku':f"{(r['total_qty'] or 0):.3f}",'Ületusega kandeid':r['overage_entries'],'Ületus kokku':f"{(r['overage_total'] or 0):.3f}"} for r in summary]
        drows = [{'Aeg':esc(r['created_at']),'Juht':esc(r['driver']),'Lank':esc(r['stand_code']),'Sortiment':esc(r['assortment']),'Sisestus':f"{r['quantity']:.3f}",'Jääk enne':f"{(r['remaining_before'] or 0):.3f}",'Ületus':f"{(r['overage_amount'] or 0):.3f}",'Staatus':esc(r['status']),'_status':r['status']} for r in details]
        body = '<h1>Juhi põhine kõrvalekalde raport</h1>' + table(['Juht','Kandeid','Kogus kokku','Ületusega kandeid','Ületus kokku'], rows)
        body += '<h2>Probleemsed kanded</h2>' + table(['Aeg','Juht','Lank','Sortiment','Sisestus','Jääk enne','Ületus','Staatus'], drows, lambda r: status_class(r['_status']))
        self.send_html('Juhi raport', body, msg)

    def export_excellent(self):
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Klient','Lank','Sortiment','Harvester_tm','Forwarder_tm','Vahe_tm','Staatus','Kinnitatud_kogus_tm','Veo_pikkus_keskm_m','Raskusastmed'])
        for r in overview_rows():
            confirmed = min(float(r['harvester_total']), float(r['forwarder_total'])) if r['status'] != 'PRD puudub' else 0
            
            meta = db().execute('''SELECT AVG(haul_distance) avg_len, GROUP_CONCAT(DISTINCT difficulty) diffs FROM forwarder_entries fe JOIN stands s ON s.id=fe.stand_id JOIN assortments a ON a.id=fe.assortment_id WHERE s.code=? AND a.name=?''', (r['stand_code'], r['assortment'])).fetchone()
            avg_len = '' if not meta or meta['avg_len'] is None else f"{meta['avg_len']:.0f}"
            diffs = '' if not meta or not meta['diffs'] else meta['diffs']
            writer.writerow([r['client'], r['stand_code'], r['assortment'], f"{r['harvester_total']:.3f}", f"{r['forwarder_total']:.3f}", f"{r['diff']:.3f}", r['status'], f"{confirmed:.3f}", avg_len, diffs])
        data = output.getvalue().encode('utf-8-sig')
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="excellent_koond.csv"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def seed_demo(self):
        conn = db(); cur = conn.cursor()
        stand_id = cur.execute("SELECT id FROM stands WHERE code='L-124'").fetchone()['id']
        ass_id = cur.execute("SELECT id FROM assortments WHERE name='Palk'").fetchone()['id']
        cur.execute('INSERT INTO harvester_imports(stand_id, assortment_id, work_date, shift, machine, operator, quantity, source_file, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (stand_id, ass_id, date.today().isoformat(), 'päev', 'Harvester 1', 'Operaator', 100.0, 'demo.prd', now()))
        conn.commit(); conn.close()
        self.redirect('/?msg=Demo%20PRD%20kogus%20lisatud')


def run():
    init_db()
    httpd = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Metsatöö kontroll PILV v14 töötab pordil {PORT}')
    httpd.serve_forever()


if __name__ == '__main__':
    run()
