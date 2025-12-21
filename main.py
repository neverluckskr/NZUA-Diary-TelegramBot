import cloudscraper
import sqlite3
import os
import requests
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from datetime import datetime, timedelta
import json
import re
import base64
import hashlib
from urllib.parse import urljoin, urlparse
import html
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from report_card_parser import parse_report_card

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except Exception:
    Fernet = None
    CRYPTO_AVAILABLE = False

API_BASE = "https://api-mobile.nz.ua"
scraper = cloudscraper.create_scraper()

# База даних
# На Railway volume монтується на /data, локально використовуємо data/
if os.path.isdir("/data"):
    DB_FILE = os.getenv("DB_FILE", "/data/nz_bot.db")
    ENCRYPTION_KEY_FILE = "/data/bot_encryption.key"
else:
    DB_FILE = os.getenv("DB_FILE", "data/nz_bot.db")
    ENCRYPTION_KEY_FILE = "data/bot_encryption.key"
# Власник / основний адмін (можна задати через змінну середовища OWNER_ID)
OWNER_ID = int(os.getenv("OWNER_ID", "1716175980"))

def get_db_connection():
    """Повертає з'єднання з базою даних SQLite"""
    return sqlite3.connect(DB_FILE)

# Ініціалізація шифрування
def get_encryption_key():
    """Отримує або створює ключ шифрування"""
    if not CRYPTO_AVAILABLE:
        return None
    
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        return key

ENCRYPTION_KEY = get_encryption_key()
cipher_suite = Fernet(ENCRYPTION_KEY) if CRYPTO_AVAILABLE and ENCRYPTION_KEY else None

def encrypt_data(data: str) -> str:
    """Шифрує дані"""
    if cipher_suite:
        return cipher_suite.encrypt(data.encode()).decode()
    return data

def decrypt_data(data: str) -> str:
    """Дешифрує дані"""
    if cipher_suite:
        try:
            return cipher_suite.decrypt(data.encode()).decode()
        except:
            return data
    return data

# Константи
WEEKDAYS = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', "П'ятниця", 'Субота', 'Неділя']
POLICY_TEXT = """📋 *Політика конфіденційності та умови використання*

🔐 *Безпека даних:*
• Всі ваші дані зберігаються у зашифрованому форматі
• Логіни та паролі шифруються перед збереженням у базі даних
• Бот не передає ваші особисті дані третім особам
• Ви можете видалити всі дані командою /logout

📱 *Використання:*
• Бот працює з офіційним API NZ.UA
• Ми не несемо відповідальності за збої або зміни в роботі API NZ.UA
• Використовуючи бота, ви автоматично погоджуєтеся з цією політикою

💬 *Підтримка:*
• Для питань та звернень використовуйте /support
• Адміністратори відповідають найближчим часом

⚖️ *Відповідальність:*
• Бот надається "як є" без гарантій
• Ми не несемо відповідальності за втрату даних або некоректну роботу
• Користувач несе повну відповідальність за безпеку своїх облікових даних

🔄 *Оновлення:*
• Політика може змінюватися без попередження
• Рекомендуємо періодично перевіряти цю сторінку
"""

VIP_TEXT = """🎁 Free VIP — безкоштовно для одноклассників!

• ✨ Можливості:
• 🔔 Нагадування за 5 хв до уроку
• 📬 Сповіщення про нові оцінки
• 🎯 Аналітика успішності
• 📊 Експорт даних

Free VIP видається тільки одноклассникам власника бота.
"""

# Список одноклассников (им доступен Free VIP)
CLASSMATES = [
    1132700501, 5279618116, 1247759597, 2082626797, 1411185092, 7053455242,
    1699237592, 5054267905, 5043377640, 5014023987, 6544254368, 7965156882,
    6624745883, 1131614831, 5073499407, 5680245801, 1018036447, 1516218125,
    6289987511, 1762490862, 2111925693, 6133869534, 2026640936, 1408724410,
    1698107724, 5328485637, 1085938822, 5085998468, 588691770
]

# Конфіг для VIP-джобів
REMINDER_MINUTES = 5  # сколько минут до урока отправлять напоминание
REMINDER_INTERVAL = 60  # проверять каждые N секунд
GRADE_POLL_INTERVAL = 300  # проверять оценки каждые N секунд
GRADES_LOOKBACK_DAYS = 30  # сколько дней смотреть на оценки
PING_URL = os.getenv("PING_URL")
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "600"))  # каждые N секунд слать пинг, по умолчанию 10 минут

# ============== БАЗА ДАНИХ ==============

def init_db():
    """Ініціалізація бази даних"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Таблиця сесій з шифрованими даними
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        password TEXT NOT NULL,
        token TEXT NOT NULL,
        student_id TEXT NOT NULL,
        fio TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Таблиця звернень до підтримки
    c.execute('''CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'open',
        resolved_by INTEGER,
        resolved_at TIMESTAMP,
        admin_note TEXT
    )''')
    
    # Таблиця VIP-підписок
    c.execute('''CREATE TABLE IF NOT EXISTS vip_users (
        user_id INTEGER PRIMARY KEY,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Таблиця відправлених нагадувань
    c.execute('''CREATE TABLE IF NOT EXISTS reminders_sent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        lesson_date TEXT NOT NULL,
        lesson_time TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Таблиця останніх відомих оцінок
    c.execute('''CREATE TABLE IF NOT EXISTS last_grades (
        user_id INTEGER NOT NULL,
        subject TEXT NOT NULL,
        last_grade TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, subject)
    )''')

    # Таблиця заявок на VIP
    c.execute('''CREATE TABLE IF NOT EXISTS vip_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        contact_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Таблиця дій адміністраторів
    c.execute('''CREATE TABLE IF NOT EXISTS admin_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_user INTEGER,
        ticket_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Налаштування VIP для користувачів
    c.execute('''CREATE TABLE IF NOT EXISTS vip_settings (
        user_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, key)
    )''')
    
    # Таблиця останніх відомих новин
    c.execute('''CREATE TABLE IF NOT EXISTS last_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        news_id TEXT NOT NULL,
        title TEXT,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(news_id)
    )''')

    # Міграція: додати колонки до таблиці support_tickets, якщо їх немає
    c.execute("PRAGMA table_info(support_tickets)")
    cols = [r[1] for r in c.fetchall()]
    
    if 'status' not in cols:
        c.execute("ALTER TABLE support_tickets ADD COLUMN status TEXT DEFAULT 'open'")
    if 'resolved_by' not in cols:
        c.execute("ALTER TABLE support_tickets ADD COLUMN resolved_by INTEGER")
    if 'resolved_at' not in cols:
        c.execute("ALTER TABLE support_tickets ADD COLUMN resolved_at TIMESTAMP")
    if 'admin_note' not in cols:
        c.execute("ALTER TABLE support_tickets ADD COLUMN admin_note TEXT")

    conn.commit()
    conn.close()
    
    if CRYPTO_AVAILABLE:
        print(f"✅ База даних (SQLite) ініціалізована (з шифруванням)")
    else:
        print(f"⚠️  База даних (SQLite) ініціалізована (без шифрування - встановіть cryptography)")

def save_session(user_id: int, username: str, password: str, token: str, student_id: str, fio: str):
    """Зберігає сесію користувача з шифрованими даними"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Шифруємо чутливі дані
    encrypted_password = encrypt_data(password)
    encrypted_token = encrypt_data(token)
    
    c.execute('''INSERT OR REPLACE INTO sessions 
                 (user_id, username, password, token, student_id, fio, last_login) 
                 VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''', 
              (user_id, username, encrypted_password, encrypted_token, student_id, fio))
    conn.commit()
    conn.close()

def get_session(user_id: int):
    """Отримує сесію користувача та дешифрує дані"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT username, password, token, student_id, fio FROM sessions WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            'username': row[0],
            'password': decrypt_data(row[1]),
            'token': decrypt_data(row[2]),
            'student_id': row[3],
            'fio': row[4]
        }
    return None

async def refresh_session(user_id: int):
    """Оновлює токен користувача за допомогою збережених credentials"""
    session = get_session(user_id)
    if not session:
        return None
    
    try:
        r = scraper.post(f"{API_BASE}/v1/user/login", json={
            "username": session['username'],
            "password": session['password']
        })
        
        if r.status_code == 200:
            data = r.json()
            save_session(
                user_id,
                session['username'],
                session['password'],
                data['access_token'],
                data['student_id'],
                data['FIO']
            )
            return get_session(user_id)
    except:
        pass
    
    return None

def delete_session_from_db(user_id: int):
    """Видаляє сесію користувача"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def save_support_ticket(user_id: int, message: str):
    """Зберігає звернення до підтримки"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO support_tickets (user_id, message) VALUES (?, ?)', (user_id, message))
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()
    return ticket_id

def get_ticket(ticket_id: int):
    """Повертає дані тикету або None"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT id, user_id, message, created_at, COALESCE(status,'open'), resolved_by, resolved_at, admin_note
                 FROM support_tickets WHERE id = ?''', (ticket_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        'id': row[0], 'user_id': row[1], 'message': row[2], 'created_at': row[3],
        'status': row[4], 'resolved_by': row[5], 'resolved_at': row[6], 'admin_note': row[7]
    }


def resolve_ticket_db(ticket_id: int, admin_id: int, note: str = None):
    """Позначає тикет як вирішений"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE support_tickets SET status = ?, resolved_by = ?, resolved_at = CURRENT_TIMESTAMP, admin_note = ? WHERE id = ?',
              ('closed', admin_id, note, ticket_id))
    conn.commit()
    # повертаємо оновлений запис
    c.execute('SELECT id, user_id, message, created_at, status FROM support_tickets WHERE id = ?', (ticket_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {'id': row[0], 'user_id': row[1], 'message': row[2], 'created_at': row[3], 'status': row[4]}


# --- Mark/grade helpers ---

def _extract_mark_info(mark):
    """Повертає кортеж (signature, display_text) для оцінки"""
    try:
        if isinstance(mark, dict):
            # value
            value = None
            for key in ('mark','value','grade','score','mark_value'):
                if key in mark and mark.get(key) is not None:
                    value = mark.get(key)
                    break
            mid = mark.get('id') or mark.get('mark_id') or ''
            date = mark.get('date') or mark.get('created_at') or mark.get('datetime') or ''
            val_str = str(value).strip() if value is not None else str(mark)
        else:
            val_str = str(mark)
            mid = ''
            date = ''
    except Exception:
        val_str = str(mark)
        mid = ''
        date = ''

    signature = f"{val_str}|{mid}|{date}"
    display = val_str if not date else f"{val_str} ({date})"
    return signature, display


def _extract_numeric_from_mark(mark):
    """Старається витягти числове значення з оцінки, повертає float або None"""
    try:
        if isinstance(mark, (int, float)):
            return float(mark)
        if isinstance(mark, dict):
            for key in ('mark','value','grade','score','mark_value'):
                if key in mark and mark.get(key) is not None:
                    s = str(mark.get(key))
                    m = re.search(r"(\d+(?:[\.,]\d+)?)", s)
                    if m:
                        return float(m.group(1).replace(',', '.'))
                    else:
                        return None
        s = str(mark)
        m = re.search(r"(\d+(?:[\.,]\d+)?)", s)
        if m:
            return float(m.group(1).replace(',', '.'))
    except Exception:
        return None
    return None


def parse_grades_from_html(html: str):
    """Парсить сторінку 'Виписка оцінок' і повертати (start_date, end_date, {subject: [(token, date_iso_or_None), ...]})"""
    from bs4 import BeautifulSoup

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text("\n", strip=True)

        # Try to read date inputs from the form (date_from / date_to)
        try:
            # Try multiple selectors for date inputs
            df = soup.find('input', attrs={'name': 'date_from'}) or soup.find(id='classselectform-date_from') or soup.find('input', id='classselectform-date_from')
            dt = soup.find('input', attrs={'name': 'date_to'}) or soup.find(id='classselectform-date_to') or soup.find('input', id='classselectform-date_to')
            if df and df.get('value'):
                start_date = df.get('value')
                print(f"[PARSE_HTML] Found start_date from input: {start_date}")
            if dt and dt.get('value'):
                end_date = dt.get('value')
                print(f"[PARSE_HTML] Found end_date from input: {end_date}")
        except Exception as e:
            print(f"[PARSE_HTML] Error reading date inputs: {e}")
            pass
    except Exception:
        # fallback to raw text
        text = html

    # helper to try to find a date inside a token string
    months = {
        'січня': 1, 'лютого': 2, 'березня': 3, 'квітня': 4, 'травня': 5, 'червня': 6,
        'липня': 7, 'серпня': 8, 'вересня': 9, 'жовтня': 10, 'листопада': 11, 'грудня': 12
    }

    def _try_parse_date_from_text(s: str):
        try:
            s = s or ''
            if not isinstance(s, str):
                s = str(s)
            # ISO
            m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
            if m:
                return m.group(1)
            # dd.mm.yyyy
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
            if m:
                d, mo, y = m.groups()
                try:
                    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                except Exception:
                    pass
            # Ukrainian month names: '19 грудня 2025' or '19 грудня'
            # Escape special regex characters in month names and join them
            month_pattern = '|'.join(re.escape(month) for month in months.keys())
            m = re.search(r"(\d{1,2})\s+({})\s*(\d{4})?".format(month_pattern), s, flags=re.IGNORECASE)
            if m:
                d = int(m.group(1))
                mon_name = m.group(2).lower()
                y = int(m.group(3)) if m.group(3) else datetime.now().year
                mo = months.get(mon_name, None)
                if mo:
                    try:
                        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                    except Exception:
                        pass
        except Exception as e:
            # If any error occurs, just return None (no date found)
            pass
        return None

    # Attempt to extract the visible date range
    start_date = None
    end_date = None
    m = re.search(r"Оберіть діапазон дат:\s*(\d{4}-\d{2}-\d{2})\s*по\s*(\d{4}-\d{2}-\d{2})", text)
    if not start_date and m:
        start_date, end_date = m.group(1), m.group(2)
    else:
        # try simple two dates
        m2 = re.search(r"(\d{4}-\d{2}-\d{2}).{0,40}(\d{4}-\d{2}-\d{2})", text)
        if not start_date and m2:
            start_date, end_date = m2.group(1), m2.group(2)

    # Try to find table rows / lines first
    subjects = {}
    try:
        if 'Виписка оцінок' in text or 'Отримані результати' in text:
            lines = text.splitlines()
            print(f"[PARSE_HTML] Processing {len(lines)} lines from text")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                m = re.match(r'^\s*(\d+)\s+([^\t\n\r\d].*?)\s{2,}(.+)$', line)
                if not m:
                    parts = line.split('\t')
                    if len(parts) >= 3 and parts[0].strip().isdigit():
                        subj = parts[1].strip()
                        marks_raw = parts[2].strip()
                    else:
                        continue
                else:
                    subj = m.group(2).strip()
                    marks_raw = m.group(3).strip()

                tokens_raw = [t.strip() for t in re.split(r",\s*", marks_raw) if t.strip()]
                tokens = []
                for t in tokens_raw:
                    d = _try_parse_date_from_text(t)
                    tokens.append((t, d))
                if tokens:
                    subjects[subj] = tokens
                    print(f"[PARSE_HTML] Found subject: {subj} with {len(tokens)} marks")
    except Exception as e:
        print(f"[PARSE_HTML] Error parsing text lines: {e}")
        pass

    # If no subjects found via text, try to find HTML tables
    if not subjects:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            # First try to find the specific marks-report table
            marks_table = soup.find('table', class_='marks-report')
            if not marks_table:
                # Fallback to any table
                tables = soup.find_all('table')
                print(f"[PARSE_HTML] Found {len(tables)} tables in HTML (no marks-report table)")
            else:
                tables = [marks_table]
                print(f"[PARSE_HTML] Found marks-report table")
            
            for table in tables:
                rows = table.select('tbody tr') if table.select('tbody') else table.select('tr')
                print(f"[PARSE_HTML] Processing table with {len(rows)} rows")
                row_count = 0
                for tr in rows:
                    tds = tr.select('td')
                    if len(tds) >= 3:
                        # Get text from each td, preserving structure
                        num_text = tds[0].get_text(' ', strip=True)
                        subj = tds[1].get_text(' ', strip=True)
                        marks_raw = tds[2].get_text(' ', strip=True)
                        
                        # Skip header row or empty rows
                        if not num_text.strip().isdigit() or not subj:
                            continue
                        
                        # Skip rows with empty marks (like "Польська мова" with empty td)
                        if not marks_raw or marks_raw.strip() == '':
                            print(f"[PARSE_HTML] Skipping subject '{subj}' - no marks")
                            continue
                        
                        row_count += 1
                            
                        # Split marks by comma, but preserve parentheses content
                        tokens_raw = []
                        # More careful splitting - split by comma but keep parentheses together
                        current_token = ""
                        paren_depth = 0
                        for char in marks_raw:
                            if char == '(':
                                paren_depth += 1
                                current_token += char
                            elif char == ')':
                                paren_depth -= 1
                                current_token += char
                            elif char == ',' and paren_depth == 0:
                                if current_token.strip():
                                    tokens_raw.append(current_token.strip())
                                current_token = ""
                            else:
                                current_token += char
                        if current_token.strip():
                            tokens_raw.append(current_token.strip())
                        
                        tokens = []
                        for t in tokens_raw:
                            if t:  # Only process non-empty tokens
                                try:
                                    d = _try_parse_date_from_text(t)
                                    tokens.append((t, d))
                                except Exception as e:
                                    # If date parsing fails, just add token without date
                                    print(f"[PARSE_HTML] Warning: failed to parse date from token '{t}': {e}")
                                    tokens.append((t, None))
                        if tokens and subj:
                            subjects[subj] = tokens
                            print(f"[PARSE_HTML] Found subject in table: {subj} with {len(tokens)} marks")
                
                print(f"[PARSE_HTML] Processed {row_count} data rows from table")
        except Exception as e:
            print(f"[PARSE_HTML] Error parsing HTML tables: {e}")
            import traceback
            print(f"[PARSE_HTML] Traceback: {traceback.format_exc()}")
            pass
    
    # If still no subjects, try more flexible parsing
    if not subjects:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            # Try to find any divs or spans that might contain subject names and marks
            # Look for patterns like "Subject Name: 5, 6, 7" or similar
            all_text = soup.get_text("\n", strip=True)
            print(f"[PARSE_HTML] Trying flexible parsing, text length: {len(all_text)}")
            # Try to find lines with numbers followed by text (subject names)
            lines = all_text.splitlines()
            for i, line in enumerate(lines):
                line = line.strip()
                # Look for pattern: number, subject name, marks
                # More flexible regex
                m = re.match(r'^\s*(\d+)[\.\)\s]+(.+?)\s+([\d\s,НПВ\-]+)$', line)
                if m:
                    num, subj, marks_raw = m.groups()
                    tokens_raw = [t.strip() for t in re.split(r"[,;\s]+", marks_raw) if t.strip() and t.strip() not in ['', '-']]
                    if tokens_raw:
                        tokens = []
                        for t in tokens_raw:
                            d = _try_parse_date_from_text(t)
                            tokens.append((t, d))
                        if tokens and subj.strip():
                            subjects[subj.strip()] = tokens
                            print(f"[PARSE_HTML] Found subject (flexible): {subj.strip()} with {len(tokens)} marks")
        except Exception as e:
            print(f"[PARSE_HTML] Error in flexible parsing: {e}")
            pass
    
    print(f"[PARSE_HTML] Final result: {len(subjects)} subjects found")
    return start_date, end_date, subjects

    return start_date, end_date, subjects


def is_vip_user(user_id: int) -> bool:
    """Перевіряє чи є користувач VIP"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT expires_at FROM vip_users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row and row[0]:
        try:
            expires = datetime.fromisoformat(row[0])
            return expires > datetime.now()
        except Exception:
            return False
    return False

# ----------------- VIP HELPERS -----------------

def grant_vip(user_id: int, days: int = 30):
    """Надає VIP на вказану кількість днів"""
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO vip_users (user_id, expires_at, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
              (user_id, expires_at))
    conn.commit()
    conn.close()


def revoke_vip(user_id: int):
    """Відміняє VIP"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM vip_users WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def save_reminder_sent(user_id: int, lesson_date: str, lesson_time: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO reminders_sent (user_id, lesson_date, lesson_time) VALUES (?, ?, ?)',
              (user_id, lesson_date, lesson_time))
    conn.commit()
    conn.close()


def has_reminder_sent(user_id: int, lesson_date: str, lesson_time: str) -> bool:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT 1 FROM reminders_sent WHERE user_id = ? AND lesson_date = ? AND lesson_time = ?',
              (user_id, lesson_date, lesson_time))
    res = c.fetchone()
    conn.close()
    return bool(res)


def get_last_grades(user_id: int) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT subject, last_grade FROM last_grades WHERE user_id = ?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def save_last_grades(user_id: int, grades: dict):
    conn = get_db_connection()
    c = conn.cursor()
    for subject, grade in grades.items():
        c.execute('INSERT OR REPLACE INTO last_grades (user_id, subject, last_grade, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
                  (user_id, subject, grade))
    conn.commit()
    conn.close()


def create_vip_request(user_id: int, message: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO vip_requests (user_id, contact_text) VALUES (?, ?)', (user_id, message))
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()
    return ticket_id


def log_admin_action(admin_id: int, action: str, target_user: int = None, ticket_id: int = None, details: str = None):
    """Логує дію адміністратора в БД"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO admin_actions (admin_id, action, target_user, ticket_id, details) VALUES (?, ?, ?, ?, ?)',
              (admin_id, action, target_user, ticket_id, details))
    conn.commit()
    conn.close()


def set_vip_setting(user_id: int, key: str, value: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO vip_settings (user_id, key, value, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
              (user_id, key, str(value)))
    conn.commit()
    conn.close()


def get_vip_setting(user_id: int, key: str, default=None):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT value FROM vip_settings WHERE user_id = ? AND key = ?', (user_id, key))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    return default


def get_all_vip_settings(user_id: int) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT key, value FROM vip_settings WHERE user_id = ?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# Адміни (можна задати через змінну середовища ADMIN_IDS через кому, наприклад: "1716175980,751886453")
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")
if ADMIN_IDS_ENV:
    ADMINS = [int(uid.strip()) for uid in ADMIN_IDS_ENV.split(",") if uid.strip()]
else:
    # За замовчуванням: власник + його дівчина
    ADMINS = [1716175980, 751886453, 1699237592]

def is_admin(user_id: int) -> bool:
    """Перевіряє чи є користувач адміністратором.
    Перевіряє як жорстко заданий список `ADMINS`, так і змінну оточення `ADMIN_IDS`.
    """
    if user_id in ADMINS:
        return True
    admin_env = os.getenv('ADMIN_IDS', '')
    if not admin_env:
        return False
    return str(user_id) in [x.strip() for x in admin_env.split(',') if x.strip()]

# ----------------- BACKGROUND JOBS -----------------

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет расписание VIP-пользователей и отправляет напоминания за REMINDER_MINUTES"""
    print("[VIP JOB] Checking reminders...")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, expires_at FROM vip_users WHERE expires_at > ?', (datetime.now().isoformat(),))
    users = c.fetchall()
    conn.close()
    
    if not users:
        print("[VIP JOB] No active VIP users found")
        return

    print(f"[VIP JOB] Found {len(users)} active VIP users")

    for user in users:
        try:
            user_id = user[0]
            session = get_session(user_id)
            if not session:
                print(f"[VIP JOB] No session for user {user_id}")
                continue

            # Проверяем настройки напоминаний
            reminders_enabled = get_vip_setting(user_id, 'reminders', '1') == '1'
            if not reminders_enabled:
                print(f"[VIP JOB] User {user_id} has reminders disabled; skipping")
                continue

            today = datetime.now().strftime('%Y-%m-%d')
            
            # Пробуем получить расписание через API
            try:
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/timetable",
                    headers={"Authorization": f"Bearer {session['token']}"},
                    json={"student_id": session['student_id'], "start_date": today, "end_date": today},
                    timeout=10
                )
            except Exception as e:
                print(f"[VIP JOB] API request failed for user {user_id}: {e}")
                continue

            if r.status_code == 401:
                print(f"[VIP JOB] Token expired for user {user_id}, refreshing...")
                new_s = await refresh_session(user_id)
                if new_s:
                    session = new_s
                    try:
                        r = scraper.post(
                            f"{API_BASE}/v1/schedule/timetable",
                            headers={"Authorization": f"Bearer {session['token']}"},
                            json={"student_id": session['student_id'], "start_date": today, "end_date": today},
                            timeout=10
                        )
                    except Exception as e:
                        print(f"[VIP JOB] API request failed after refresh for user {user_id}: {e}")
                        continue
                else:
                    print(f"[VIP JOB] Could not refresh session for user {user_id}")
                    continue

            if r.status_code != 200:
                print(f"[VIP JOB] API returned {r.status_code} for user {user_id}")
                continue

            try:
                data = r.json()
            except Exception as e:
                print(f"[VIP JOB] Could not parse JSON for user {user_id}: {e}")
                continue
            
            now_dt = datetime.now()
            lessons_today = []

            for day in data.get('dates', []):
                for call in day.get('calls', []):
                    time_start = call.get('time_start')
                    if not time_start:
                        continue
                    
                    subject_name = "Урок"
                    subjects = call.get('subjects', [])
                    if subjects:
                        subject_name = subjects[0].get('subject_name', subject_name)
                    
                    lessons_today.append({'time': time_start, 'subject': subject_name})
                    
                    try:
                        lesson_dt = datetime.strptime(f"{today} {time_start}", "%Y-%m-%d %H:%M")
                    except Exception:
                        continue

                    delta = (lesson_dt - now_dt).total_seconds()
                    
                    # Расширяем окно: напоминание за REMINDER_MINUTES минут (с запасом)
                    # Отправляем если урок через 1-6 минут
                    min_delta = 60  # минимум 1 минута до урока
                    max_delta = (REMINDER_MINUTES + 1) * 60  # максимум REMINDER_MINUTES+1 минут
                    
                    if min_delta < delta <= max_delta:
                        lesson_date = today
                        lesson_time = time_start

                        if not has_reminder_sent(user_id, lesson_date, lesson_time):
                            minutes_left = int(delta // 60)
                            try:
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=f"⏰ *{lesson_time}* — {subject_name}\n_через {minutes_left} хв_",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                                save_reminder_sent(user_id, lesson_date, lesson_time)
                                print(f"[VIP JOB] ✅ Sent reminder to {user_id} for {lesson_time} {subject_name} (in {minutes_left} min)")
                            except Exception as e:
                                print(f"[VIP JOB] ❌ Could not send reminder to {user_id}: {e}")
            
            if lessons_today:
                print(f"[VIP JOB] User {user_id} has {len(lessons_today)} lessons today: {[l['time'] for l in lessons_today]}")

        except Exception as e:
            print(f"[VIP JOB] Error processing user {user}: {e}")
            import traceback
            traceback.print_exc()


async def check_grades(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет новые оценки для VIP-пользователей через новости и отправляет уведомления"""
    print("[VIP JOB] Checking grades from news")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, expires_at FROM vip_users WHERE expires_at > ?', (datetime.now().isoformat(),))
    users = c.fetchall()
    conn.close()

    for user in users:
        try:
            user_id = user[0]
            session = get_session(user_id)
            if not session:
                continue

            # Проверяем настройки уведомлений
            notif_enabled = get_vip_setting(user_id, 'grade_notifications', '1') == '1'
            if not notif_enabled:
                print(f"[VIP JOB] User {user_id} has grade notifications disabled; skipping")
                continue

            # Получаем новости с оценками
            try:
                from bs4 import BeautifulSoup
                login_url = "https://nz.ua/login"
                login_page = scraper.get(login_url)
                login_soup = BeautifulSoup(login_page.text, "html.parser")
                csrf = None
                meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                if meta_csrf:
                    csrf = meta_csrf.get('content')
                hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                if hidden_csrf and hidden_csrf.get('value'):
                    csrf = hidden_csrf.get('value')

                login_data = {
                    "LoginForm[login]": session['username'],
                    "LoginForm[password]": session['password'],
                    "LoginForm[rememberMe]": "1"
                }
                headers = {}
                if csrf:
                    login_data['_csrf'] = csrf
                    headers['X-CSRF-Token'] = csrf

                scraper.post(login_url, data=login_data, headers=headers)

                # Получаем новости
                endpoints = ["/dashboard/news", "/dashboard", "/news", "/site/news"]
                base_url = "https://nz.ua"
                news_resp = None

                for ep in endpoints:
                    url = urljoin(base_url, ep)
                    try:
                        resp = scraper.get(url)
                        if resp.status_code == 200 and ('Мої новини' in resp.text or 'school-news-list' in resp.text):
                            news_resp = resp
                            break
                    except Exception:
                        continue

                if not news_resp:
                    print(f"[VIP JOB] Could not fetch news for user {user_id}")
                    continue

                # Парсим через BeautifulSoup (как в news_cmd)
                soup = BeautifulSoup(news_resp.text, "html.parser")
                root = soup.find("div", id="school-news-list")
                
                if not root:
                    print(f"[VIP JOB] No school-news-list found for user {user_id}")
                    continue
                
                items = root.select("div.news-page__item")
                if not items:
                    print(f"[VIP JOB] No news items found for user {user_id}")
                    continue

                # Получаем последние известные новости из БД
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT news_id FROM last_news WHERE news_id LIKE ? ORDER BY created_at DESC LIMIT 200', (f"{user_id}_%",))
                known_news_ids = {row[0] for row in c.fetchall()}
                conn.close()

                new_grades = []
                
                for item in items[:20]:
                    name_el = item.select_one(".news-page__header .news-page__name")
                    date_el = item.select_one(".news-page__header .news-page__date")
                    desc_el = item.select_one(".news-page__desc")
                    
                    teacher = name_el.get_text(strip=True) if name_el else ""
                    date_str = date_el.get_text(strip=True) if date_el else ""
                    
                    if not desc_el:
                        continue
                    
                    desc_text = desc_el.get_text(" ", strip=True)
                    
                    # Ищем паттерн оценки
                    grade_pattern = r'Ви отримали оцінку\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)'
                    changed_pattern = r'Оцінка змінена на\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)'
                    
                    match = re.search(grade_pattern, desc_text)
                    is_changed = False
                    if not match:
                        match = re.search(changed_pattern, desc_text)
                        is_changed = True
                    
                    if not match:
                        continue
                    
                    grade = match.group(1).strip()
                    subject = match.group(2).strip()
                    grade_type = match.group(3).strip()
                    
                    # Формируем уникальный ID для новости (включаем user_id)
                    news_id = f"{user_id}_{teacher}_{date_str}_{grade}_{subject}"
                    
                    if news_id not in known_news_ids:
                        new_grades.append({
                            'teacher': teacher,
                            'date': date_str,
                            'grade': grade,
                            'subject': subject,
                            'type': grade_type,
                            'is_changed': is_changed
                        })
                        # Сохраняем в БД
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute('INSERT OR IGNORE INTO last_news (news_id, title, content) VALUES (?, ?, ?)',
                                (news_id, subject, str({'grade': grade, 'teacher': teacher})))
                        conn.commit()
                        conn.close()

                if new_grades:
                    # Форматируем уведомления
                    text_lines = ["📬 *Нові оцінки:*"]
                    for item in new_grades[:10]:
                        teacher_name = item.get('teacher', '')
                        if teacher_name:
                            name_parts = teacher_name.split()
                            if len(name_parts) >= 3:
                                short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}."
                            elif len(name_parts) == 2:
                                short_name = f"{name_parts[0]} {name_parts[1][0]}."
                            else:
                                short_name = teacher_name
                        else:
                            short_name = "—"
                        
                        date_str = item.get('date', '')
                        grade = item.get('grade', '')
                        subject = item.get('subject', '')
                        grade_type = item.get('type', '')
                        is_changed = item.get('is_changed', False)
                        
                        formatted_type = format_grade_type(grade_type)
                        
                        if is_changed:
                            text_lines.append(f"• {short_name} - {date_str}, змінила оцінку на *{grade}* з _{subject}_, {formatted_type}")
                        else:
                            text_lines.append(f"• {short_name} - {date_str}, поставила *{grade}* з _{subject}_, {formatted_type}")

                    try:
                        await context.bot.send_message(chat_id=user_id, text="\n".join(text_lines), parse_mode=ParseMode.MARKDOWN)
                        print(f"[VIP JOB] Sent {len(new_grades)} grade notifications to {user_id}")
                    except Exception as e:
                        print(f"[VIP JOB] Could not send grades to {user_id}: {e}")
                else:
                    print(f"[VIP JOB] No new grades for user {user_id}")

            except Exception as e:
                print(f"[VIP JOB] Error checking news for user {user_id}: {e}")
                import traceback
                traceback.print_exc()
                continue

        except Exception as e:
            print(f"[VIP JOB] Error checking grades for user {user}: {e}")

# ============== КОМАНДИ ==============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - початок роботи"""
    # Перевіряємо чи є активна сесія
    session = get_session(update.effective_user.id)
    if session:
        keyboard = [
            ['📅 Розклад', '📋 Табель'],
            ['📰 Новини', '📊 Середній бал'],
            ['🎁 Free VIP', '✉️ Підтримка']
        ]
        # Для админов добавляем кнопку админ-меню
        if is_admin(update.effective_user.id):
            keyboard.append(['🛠 Админ-меню'])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        welcome_back = (
            f"👋 *З поверненням, {session['fio']}!*\n\n"
            "🎓 Ваш електронний щоденник готовий до роботи\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 *Оберіть функцію з меню нижче:*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📅 Розклад • 📋 Табель • 📰 Новини\n"
            "📊 Середній бал • 🎁 VIP • ✉️ Підтримка\n\n"
            "_Потрібна допомога? Натисніть_ /help"
        )
        await update.message.reply_text(
            welcome_back,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    welcome_text = (
        "👋 *Вітаємо в NZ.UA Bot!*\n\n"
        "🎓 Це неофіційний бот для зручної роботи з електронним щоденником NZ.UA\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ *Можливості бота:*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📅 Розклад уроків на будь-який день\n"
        "📋 Табель успішності з оцінками\n"
        "📰 Новини та оцінки від вчителів\n"
        "📊 Розрахунок середнього балу\n"
        "🔔 Сповіщення про нові оцінки (VIP)\n"
        "⏰ Нагадування про уроки (VIP)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 *Безпека:*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• Ваші дані зберігаються в зашифрованому вигляді\n"
        "• Бот не передає дані третім особам\n"
        "• Ви можете видалити дані командою /logout\n"
        "• Детальніше: /policy\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📱 *Для входу введіть свій логін NZ.UA:*"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    context.user_data['step'] = 'waiting_login'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстових повідомлень"""
    step = context.user_data.get('step')
    
    # Admin replying to a ticket
    if step == 'admin_reply':
        if not is_admin(update.effective_user.id):
            await update.message.reply_text('❌ Тільки адміни можуть виконувати цю дію')
            context.user_data.pop('step', None)
            context.user_data.pop('reply_ticket_id', None)
            return
        ticket_id = context.user_data.get('reply_ticket_id')
        if not ticket_id:
            await update.message.reply_text('❌ Немає відкритого тикета для відповіді')
            context.user_data.pop('step', None)
            return
        text = update.message.text
        t = get_ticket(ticket_id)
        if not t:
            await update.message.reply_text('❌ Тикет не знайдено')
            context.user_data.pop('step', None)
            context.user_data.pop('reply_ticket_id', None)
            return
        try:
            await context.bot.send_message(t['user_id'], f"✉️ Адмін відповів на ваше звернення #{ticket_id}:\n\n{text}")
            log_admin_action(update.effective_user.id, 'reply_ticket', target_user=t['user_id'], ticket_id=ticket_id, details=text)
            await update.message.reply_text('✅ Повідомлення надіслано користувачу')
        except Exception as e:
            await update.message.reply_text(f'❌ Не вдалось надіслати повідомлення: {e}')
        context.user_data.pop('step', None)
        context.user_data.pop('reply_ticket_id', None)
        return

    # Admin broadcast message to all users
    if step == 'admin_broadcast':
        if not is_admin(update.effective_user.id):
            await update.message.reply_text('❌ Тільки адміни можуть виконувати цю дію')
            context.user_data.pop('step', None)
            return
        
        broadcast_text = update.message.text
        
        # Получаем всех пользователей из базы данных
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT DISTINCT user_id FROM sessions')
        user_rows = c.fetchall()
        conn.close()
        
        total_users = len(user_rows)
        success_count = 0
        failed_count = 0
        
        # Отправляем сообщение всем пользователям
        await update.message.reply_text(f"📤 Розсилка повідомлення {total_users} користувачам...")
        
        for row in user_rows:
            user_id = row[0]
            try:
                await context.bot.send_message(user_id, broadcast_text)
                success_count += 1
            except Exception as e:
                failed_count += 1
                print(f"[BROADCAST] Failed to send to user {user_id}: {e}")
        
        # Логируем действие админа
        log_admin_action(update.effective_user.id, 'broadcast', details=f'sent to {success_count}/{total_users} users')
        
        # Отправляем отчет админу
        result_text = (
            f"✅ *Розсилка завершена*\n\n"
            f"📊 Статистика:\n"
            f"• Успішно: {success_count}\n"
            f"• Не вдалось: {failed_count}\n"
            f"• Всього: {total_users}"
        )
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
        
        context.user_data.pop('step', None)
        return

    # Обробка логіну
    if step == 'waiting_login':
        context.user_data['login'] = update.message.text
        context.user_data['step'] = 'waiting_password'
        await update.message.reply_text("🔒 Тепер введи пароль:")
        return
    
    # Обробка пароля
    elif step == 'waiting_password':
        login = context.user_data['login']
        password = update.message.text
        
        # Видаляємо повідомлення з паролем для безпеки
        try:
            await update.message.delete()
        except:
            pass
        
        try:
            r = scraper.post(f"{API_BASE}/v1/user/login", json={
                "username": login,
                "password": password
            })
            
            if r.status_code == 200:
                data = r.json()
                
                # Зберігаємо в БД з паролем для автоматичного оновлення
                save_session(
                    update.effective_user.id,
                    login,
                    password,
                    data['access_token'],
                    data['student_id'],
                    data['FIO']
                )
                
                # Автоматично видаємо Free VIP одноклассникам на 30 днів
                vip_msg = ""
                if update.effective_user.id in CLASSMATES and not is_vip_user(update.effective_user.id):
                    grant_vip(update.effective_user.id, 30)
                    vip_msg = "\n\n🎁 *Тобі активовано Free VIP на 30 днів!*"
                
                keyboard = [
                    ['📅 Розклад', '📋 Табель'],
                    ['📰 Новини', '📊 Середній бал'],
                    ['🎁 Free VIP', '✉️ Підтримка']
                ]
                if is_admin(update.effective_user.id):
                    keyboard.append(['🛠 Админ-меню'])
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                
                await update.message.reply_text(
                    f"✅ Вітаю, {data['FIO']}!\n\n"
                    f"🎓 ID учня: {data['student_id']}\n\n"
                    f"Обирай функцію з меню нижче 👇{vip_msg}",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    "❌ Невірний логін або пароль.\n\n"
                    "Спробуй ще раз: /start"
                )
        
        except Exception as e:
            await update.message.reply_text(f"❌ Помилка підключення: {e}\n\nСпробуй пізніше.")
        
        context.user_data.clear()
        return
    
    # Обробка звернень до підтримки
    elif step == 'support':
        message = update.message.text
        ticket_id = save_support_ticket(update.effective_user.id, message)

        notify_text = (
            f"✉️ Нова заявка #{ticket_id}\n"
            f"Від: {update.effective_user.full_name} ({update.effective_user.username or '—'})\n"
            f"User ID: {update.effective_user.id}\n\n"
            f"{message}"
        )

        # Створюємо кнопки для адмінів
        profile_url = f"tg://user?id={update.effective_user.id}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 Профіль", url=profile_url)],
            [InlineKeyboardButton("✅ Дати VIP 30д", callback_data=f"admin:grant_vip:{update.effective_user.id}:30"), InlineKeyboardButton("❌ Забрати VIP", callback_data=f"admin:revoke_vip:{update.effective_user.id}")],
            [InlineKeyboardButton("✅ Закрити тикет", callback_data=f"admin:resolve_ticket:{ticket_id}"), InlineKeyboardButton("✉️ Відповісти", callback_data=f"admin:reply_ticket:{ticket_id}")]
        ])

        # Повідомляємо власника
        try:
            await context.bot.send_message(OWNER_ID, notify_text, reply_markup=kb)
        except Exception as e:
            print(f"[SUPPORT] Could not notify owner {OWNER_ID}: {e}")

        # Повідомляємо додаткових адміністраторів, якщо вказані
        admin_env = os.getenv('ADMIN_IDS', '')
        if admin_env:
            for aid in [a.strip() for a in admin_env.split(',') if a.strip()]:
                try:
                    await context.bot.send_message(int(aid), notify_text, reply_markup=kb)
                except Exception as e:
                    print(f"[SUPPORT] Could not notify admin {aid}: {e}")

        await update.message.reply_text(
            f"✅ Ваше звернення #{ticket_id} зафіксовано!\n\n"
            f"Адмін отримав заявку і зв'яжеться з вами найближчим часом."
        )
        context.user_data.clear()
        return

    # Обробка заявки на VIP
    elif step == 'vip_request':
        message = update.message.text
        ticket_id = create_vip_request(update.effective_user.id, message)

        notify_text = (
            f"🛎️ Нова заявка на VIP #{ticket_id} від {update.effective_user.id} ({update.effective_user.username or update.effective_user.full_name}):\n\n{message}\n\nКонтакт для оплати: https://t.me/impulsedevfd"
        )

        # Створюємо кнопки для адмінів
        profile_url = f"tg://user?id={update.effective_user.id}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 Профіль", url=profile_url)],
            [InlineKeyboardButton("✅ Дати VIP 30д", callback_data=f"admin:grant_vip:{update.effective_user.id}:30"), InlineKeyboardButton("❌ Забрати VIP", callback_data=f"admin:revoke_vip:{update.effective_user.id}")],
            [InlineKeyboardButton("✅ Закрити тикет", callback_data=f"admin:resolve_ticket:{ticket_id}")]
        ])

        # Повідомляємо власника
        try:
            await context.bot.send_message(OWNER_ID, notify_text, reply_markup=kb)
        except Exception as e:
            print(f"[VIP REQUEST] Could not notify owner {OWNER_ID}: {e}")

        # Повідомляємо адмінів (ADMIN_IDS in env) якщо вказані
        admin_env = os.getenv('ADMIN_IDS', '')
        if admin_env:
            for aid in [a.strip() for a in admin_env.split(',') if a.strip()]:
                try:
                    await context.bot.send_message(int(aid), notify_text, reply_markup=kb)
                except Exception as e:
                    print(f"[VIP REQUEST] Could not notify admin {aid}: {e}")

        await update.message.reply_text(f"✅ Ваша заявка на VIP #{ticket_id} відправлена! Адмін зв'яжеться з вами.")
        context.user_data.clear()
        return
    
    # Проверяем, не является ли сообщение датами для среднего бала
    else:
        # Получаем текст сообщения
        text = update.message.text if update.message.text else ""
        # Проверяем формат дат (например: "10.12.2025 20.12.2025" или "05.10.2025 25.11.2025")
        date_pattern = r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})'
        match = re.match(date_pattern, text.strip())
        if match:
            # Это даты для среднего бала
            try:
                d1, m1, y1, d2, m2, y2 = match.groups()
                start_date = f"{y1}-{m1.zfill(2)}-{d1.zfill(2)}"
                end_date = f"{y2}-{m2.zfill(2)}-{d2.zfill(2)}"
                # Проверяем валидность дат
                datetime.strptime(start_date, '%Y-%m-%d')
                datetime.strptime(end_date, '%Y-%m-%d')
                # Вызываем avg с этими датами
                context.args = [start_date, end_date]
                await avg(update, context)
                return
            except Exception:
                pass  # Если не удалось распарсить, продолжаем как обычно
        
        await update.message.reply_text(
            "❓ Не розумію цю команду.\n\n"
            "Використовуй меню або команди:\n"
            "/start - Початок роботи\n"
            "/help - Допомога"
        )

# ============== РОЗКЛАД ==============

async def show_weekday_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, kind='schedule'):
    """Показує клавіатуру вибору дня тижня"""
    buttons = []
    for day in WEEKDAYS:
        buttons.append([InlineKeyboardButton(day, callback_data=f"{kind}:{day}")])
    
    # Додаємо кнопку "Сьогодні"
    today_weekday = datetime.now().weekday()
    today_name = WEEKDAYS[today_weekday]
    buttons.insert(0, [InlineKeyboardButton(f"📍 Сьогодні ({today_name})", callback_data=f"{kind}:today")])
    
    kb = InlineKeyboardMarkup(buttons)
    
    text = "📅 Оберіть день для розкладу:" if kind == 'schedule' else "📚 Оберіть день для домашки:"
    await update.message.reply_text(text, reply_markup=kb)

async def get_date_for_weekday(day_name: str) -> str:
    """Конвертує назву дня у дату"""
    if day_name == 'today':
        return datetime.now().strftime('%Y-%m-%d')
    
    mapping = {
        'Понеділок': 0,
        'Вівторок': 1,
        'Середа': 2,
        'Четвер': 3,
        "П'ятниця": 4
    }
    
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    target = monday + timedelta(days=mapping.get(day_name, 0))
    
    return target.strftime('%Y-%m-%d')

async def schedule_for_date(query_or_update, context: ContextTypes.DEFAULT_TYPE, date: str):
    """Отримує розклад на конкретну дату (компактне форматування + домашка прив'язана до конкретного уроку)"""
    user_id = (query_or_update.from_user.id if hasattr(query_or_update, 'from_user')
               else query_or_update.effective_user.id)

    def split_diary_tasks(tasks: list) -> tuple[str | None, list[str]]:
        topic_parts: list[str] = []
        homework_parts: list[str] = []

        for raw in tasks or []:
            # Разбиваем по переносам строк (данные могут прийти как одна строка с \n)
            for line in str(raw).split('\n'):
                s = line.strip()
                if not s:
                    continue

                # Мусор: числа, одиночные буквы (Н, П, В и т.д.)
                if re.fullmatch(r"\d+", s):
                    continue
                if re.fullmatch(r"[A-Za-zА-Яа-яЄєІіЇїҐґ]", s):
                    continue

                # Тема: только строки с "Поточна:" или "Тема:"
                m_topic = re.match(r"^(поточна|тема)\s*[:\-]?\s*(.*)$", s, flags=re.IGNORECASE)
                if m_topic:
                    topic_parts.append((m_topic.group(2) or '').strip())
                    continue

                # Всё остальное — ДЗ. Убираем префикс "Д/з:" / "ДЗ:" если есть
                hw_text = s
                m_hw = re.match(r"^(д\s*/\s*з|дз)\s*[:\-]?\s*(.*)$", s, flags=re.IGNORECASE)
                if m_hw:
                    hw_text = (m_hw.group(2) or '').strip()

                if hw_text:
                    homework_parts.append(hw_text)

        topic_text = "\n".join([p for p in topic_parts if p]) or None
        return topic_text, [p for p in homework_parts if p]

    session = get_session(user_id)
    if not session:
        text = '❌ Спочатку увійдіть: /start'
        if hasattr(query_or_update, 'edit_message_text'):
            await query_or_update.edit_message_text(text)
        else:
            await query_or_update.message.reply_text(text)
        return

    try:
        r = scraper.post(
            f"{API_BASE}/v1/schedule/timetable",
            headers={"Authorization": f"Bearer {session['token']}"},
            json={
                "student_id": session['student_id'],
                "start_date": date,
                "end_date": date
            }
        )

        # Якщо токен застарів, оновлюємо
        if r.status_code == 401:
            new_session = await refresh_session(user_id)
            if new_session:
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/timetable",
                    headers={"Authorization": f"Bearer {new_session['token']}"},
                    json={
                        "student_id": new_session['student_id'],
                        "start_date": date,
                        "end_date": date
                    }
                )
            else:
                text = '❌ Сесія застаріла. Використайте /logout та /start'
                if hasattr(query_or_update, 'edit_message_text'):
                    await query_or_update.edit_message_text(text)
                else:
                    await query_or_update.message.reply_text(text)
                return

        # Получаем домашку из diary
        r_hw = scraper.post(
            f"{API_BASE}/v1/schedule/diary",
            headers={"Authorization": f"Bearer {session['token']}"},
            json={
                "student_id": session['student_id'],
                "start_date": date,
                "end_date": date
            }
        )

        if r_hw.status_code == 401:
            new_session = await refresh_session(user_id)
            if new_session:
                session = new_session
                r_hw = scraper.post(
                    f"{API_BASE}/v1/schedule/diary",
                    headers={"Authorization": f"Bearer {session['token']}"},
                    json={
                        "student_id": session['student_id'],
                        "start_date": date,
                        "end_date": date
                    }
                )

        # Собираем домашку по (предмет, номер урока) — чтобы не смешивать уроки одного предмета
        homework_dict = {}
        if r_hw.status_code == 200:
            hw_data = r_hw.json()
            for day in hw_data.get('dates', []):
                for call in day.get('calls', []):
                    call_num = call.get('call_number')
                    for subj in call.get('subjects', []):
                        name = subj.get('subject_name', 'Невідомо')
                        tasks = subj.get('hometask', []) or []
                        # Фильтруем мусор
                        topic_text, hw_parts = split_diary_tasks(tasks)
                        # Ключ = (предмет, номер урока)
                        key = (name, call_num)
                        if hw_parts:
                            # Накапливаем, а не перезаписываем
                            if key in homework_dict:
                                homework_dict[key] += ', ' + ', '.join(hw_parts)
                            else:
                                homework_dict[key] = ', '.join(hw_parts)

        if r.status_code == 200:
            data = r.json()

            # Форматування дати
            date_obj = datetime.strptime(date, '%Y-%m-%d')
            day_name = WEEKDAYS[date_obj.weekday()]

            message = f"📅 *{date_obj.strftime('%d.%m')}* • {day_name}\n\n"

            has_lessons = False
            for day in data.get('dates', []):
                for call in day.get('calls', []):
                    num = call.get('call_number')
                    time_start = call.get('time_start') or ''
                    time_end = call.get('time_end') or ''
                    for subj in call.get('subjects', []):
                        has_lessons = True
                        name = subj.get('subject_name', 'Невідомо')
                        room = subj.get('room', '') or (subj.get('classroom') or {}).get('name', '') or ''
                        room_number = re.sub(r'[^\d]', '', str(room)) if room else ''

                        # Компактный вывод в одну-две строки, всегда показываем 🚪
                        room_str = f" 🚪{room_number}" if room_number else " 🚪—"
                        message += f"`{num}.` *{time_start}* {name}{room_str}\n"

                        # ДЗ — показываем всегда
                        key = (name, num)
                        if key in homework_dict:
                            message += f"    📝 _{homework_dict[key]}_\n"
                        else:
                            message += "    📝 —\n"

            if not has_lessons:
                message = f"🌴 *{date_obj.strftime('%d.%m')}* • {day_name}\nУроків немає!"

            # Inline-кнопки с днями недели (компактно в один ряд)
            days_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Пн", callback_data="schedule:Понеділок"),
                InlineKeyboardButton("Вт", callback_data="schedule:Вівторок"),
                InlineKeyboardButton("Ср", callback_data="schedule:Середа"),
                InlineKeyboardButton("Чт", callback_data="schedule:Четвер"),
                InlineKeyboardButton("Пт", callback_data="schedule:П'ятниця")
            ]])

            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=days_kb)
            else:
                await query_or_update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=days_kb)
        else:
            text = f"❌ Не вдалось отримати розклад (код: {r.status_code})"
            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(text)
            else:
                await query_or_update.message.reply_text(text)

    except Exception as e:
        text = f"❌ Помилка: {e}"
        if hasattr(query_or_update, 'edit_message_text'):
            await query_or_update.edit_message_text(text)
        else:
            await query_or_update.message.reply_text(text)

async def homework_for_date(query_or_update, context: ContextTypes.DEFAULT_TYPE, date: str):
    """Отримує домашнє завдання на конкретну дату"""
    user_id = (query_or_update.from_user.id if hasattr(query_or_update, 'from_user')
               else query_or_update.effective_user.id)

    session = get_session(user_id)
    if not session:
        text = '❌ Спочатку увійдіть: /start'
        if hasattr(query_or_update, 'edit_message_text'):
            await query_or_update.edit_message_text(text)
        else:
            await query_or_update.message.reply_text(text)
        return

    try:
        r = scraper.post(
            f"{API_BASE}/v1/schedule/diary",
            headers={"Authorization": f"Bearer {session['token']}"},
            json={"student_id": session['student_id'], "start_date": date, "end_date": date}
        )

        if r.status_code == 401:
            new_session = await refresh_session(user_id)
            if new_session:
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/diary",
                    headers={"Authorization": f"Bearer {new_session['token']}"},
                    json={"student_id": new_session['student_id'], "start_date": date, "end_date": date}
                )
            else:
                text = '❌ Сесія застаріла. Використайте /logout та /start'
                if hasattr(query_or_update, 'edit_message_text'):
                    await query_or_update.edit_message_text(text)
                else:
                    await query_or_update.message.reply_text(text)
                return

        if r.status_code == 200:
            data = r.json()
            date_obj = datetime.strptime(date, '%Y-%m-%d')
            day_name = WEEKDAYS[date_obj.weekday()]
            message = f"📚 *Домашнє завдання на {date_obj.strftime('%d.%m.%Y')}* ({day_name})\n\n"

            has_homework = False
            for day in data.get('dates', []):
                for call in day.get('calls', []):
                    num = call.get('call_number')
                    time_start = call.get('time_start') or ''
                    time_end = call.get('time_end') or ''
                    for subj in call.get('subjects', []):
                        name = subj.get('subject_name', 'Невідомо')
                        tasks = subj.get('hometask', []) or []
                        tasks_filtered = [str(t).strip() for t in tasks if t and str(t).strip()]
                        if tasks_filtered:
                            has_homework = True
                            message += f"*{num}. {time_start}-{time_end}*\n"
                            message += f"📖 {name}\n"
                            hw_text = "\n".join(tasks_filtered)
                            message += f"ДЗ: {hw_text}\n\n"

            if not has_homework:
                message = f"✅ На {date_obj.strftime('%d.%m.%Y')} ({day_name}) домашки немає!"

            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(message, parse_mode=ParseMode.MARKDOWN)
            else:
                await query_or_update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        else:
            text = '❌ Не вдалось отримати домашку'
            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(text)
            else:
                await query_or_update.message.reply_text(text)

    except Exception as e:
        text = f"❌ Помилка: {e}"
        if hasattr(query_or_update, 'edit_message_text'):
            await query_or_update.edit_message_text(text)
        else:
            await query_or_update.message.reply_text(text)

# ============== СЕРЕДНІЙ БАЛ ==============

async def avg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує оцінки та середній бал"""
    try:
        print(f"[AVG] called by user={update.effective_user and update.effective_user.id} args={context.args}")
    except Exception:
        pass
    session = get_session(update.effective_user.id)

    # Immediate ack so user sees a response
    try:
        await update.message.reply_text("🔄 Завантажую дані...", quote=True)
    except Exception:
        pass

    try:
        print(f"[AVG] session for user {update.effective_user and update.effective_user.id}: {bool(session)}")
    except Exception:
        pass

    if not session:
        await update.message.reply_text("❌ Спочатку увійди: /start")
        return
    
    # Підтримка аргументів: /avg [--force-api] <start> [end] у форматі YYYY-MM-DD
    start_arg = None
    end_arg = None
    force_api = False
    args = list(context.args or [])
    # support flag anywhere in args
    if '--force-api' in args:
        force_api = True
        args = [a for a in args if a != '--force-api']

    if args:
        try:
            if len(args) >= 1:
                datetime.strptime(args[0], '%Y-%m-%d')
                start_arg = args[0]
            if len(args) >= 2:
                datetime.strptime(args[1], '%Y-%m-%d')
                end_arg = args[1]
        except Exception:
            await update.message.reply_text("❌ Неправильний формат дат. Використовуйте YYYY-MM-DD: `/avg 2025-08-21 2025-12-31`")
            return

    # Беремо оцінки з початку навчального року (1-го серпня/початок підготовки) — використовуємо Aug 1 як дефолт
    today = datetime.now()
    year = today.year
    aug1 = datetime(year, 8, 1)
    if today < aug1:
        aug1 = datetime(year - 1, 8, 1)

    default_start = aug1.strftime('%Y-%m-%d')
    start = start_arg or default_start
    end = end_arg or datetime.now().strftime('%Y-%m-%d')

    # валідація діапазону
    try:
        s_dt = datetime.strptime(start, '%Y-%m-%d')
        e_dt = datetime.strptime(end, '%Y-%m-%d')
        if e_dt < s_dt:
            await update.message.reply_text("❌ 'end' менша за 'start'. Перевірте порядок дат.")
            return
    except Exception:
        await update.message.reply_text("❌ Невірні дати")
        return

    try:
        last_exc = None
        # First, try to use the API response
        r = scraper.post(
            f"{API_BASE}/v1/schedule/student-performance",
            headers={"Authorization": f"Bearer {session['token']}"},
            json={
                "student_id": session['student_id'],
                "start_date": start,
                "end_date": end
            }
        )

        # Якщо токен застарів, оновлюємо
        if r.status_code == 401:
                print(f"[AVG] API returned 401, attempting refresh")
                new_session = await refresh_session(update.effective_user.id)
                if new_session:
                    r = scraper.post(
                        f"{API_BASE}/v1/schedule/student-performance",
                        headers={"Authorization": f"Bearer {new_session['token']}"},
                        json={
                            "student_id": new_session['student_id'],
                            "start_date": start,
                            "end_date": end
                        }
                    )
                else:
                    await update.message.reply_text("❌ Сесія застаріла. Використайте /logout та /start")
                    return

        # Initialize variables
        use_sources = None
        api_data = None
        used_api_due_to_html_failure = False
        total_api_marks = 0

        try:
            print(f"[AVG] API status: {r.status_code}")
            if r.status_code == 200:
                try:
                    api_preview = str(r.json())[:200]
                except Exception:
                    api_preview = str(r.text)[:200]
                print(f"[AVG] API preview: {api_preview}")
            else:
                print(f"[AVG] API response not OK: {r.status_code} - {str(r.text)[:200]}")
        except Exception as e:
            print(f"[AVG] Error inspecting API response: {e}")
        
        # Parse API data if status is 200
        if r.status_code == 200:
            try:
                api_data = r.json()
                # Count API marks
                total_api_marks = 0
                for subj in api_data.get('subjects', []):
                    total_api_marks += len(subj.get('marks', []) or [])
                # Prefer API when forced or when user provided specific dates
                if force_api or start_arg or end_arg:
                    use_sources = 'api'
                    print(f"[AVG] Using API (forced or date args): force_api={force_api}, start_arg={start_arg}, end_arg={end_arg}")
                elif total_api_marks >= 15:
                    use_sources = 'api'
                    print(f"[AVG] Using API (enough marks: {total_api_marks})")
                # If API returned empty result and no date args, try HTML as fallback
                elif total_api_marks == 0 and not (start_arg or end_arg):
                    use_sources = None  # Will try HTML
                    print(f"[AVG] API returned empty ({total_api_marks} marks), will try HTML fallback")
                else:
                    use_sources = None  # Will try HTML
                    print(f"[AVG] API has {total_api_marks} marks (< 15), will try HTML fallback")
            except Exception as e:
                print(f"[AVG] Error parsing API JSON: {e}")
                api_data = None

            # If API doesn't provide full history, try grades-statement HTML page
            grades_html = None
            # whether any per-mark dates were parsed from grades-statement tokens
            grades_html_any_dates = False
            if use_sources != 'api':
                print(f"[AVG] Attempting to load HTML grades-statement...")
                try:
                    # Build URL and params; the site accepts date_from/date_to query params
                    grades_url = f"https://nz.ua/schedule/grades-statement"
                    params = {'student_id': session['student_id']}
                    if start_arg:
                        params['date_from'] = start_arg
                    if end_arg:
                        params['date_to'] = end_arg

                    gresp = None
                    last_exc = None
                    headers = {'User-Agent': 'nz-bot/1.0 (+https://nz.ua)', 'Referer': grades_url}
                    for attempt in range(4):
                        try:
                            gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                            if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                grades_html = gresp.text
                                break
                        except Exception as exc:
                            last_exc = exc

                        # Try logging in and retry
                        try:
                            login_url = "https://nz.ua/login"
                            page = scraper.get(login_url, timeout=10, headers=headers)
                            csrf = None
                            from bs4 import BeautifulSoup
                            login_soup = BeautifulSoup(page.text, 'html.parser')
                            meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                            if meta_csrf:
                                csrf = meta_csrf.get('content')
                            hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                            if hidden_csrf and hidden_csrf.get('value'):
                                csrf = hidden_csrf.get('value')

                            login_data = {
                                "LoginForm[login]": session['username'],
                                "LoginForm[password]": session['password'],
                                "LoginForm[rememberMe]": "1"
                            }
                            lheaders = {'Referer': grades_url}
                            if csrf:
                                login_data['_csrf'] = csrf
                                lheaders['X-CSRF-Token'] = csrf

                            scraper.post(login_url, data=login_data, headers=lheaders, timeout=10)
                            # retry fetch after login
                            try:
                                gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                                if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                    grades_html = gresp.text
                                    break
                            except Exception as exc:
                                last_exc = exc
                        except Exception as exc:
                            last_exc = exc

                        time.sleep(1)

                    # final fallback: if grades-statement failed but we have API results, use API instead
                    if not grades_html and api_data and total_api_marks > 0:
                        use_sources = 'api'
                        used_api_due_to_html_failure = True
                        print(f"[AVG] HTML failed, falling back to API ({total_api_marks} marks)")
                    elif grades_html:
                        print(f"[AVG] HTML loaded successfully")
                    else:
                        print(f"[AVG] HTML loading failed")
                except Exception as e:
                    grades_html = None
                    print(f"[AVG] HTML loading exception: {e}")
            
            # If API was selected but returned empty, try HTML as fallback (if no date args)
            # This should not happen often since we set use_sources = None above when API is empty,
            # but handle it just in case
            if use_sources == 'api' and api_data and total_api_marks == 0 and not (start_arg or end_arg) and not force_api:
                print(f"[AVG] API was selected but empty, switching to HTML fallback")
                use_sources = None  # Will try HTML instead
                # Try to get HTML if we haven't already
                if not grades_html:
                    try:
                        grades_url = f"https://nz.ua/schedule/grades-statement"
                        params = {'student_id': session['student_id']}
                        headers = {'User-Agent': 'nz-bot/1.0 (+https://nz.ua)', 'Referer': grades_url}
                        gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                        if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                            grades_html = gresp.text
                            print(f"[AVG] HTML loaded in fallback attempt")
                    except Exception as e:
                        print(f"[AVG] HTML fallback exception: {e}")

            # choose source and parse
            parsed_range = (start, end)
            subjects_parsed = None
            if use_sources == 'api' and api_data and total_api_marks > 0:
                # build subjects from API
                parsed_range = (start, end)
                subjects_parsed = {}
                for subj in api_data.get('subjects', []):
                    name = subj.get('subject_name', '').strip()
                    marks = subj.get('marks', []) or []
                    if name:
                        # convert marks to strings/tokens
                        tokens = []
                        for m in marks:
                            if isinstance(m, (str, int, float)):
                                tokens.append(str(m))
                            else:
                                sig, disp = _extract_mark_info(m)
                                tokens.append(disp)
                        subjects_parsed[name] = tokens
            elif grades_html:
                print(f"[AVG] Parsing HTML grades-statement...")
                sd, ed, subs = parse_grades_from_html(grades_html)
                print(f"[AVG] HTML parsed: {len(subs)} subjects found, date range: {sd} - {ed}")
                
                # If no subjects found, log more details
                if not subs:
                    print(f"[AVG] WARNING: HTML parser returned 0 subjects!")
                    # Try to check if HTML contains the table
                    if 'marks-report' in grades_html:
                        print(f"[AVG] HTML contains 'marks-report' table")
                    if '<table' in grades_html:
                        print(f"[AVG] HTML contains table elements")
                    # Log first 500 chars of HTML for debugging
                    print(f"[AVG] HTML preview (first 500 chars): {grades_html[:500]}")
                
                # If user provided explicit dates, keep them; otherwise use the visible page range if present
                if not (start_arg or end_arg) and sd and ed:
                    parsed_range = (sd, ed)
                    # Also update the filter range to match HTML page range when no args provided
                    try:
                        s_dt = datetime.strptime(sd, '%Y-%m-%d')
                        e_dt = datetime.strptime(ed, '%Y-%m-%d')
                        print(f"[AVG] Using HTML page date range for filtering: {sd} - {ed}")
                    except Exception:
                        pass  # Keep original range

                # subs: {subject: [(token, date_iso_or_None), ...]}
                subjects_parsed = {}
                any_token_dates = False
                for name, toks in subs.items():
                    filtered = []
                    for tok_item in toks:
                        if isinstance(tok_item, (list, tuple)) and len(tok_item) >= 2:
                            tok_text, tok_date = tok_item[0], tok_item[1]
                        else:
                            tok_text, tok_date = str(tok_item), None

                        if tok_date:
                            any_token_dates = True
                            try:
                                dt = datetime.strptime(tok_date, '%Y-%m-%d')
                                if s_dt <= dt <= e_dt:
                                    filtered.append(tok_text)
                                else:
                                    # outside requested range -> skip
                                    pass
                            except Exception:
                                # if we can't parse, include it
                                filtered.append(tok_text)
                        else:
                            # no per-mark date available -> can't filter reliably, include
                            # When no date args provided, include all marks from HTML
                            if not (start_arg or end_arg):
                                filtered.append(tok_text)
                            else:
                                # When date args provided but no per-mark dates, include anyway
                                # (HTML page should already be filtered by date_from/date_to params)
                                filtered.append(tok_text)

                    if filtered:
                        subjects_parsed[name] = filtered
                        print(f"[AVG] Subject '{name}': {len(filtered)} marks after filtering")

                # remember whether we had any per-mark dates for post-processing note
                grades_html_any_dates = any_token_dates
                print(f"[AVG] After filtering by date range: {len(subjects_parsed)} subjects with marks")

            if not subjects_parsed:
                print(f"[AVG] No subjects parsed from any source")
                # Check if API was used but returned empty
                if use_sources == 'api' and api_data and total_api_marks == 0:
                    err_msg = '❌ Не знайдено оцінок'
                    if start_arg or end_arg:
                        err_msg += f' за вказаний період ({start} — {end})'
                    elif force_api:
                        err_msg += ' (API повернув порожній результат)'
                    else:
                        err_msg += ' за поточний навчальний рік'
                    if not (start_arg or end_arg) and not force_api:
                        err_msg += '\n\n💡 Спробуйте вказати конкретний діапазон дат:\n`/avg 2025-12-19 2025-12-31`'
                    await update.message.reply_text(err_msg)
                    return
                
                # fallback response
                err_msg = '❌ Не вдалось отримати оцінки'
                if start_arg or end_arg:
                    err_msg += f' за вказаний період ({start} — {end})'
                else:
                    err_msg += ' (немає оцінок за поточний навчальний рік)'
                try:
                    if last_exc:
                        err_msg += f"\n_Деталі: {str(last_exc)}_"
                except Exception:
                    pass
                if not (start_arg or end_arg):
                    err_msg += '\n\n💡 Спробуйте вказати конкретний діапазон дат:\n`/avg 2025-12-19 2025-12-31`'
                err_msg += '\nАбо спробуйте `/avg --force-api`'
                await update.message.reply_text(err_msg)
                return

            # compute averages from subjects_parsed
            message = f"📅 Діапазон дат: {parsed_range[0]} — {parsed_range[1]}\n\n📊 Середній бал по предметам:\n\n"
            total = 0.0
            count = 0
            subjects_data = []

            for name, tokens in subjects_parsed.items():
                subj_numeric_sum = 0.0
                subj_numeric_count = 0
                subj_non_numeric = {}
                for tok in tokens:
                    val = _extract_numeric_from_mark(tok)
                    if val is not None:
                        subj_numeric_sum += val
                        subj_numeric_count += 1
                        total += val
                        count += 1
                    else:
                        subj_non_numeric[tok] = subj_non_numeric.get(tok, 0) + 1

                if subj_numeric_count > 0:
                    avg_mark = subj_numeric_sum / subj_numeric_count
                    subjects_data.append({'name': name, 'avg': avg_mark, 'count': subj_numeric_count})
                else:
                    if len(tokens) == 0:
                        subjects_data.append({'name': name, 'avg': None, 'count': 0, 'note': 'нема оцінок'})
                    else:
                        tokens_sorted = sorted(subj_non_numeric.items(), key=lambda x: -x[1])
                        tokens_summary = ', '.join([t[0] for t in tokens_sorted[:3]])
                        subjects_data.append({'name': name, 'avg': None, 'count': len(tokens), 'note': f'ненумерічні оцінки: {tokens_summary}'})

            # Sort numeric subjects by avg desc, then non-numeric/empty at the bottom
            numeric = [s for s in subjects_data if s.get('avg') is not None]
            nonnum = [s for s in subjects_data if s.get('avg') is None]
            numeric.sort(key=lambda x: x['avg'], reverse=True)

            lines = []
            for s in numeric + nonnum:
                if s.get('avg') is not None:
                    lines.append(f"{s['name']}: {s['avg']:.2f} ({s['count']} оцінок)")
                else:
                    if s.get('note'):
                        lines.append(f"{s['name']}: — ({s['note']})")
                    else:
                        lines.append(f"{s['name']}: — (нема оцінок)")

            message += "\n".join(lines)

            if count > 0:
                overall = total / count
                message += f"\n\n📈 *Загальний середній: {overall:.2f}*"
            else:
                message += "\n\n📈 *Загальний середній: —*"

            # Відправляємо результат (без указания источника данных)

            # If using grades-statement as fallback and user asked for a specific range, warn when per-mark dates are missing
            try:
                if use_sources != 'api' and grades_html and (start_arg or end_arg) and not grades_html_any_dates:
                    message += "\n\n_Примітка: у даних grades-statement немає дат для окремих оцінок, тому показані всі наявні оцінки за видимий період._"
            except Exception:
                pass

            await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")

# ============== НОВИНИ ==============

def parse_news_from_html(html: str) -> list:
    """Парсить новини з HTML сторінки NZ.UA"""
    news_items = []
    
    # Шукаємо блок "Мої новини"
    if 'Мої новини' not in html:
        return []
    
    # Витягуємо текст після "Мої новини"
    start_idx = html.find('Мої новини')
    end_idx = html.find('Показано новин')
    
    if end_idx == -1:
        news_section = html[start_idx:]
    else:
        news_section = html[start_idx:end_idx]
    
    # Патерн для парсингу різних типів новин
    # 1. Звичайні оцінки: "Ім'я Прізвище Побатькові ІмяПрізвищеПобатькові 19 грудня о 10:06 Ви отримали оцінку 7 з предмету: Німецька мова, Семестрова"
    pattern1 = r'([А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+)\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+(\d+\s+[а-яіїєґʼ]+\s+о\s+\d+:\d+)\s+(Ви отримали оцінку\s+[\wА-ЯІЇЄҐа-яіїєґ/]+\s+з предмету:\s+[^,]+,\s+[^\n]+)'
    
    # 2. Зміна оцінки: "Оцінка змінена на 7 з предмету: ..."
    pattern2 = r'([А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+)\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+[А-ЯІЇЄҐ][а-яіїєґʼ]+\s+(\d+\s+[а-яіїєґʼ]+\s+о\s+\d+:\d+)\s+(Оцінка змінена на\s+[\wА-ЯІЇЄҐа-яіїєґ/]+\s+з предмету:\s+[^,]+,\s+[^\n]+)'
    
    # Шукаємо всі співпадіння
    for pattern in [pattern1, pattern2]:
        matches = re.finditer(pattern, news_section)
        
        for match in matches:
            teacher = match.group(1).strip()
            date_time = match.group(2).strip()
            full_message = match.group(3).strip()
            
            # Парсимо оцінку, предмет та тип
            if 'Ви отримали оцінку' in full_message:
                grade_match = re.search(r'оцінку\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)', full_message)
            elif 'Оцінка змінена на' in full_message:
                grade_match = re.search(r'змінена на\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)', full_message)
            else:
                continue
            
            if grade_match:
                grade = grade_match.group(1).strip()
                subject = grade_match.group(2).strip()
                grade_type = grade_match.group(3).strip()
                
                news_items.append({
                    'teacher': teacher,
                    'date': date_time,
                    'grade': grade,
                    'subject': subject,
                    'type': grade_type,
                    'is_changed': 'Оцінка змінена' in full_message
                })
    
    return news_items

def format_grade_type(grade_type):
    """Форматирует тип оценки"""
    grade_type_lower = grade_type.lower()
    if 'поточна' in grade_type_lower:
        return "Поточна оцінка"
    elif 'тематична' in grade_type_lower:
        return f"за тематичну"
    elif 'семестрова' in grade_type_lower:
        return "семестрова"
    elif 'зошит' in grade_type_lower or 'зош' in grade_type_lower:
        return "за зошит"
    elif 'контрольна' in grade_type_lower or 'к/р' in grade_type_lower:
        return "за контрольну роботу"
    elif 'практичне' in grade_type_lower or 'пр/р' in grade_type_lower:
        return "за практичне заняття"
    else:
        return f"за {grade_type.lower()}"

def format_news_message(news_items: list) -> str:
    """Форматує новини для відображення"""
    if not news_items:
        return "📰 Новин поки немає"
    
    message = "📰 *НОВИНИ*\n\n"
    
    for item in news_items[:10]:
        # Форматируем имя учителя (сокращаем)
        teacher_name = item.get('teacher', '')
        if teacher_name:
            name_parts = teacher_name.split()
            if len(name_parts) >= 3:
                short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}."
            elif len(name_parts) == 2:
                short_name = f"{name_parts[0]} {name_parts[1][0]}."
            else:
                short_name = teacher_name
        else:
            short_name = "—"
        
        date_str = item.get('date', '')
        grade = item.get('grade', '')
        subject = item.get('subject', '')
        grade_type = item.get('type', '')
        formatted_type = format_grade_type(grade_type)
        
        # Форматуємо повідомлення
        if item.get('is_changed'):
            message += f"• {short_name} - {date_str}, змінила Вам оцінку на \"{grade}\" з \"{subject}\", {formatted_type}\n\n"
        else:
            message += f"• {short_name} - {date_str}, поставила Вам оцінку \"{grade}\" з \"{subject}\", {formatted_type}\n\n"
    
    if len(news_items) > 10:
        message += f"_...та ще {len(news_items) - 10} новин_"
    
    return message

async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує новини з NZ.UA"""
    session = get_session(update.effective_user.id)
    if not session:
        await update.message.reply_text("❌ Спочатку увійди: /start")
        return

    msg = await update.message.reply_text("🔄 Завантажую новини...")

    try:
        from bs4 import BeautifulSoup

        login_url = "https://nz.ua/login"

        # Спроба: спочатку отримати сторінку логіну і витягти CSRF токен
        try:
            login_page = scraper.get(login_url)
            login_soup = BeautifulSoup(login_page.text, "html.parser")
            csrf = None
            meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
            if meta_csrf:
                csrf = meta_csrf.get('content')
            hidden_csrf = login_soup.find('input', {'name': '_csrf'})
            if hidden_csrf and hidden_csrf.get('value'):
                csrf = hidden_csrf.get('value')

            if csrf:
                print(f"[NEWS] Found CSRF token")
            else:
                print(f"[NEWS] CSRF token not found on login page")
        except Exception as e:
            print(f"[NEWS] Could not fetch login page: {e}")
            csrf = None

        # Підготовка даних для логіну
        login_data = {
            "LoginForm[login]": session['username'],
            "LoginForm[password]": session['password'],
            "LoginForm[rememberMe]": "1"
        }
        headers = {}
        if csrf:
            login_data['_csrf'] = csrf
            headers['X-CSRF-Token'] = csrf

        # Виконуємо логін (спробуємо один раз, потім перевіримо сторінку новин)
        r_login = scraper.post(login_url, data=login_data, headers=headers)
        print(f"[NEWS] Login status: {r_login.status_code}, URL after login: {r_login.url}")
        try:
            print("[NEWS] Cookies after login:", scraper.cookies.get_dict())
        except Exception:
            pass

        # Список endpoint'ів які варто спробувати
        endpoints = ["/dashboard/news", "/dashboard", "/news", "/site/news"]
        base_url = "https://nz.ua"
        news_resp = None

        for ep in endpoints:
            url = urljoin(base_url, ep)
            try:
                resp = scraper.get(url)
                print(f"[NEWS] GET {url} -> {resp.status_code}")
                if resp.status_code == 200 and 'Мої новини' in resp.text or 'school-news-list' in resp.text:
                    news_resp = resp
                    break
                # keep last 200 response for debugging
                if resp.status_code == 200 and news_resp is None:
                    news_resp = resp
            except Exception as e:
                print(f"[NEWS] Error fetching {url}: {e}")

        if not news_resp:
            await msg.edit_text('❌ Не вдалось отримати сторінку новин (мережна помилка)')
            return

        # Парсимо HTML і шукаємо блок новин
        soup = BeautifulSoup(news_resp.text, "html.parser")
        root = soup.find("div", id="school-news-list")

        # Якщо блоку немає — спробуємо парсити текстовий варіант (функція parse_news_from_html)
        if not root:
            print("[NEWS] Container 'school-news-list' not found, falling back to regex parser")
            parsed = parse_news_from_html(news_resp.text)
            if parsed:
                await update.message.reply_text(format_news_message(parsed))
                return

            await msg.edit_text('📰 Новин поки немає або не вдалось увійти на сайт (перевірте лог на сервері)')
            return

        items = root.select("div.news-page__item")
        if not items:
            await msg.edit_text('📰 Новин поки немає')
            return

        out_lines = []
        base = "https://nz.ua"
        limit = 10

        for item in items[:limit]:
            name_el = item.select_one(".news-page__header .news-page__name")
            date_el = item.select_one(".news-page__header .news-page__date")
            desc_el = item.select_one(".news-page__desc")

            name = name_el.get_text(strip=True) if name_el else "—"
            date = date_el.get_text(strip=True) if date_el else ""

            text = ""
            text_raw = ""  # Неэкранированный текст для поиска паттернов
            if desc_el:
                for br in desc_el.find_all("br"):
                    br.replace_with("\n")
                # беремо HTML фрагмент для збереження лінків, але ескейпимо текст
                inner_html = ''.join(str(x) for x in desc_el.contents)
                text_raw = BeautifulSoup(inner_html, "html.parser").get_text(" ", strip=True)
                text = html.escape(text_raw)
                link_tag = desc_el.find("a", href=True)
                if link_tag:
                    link = urljoin(base, link_tag["href"])
                    text = text.replace(
                        "Дистанційне завдання",
                        f'<a href="{html.escape(link)}">Дистанційне завдання</a>'
                    )

            # Форматируем имя учителя (сокращаем)
            name_parts = name.split()
            if len(name_parts) >= 3:
                short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}."
            elif len(name_parts) == 2:
                short_name = f"{name_parts[0]} {name_parts[1][0]}."
            else:
                short_name = name
            
            # Используем неэкранированный текст для поиска паттернов
            search_text = text_raw if text_raw else text
            
            # Ищем паттерн "Ви отримали оцінку X з предмету: Y, Z"
            grade_pattern = r'Ви отримали оцінку\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)'
            match = re.search(grade_pattern, search_text)
            if match:
                grade = match.group(1)
                subject = match.group(2).strip()
                grade_type = match.group(3).strip()
                formatted_type = format_grade_type(grade_type)
                formatted_text = f"{short_name} - {date}, поставила Вам оцінку \"{grade}\" з \"{subject}\", {formatted_type}"
                out_lines.append(f"• {formatted_text}".strip())
            else:
                # Ищем паттерн "Оцінка змінена на X з предмету: Y, Z"
                changed_pattern = r'Оцінка змінена на\s+([\wА-ЯІЇЄҐа-яіїєґ/]+)\s+з предмету:\s+([^,]+),\s+(.+)'
                match_changed = re.search(changed_pattern, search_text)
                if match_changed:
                    grade = match_changed.group(1)
                    subject = match_changed.group(2).strip()
                    grade_type = match_changed.group(3).strip()
                    formatted_type = format_grade_type(grade_type)
                    formatted_text = f"{short_name} - {date}, змінила Вам оцінку на \"{grade}\" з \"{subject}\", {formatted_type}"
                    out_lines.append(f"• {formatted_text}".strip())
                else:
                    # Для других новостей используем старый формат
                    out_lines.append(f"• *{html.escape(name)}* — {html.escape(date)}\n{text}".strip())

        result = "📰 *НОВИНИ*\n\n" + "\n\n".join(out_lines)
        if len(items) > limit:
            result += f"\n\n_...та ще {len(items) - limit} новин_"

        await msg.edit_text(result, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    except ImportError:
        await msg.edit_text("❌ Потрібно встановити BeautifulSoup: pip install beautifulsoup4")
    except Exception as e:
        await msg.edit_text(f"❌ Помилка при отриманні новин: {e}")
        print(f"[NEWS ERROR] {e}")
        import traceback
        traceback.print_exc()

# ============== ІНШІ КОМАНДИ ==============

async def vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує VIP-меню (тот же функционал, что и кнопка VIP)"""
    await vip_menu_cmd(update, context)

async def vip_request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ініціює заявку на VIP: просить користувача надіслати повідомлення"""
    await update.message.reply_text(
        "✉️ Напишіть коротке повідомлення для заявки на VIP (наприклад: 'Хочу VIP на 30 днів, мій Telegram: @user')"
    )
    context.user_data['step'] = 'vip_request' 

async def list_tickets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list_tickets - показує останні звернення (тільки для адмінів)

    Використання: /list_tickets [open|closed|all]
    По замовчуванню показує тільки open
    """
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміни можуть переглядати список звернень")
        return

    state = 'open'
    if context.args:
        arg = context.args[0].lower()
        if arg in ('open','closed','all'):
            state = arg
        else:
            await update.message.reply_text("❌ Невідомий фільтр. Використовуйте: open|closed|all")
            return

    conn = get_db_connection()
    c = conn.cursor()
    if state == 'open':
        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets WHERE COALESCE(status,'open') = 'open' ORDER BY created_at DESC LIMIT 200")
    elif state == 'closed':
        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets WHERE COALESCE(status,'open') = 'closed' ORDER BY created_at DESC LIMIT 200")
    else:
        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets ORDER BY created_at DESC LIMIT 200")

    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Звернень поки немає")
        return

    lines = []
    kb_buttons = []
    for r in rows:
        tid, uid, snip, created = r
        lines.append(f"#{tid} — {uid} — {created} — {snip}")
        kb_buttons.append([InlineKeyboardButton(f"Тикет #{tid}", callback_data=f"admin:view_ticket:{tid}")])

    text = f"📭 Останні звернення ({state}):\n\n" + "\n".join(lines)
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_buttons))


async def vip_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує VIP-меню (тільки для активних VIP)"""
    user_id = update.effective_user.id
    if not is_vip_user(user_id):
        await update.message.reply_text(VIP_TEXT + "\n\n💡 Щоб стати VIP — надішліть заявку через /vip_request")
        return

    # Получаем информацию о VIP статусе
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT expires_at FROM vip_users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    expires_text = "Не встановлено"
    if row and row[0]:
        try:
            expires = datetime.fromisoformat(row[0])
            expires_text = expires.strftime('%d.%m.%Y %H:%M')
        except:
            expires_text = str(row[0])

    def build_keyboard(uid):
        s = get_all_vip_settings(uid)
        def status(k, default='1'):
            return s.get(k, default) == '1'
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔔 Нагадування: {'✅' if status('reminders') else '❌'}", callback_data=f"vip:toggle:reminders")],
            [InlineKeyboardButton(f"📬 Оповіщення про оцінки: {'✅' if status('grade_notifications') else '❌'}", callback_data=f"vip:toggle:grade_notifications")],
            [InlineKeyboardButton("🎯 Аналітика успішності", callback_data="vip:analytics")],
            [InlineKeyboardButton("📄 Експорт даних", callback_data="vip:export")],
            [InlineKeyboardButton("📑 PDF-звіт про успішність", callback_data="vip:pdf_report")],
            [InlineKeyboardButton("⚙️ Налаштування", callback_data="vip:settings")],
            [InlineKeyboardButton("ℹ️ Інформація", callback_data="vip:info")]
        ])
        return kb

    text = f"🎁 *Free VIP*\n\n"
    text += f"📅 Діє до: `{expires_text}`\n\n"
    text += "Оберіть опцію:"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_keyboard(user_id))


async def admin_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує адмінське меню (тільки для адмінів)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміністратори можуть користуватися цим меню")
        return

    # Получаем статистику
    conn = get_db_connection()
    c = conn.cursor()
    
    # Статистика пользователей
    c.execute('SELECT COUNT(DISTINCT user_id) FROM sessions')
    total_users = c.fetchone()[0] or 0
    
    # Статистика VIP
    c.execute('SELECT COUNT(*) FROM vip_users WHERE expires_at > ?', (datetime.now().isoformat(),))
    active_vips = c.fetchone()[0] or 0
    
    # Статистика тикетов
    c.execute("SELECT COUNT(*) FROM support_tickets WHERE COALESCE(status,'open') = 'open'")
    open_tickets = c.fetchone()[0] or 0
    
    # Статистика заявок на VIP
    c.execute('SELECT COUNT(*) FROM vip_requests')
    vip_requests = c.fetchone()[0] or 0
    
    conn.close()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_menu:stats")],
        [InlineKeyboardButton("📭 Звернення", callback_data="admin_menu:list_tickets")],
        [InlineKeyboardButton("👥 VIP-користувачі", callback_data="admin_menu:list_vips")],
        [InlineKeyboardButton("📋 Заявки на VIP", callback_data="admin_menu:vip_requests")],
        [InlineKeyboardButton("▶️ Запустити: Нагадування", callback_data="admin_menu:run_reminders"), InlineKeyboardButton("▶️ Запустити: Оцінки", callback_data="admin_menu:run_grades")],
        [InlineKeyboardButton("🗂️ Лог дій", callback_data="admin_menu:view_actions")],
        [InlineKeyboardButton("⚙️ Управління", callback_data="admin_menu:management")],
        [InlineKeyboardButton("📢 Написати оповіщення всім юзерам", callback_data="admin_menu:broadcast")]
    ])

    stats_text = f"🛠️ *Адмінське меню*\n\n"
    stats_text += f"📊 *Статистика:*\n"
    stats_text += f"👤 Користувачів: {total_users}\n"
    stats_text += f"⭐ VIP активних: {active_vips}\n"
    stats_text += f"📭 Відкритих тикетів: {open_tickets}\n"
    stats_text += f"📋 Заявок на VIP: {vip_requests}\n\n"
    stats_text += "Оберіть дію:"

    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def vip_actions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує останні дії адміністраторів (тільки для адмінів)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміни можуть переглядати лог дій")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT id, admin_id, action, target_user, ticket_id, details, created_at FROM admin_actions ORDER BY created_at DESC LIMIT 50')
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("ℹ️ Записів дій адміністраторів поки немає")
        return

    lines = []
    for r in rows:
        aid, admin_id, action, target_user, ticket_id, details, created = r
        parts = [f"#{aid}", f"admin:{admin_id}", action]
        if target_user:
            parts.append(f"user:{target_user}")
        if ticket_id:
            parts.append(f"ticket:{ticket_id}")
        if details:
            parts.append(details)
        parts.append(str(created))
        lines.append(" — ".join(parts))

    text = "🗂️ Останні дії адміністраторів:\n\n" + "\n".join(lines)
    await update.message.reply_text(text)


async def report_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для отримання табеля успішності"""
    user_id = update.effective_user.id
    session = get_session(user_id)
    
    if not session:
        await update.message.reply_text("❌ Спочатку увійдіть: /start")
        return
    
    msg = await update.message.reply_text("🔄 Завантажую табель...")
    
    try:
        from bs4 import BeautifulSoup
        
        login_url = "https://nz.ua/login"
        headers = {'User-Agent': 'nz-bot/1.0'}
        
        login_page = scraper.get(login_url, headers=headers)
        login_soup = BeautifulSoup(login_page.text, "html.parser")
        
        csrf = None
        meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
        if meta_csrf:
            csrf = meta_csrf.get('content')
        hidden_csrf = login_soup.find('input', {'name': '_csrf'})
        if hidden_csrf and hidden_csrf.get('value'):
            csrf = hidden_csrf.get('value')
        
        login_data = {
            "LoginForm[login]": session['username'],
            "LoginForm[password]": session['password'],
            "LoginForm[rememberMe]": "1"
        }
        if csrf:
            login_data['_csrf'] = csrf
            headers['X-CSRF-Token'] = csrf
        
        scraper.post(login_url, data=login_data, headers=headers)
        
        report_url = "https://nz.ua/schedule/report-card"
        report_resp = scraper.get(report_url, headers=headers)
        
        if report_resp.status_code != 200 or 'Табель' not in report_resp.text:
            await msg.edit_text("❌ Не вдалося завантажити табель. Спробуйте пізніше.")
            return
        
        results = parse_report_card(report_resp.text)
        
        if not results:
            await msg.edit_text("📋 Табель порожній або не знайдено предметів.")
            return
        
        lines = ["📋 *Табель успішності*\n"]
        lines.append("```")
        
        for item in results:
            subject = item['subject']
            grade = item['semester_1']
            if len(subject) > 30:
                subject = subject[:27] + "..."
            lines.append(f"{subject}: {grade}")
        
        lines.append("```")
        
        with_grades = [r for r in results if r['semester_1'] != 'немає']
        if with_grades:
            avg_grade = sum(int(r['semester_1']) for r in with_grades) / len(with_grades)
            lines.append(f"\n📊 Середній бал: *{avg_grade:.2f}*")
        
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        print(f"[REPORT_CARD] Error: {e}")
        await msg.edit_text(f"❌ Помилка: {e}")


async def diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /diary - розклад"""
    await show_weekday_keyboard(update, context, kind='schedule')
async def homework_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /homework - домашнє завдання"""
    await show_weekday_keyboard(update, context, kind='homework')

async def policy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /policy - політика конфіденційності"""
    await update.message.reply_text(POLICY_TEXT, parse_mode=ParseMode.MARKDOWN)

async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /support - підтримка"""
    await update.message.reply_text(
        "✉️ *Підтримка*\n\n"
        "Напишіть повідомлення — ми отримаємо його.",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['step'] = 'support'

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /logout - вихід"""
    delete_session_from_db(update.effective_user.id)
    context.user_data.clear()
    
    await update.message.reply_text(
        "👋 Ви вийшли з системи.\n\n"
        "Ваші дані видалено з бота.\n"
        "Щоб увійти знову, використайте /start"
    )

async def grant_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Адмін команда: /grant_vip <user_id or reply> [days]"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміни можуть виконувати цю команду")
        return

    target_id = None
    days = 30
    # Якщо є аргументи
    if context.args:
        try:
            target_id = int(context.args[0])
            if len(context.args) > 1:
                days = int(context.args[1])
        except Exception:
            await update.message.reply_text("❌ Неправильні аргументи. Використання: /grant_vip <user_id> [days]")
            return
    elif update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    else:
        await update.message.reply_text("❌ Вкажіть ID користувача або використайте як відповідь на повідомлення")
        return

    grant_vip(target_id, days)
    log_admin_action(update.effective_user.id, 'grant_vip', target_user=target_id, details=f'days={days}')
    await update.message.reply_text(f"✅ VIP надано користувачу {target_id} на {days} днів")
    try:
        await context.bot.send_message(target_id, f"✨ Вам було надано VIP на {days} днів!")
    except Exception:
        pass

async def revoke_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Адмін команда: /revoke_vip <user_id or reply>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміни можуть виконувати цю команду")
        return

    target_id = None
    if context.args:
        try:
            target_id = int(context.args[0])
        except Exception:
            await update.message.reply_text("❌ Неправильний ID")
            return
    elif update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    else:
        await update.message.reply_text("❌ Вкажіть ID користувача або використайте як відповідь на повідомлення")
        return

    revoke_vip(target_id)
    log_admin_action(update.effective_user.id, 'revoke_vip', target_user=target_id)
    await update.message.reply_text(f"✅ VIP скасовано для користувача {target_id}")
    try:
        await context.bot.send_message(target_id, f"⚠️ Ваш VIP був скасований адміністратором.")
    except Exception:
        pass

async def ticket_close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Адмін команда: /ticket_close <ticket_id> [note]"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Тільки адміни можуть закривати тикети")
        return

    if not context.args:
        await update.message.reply_text("❌ Використання: /ticket_close <ticket_id> [примітка]")
        return

    try:
        ticket_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Неправильний ID тикета")
        return

    note = ' '.join(context.args[1:]) if len(context.args) > 1 else None
    t = get_ticket(ticket_id)
    if not t:
        await update.message.reply_text('❌ Тикет не знайдено')
        return

    resolved = resolve_ticket_db(ticket_id, update.effective_user.id, note)
    log_admin_action(update.effective_user.id, 'resolve_ticket', ticket_id=ticket_id, details=note)
    await update.message.reply_text(f"✅ Тикет #{ticket_id} помічено як вирішений")
    try:
        await context.bot.send_message(t['user_id'], f"✅ Ваше звернення #{ticket_id} було позначено як вирішене адміністратором.\nПримітка: {note or '—'}")
    except Exception:
        pass

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help - допомога"""
    help_text = (
        "📖 *Довідка NZ.UA Bot*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 *КНОПКИ МЕНЮ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📅 *Розклад* — переглянути розклад уроків на сьогодні, завтра або будь-який день тижня. Показує предмети, час, кабінети та домашні завдання.\n\n"
        "📋 *Табель* — табель успішності з оцінками за 1 семестр. Показує всі предмети та середній бал.\n\n"
        "📰 *Новини* — останні новини зі шкільного щоденника: оцінки, зауваження, оголошення від вчителів.\n\n"
        "📊 *Середній бал* — розрахунок середнього балу за вказаний період або за весь навчальний рік.\n\n"
        "🎁 *Free VIP* — безкоштовні VIP-функції: нагадування про уроки, сповіщення про нові оцінки, аналітика успішності.\n\n"
        "✉️ *Підтримка* — зв\'язок з розробником бота для питань та пропозицій.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ *КОМАНДИ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "`/start` — головне меню\n"
        "`/help` — ця довідка\n"
        "`/diary` — розклад уроків\n"
        "`/news` — новини\n"
        "`/avg` — середній бал\n"
        "`/vip` — VIP-меню\n"
        "`/support` — підтримка\n"
        "`/logout` — вийти з акаунту\n"
        "`/policy` — політика конфіденційності\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *ПІДКАЗКИ*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• Для розрахунку середнього балу за період надішліть дати у форматі:\n"
        "  `10.12.2025 20.12.2025`\n\n"
        "• Бот автоматично оновлює дані з NZ.UA при кожному запиті\n\n"
        "• VIP-користувачі отримують сповіщення про нові оцінки та нагадування про уроки\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 *БЕЗПЕКА*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Ваші дані зберігаються в зашифрованому вигляді та використовуються виключно для роботи з NZ.UA. Детальніше: /policy"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

# ============== ОБРОБКА КНОПОК ==============

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник кнопок з клавіатури"""
    try:
        print(f"[BUTTON] from={update.effective_user and update.effective_user.id} text={getattr(update.message, 'text', None)}")
    except Exception:
        pass
    text = update.message.text

    if text == "📅 Розклад":
        # Сразу показываем расписание на сегодня с кнопками дней
        today = datetime.now()
        weekday = today.weekday()
        
        if weekday >= 5:  # Субота або Неділя
            await update.message.reply_text(
                f"🌴 *{WEEKDAYS[weekday]}* — вихідний",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Пн", callback_data="schedule:Понеділок"),
                        InlineKeyboardButton("Вт", callback_data="schedule:Вівторок"),
                        InlineKeyboardButton("Ср", callback_data="schedule:Середа"),
                        InlineKeyboardButton("Чт", callback_data="schedule:Четвер"),
                        InlineKeyboardButton("Пт", callback_data="schedule:П'ятниця")
                    ]
                ])
            )
        else:
            await schedule_for_date(update, context, today.strftime('%Y-%m-%d'))
    elif text == "📚 Домашка":
        # Убрали отдельную кнопку, теперь только через Розклад
        await show_weekday_keyboard(update, context, kind='schedule')
    elif text == "📊 Середній бал":
        # Показываем интерактивное меню для среднего бала
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 За весь навчальний рік", callback_data="avg:full_year")],
            [InlineKeyboardButton("📅 Вказати діапазон дат", callback_data="avg:custom_dates")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="avg:cancel")]
        ])
        await update.message.reply_text(
            "📊 *Середній бал*\n\n"
            "Оберіть опцію:\n\n"
            "💡 _Або просто надішліть дати у форматі:_\n"
            "`10.12.2025 20.12.2025`\n"
            "або\n"
            "`05.10.2025 25.11.2025`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
    elif text == "📋 Табель":
        await report_card_cmd(update, context)
    elif text == "📰 Новини":
        await news_cmd(update, context)
    elif text == "🎁 Free VIP" or text == "⭐️ VIP":
        await vip_menu_cmd(update, context)
    elif text == "✉️ Підтримка":
        await support_cmd(update, context)
    elif text == "🛠 Админ-меню":
        if is_admin(update.effective_user.id):
            await admin_menu_cmd(update, context)
        else:
            await update.message.reply_text("❌ Тільки для адміністраторів")
    else:
        await update.message.reply_text("❓ Не знаю такої кнопки. Використайте /help для довідки.")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник callback-запитів (інлайн кнопки)"""
    query = update.callback_query

    # Safe answer to avoid crashing when query is too old
    async def _safe_answer(q, text=None, show_alert=False):
        try:
            await q.answer(text=text, show_alert=show_alert)
        except BadRequest as e:
            # Ignore 'Query is too old' and similar transient errors
            msg = str(e)
            if 'Query is too old' in msg or 'query id is invalid' in msg or 'response timeout' in msg:
                print(f"[CALLBACK] Ignored BadRequest while answering callback: {msg}")
                return
            else:
                print(f"[CALLBACK] BadRequest while answering callback: {msg}")
                return
        except Exception as e:
            print(f"[CALLBACK] Unexpected error answering callback: {e}")
            return

    await _safe_answer(query)

    data = query.data
    callback_data = data

    # Обработка callback для среднего бала
    if callback_data and callback_data.startswith('avg:'):
        avg_action = callback_data.split(':', 1)[1]
        user_id = query.from_user.id
        
        if avg_action == 'full_year':
            # Вызываем avg без аргументов (за весь учебный год)
            # Создаем временный Update объект для вызова avg
            class TempUpdate:
                def __init__(self, user_id, message):
                    self.effective_user = type('obj', (object,), {'id': user_id})()
                    self.message = message
            temp_update = TempUpdate(user_id, query.message)
            context.args = []
            # Вызываем функцию avg напрямую (она определена в этом же модуле)
            # Используем globals() чтобы получить доступ к функции
            avg_func = globals()['avg']
            await avg_func(temp_update, context)
            await query.answer()
            return
        elif avg_action == 'custom_dates':
            await query.edit_message_text(
                "*📅 Вкажіть діапазон дат*\n\n"
                "Надішліть дати у форматі:\n"
                "`10.12.2025 20.12.2025`\n"
                "або\n"
                "`05.10.2025 25.11.2025`",
                parse_mode=ParseMode.MARKDOWN
            )
            await query.answer()
            return
        elif avg_action == 'cancel':
            await query.edit_message_text("❌ Скасовано")
            await query.answer()
            return

    # VIP callbacks (toggle settings, analytics, export, etc.)
    if data and data.startswith('vip:'):
        parts = data.split(':')
        action = parts[1] if len(parts) > 1 else None
        user_id = query.from_user.id
        
        if not is_vip_user(user_id):
            await _safe_answer(query, text='Тільки VIP-користувачі можуть використовувати ці функції', show_alert=True)
            return
        
        # Получаем информацию о VIP статусе для меню
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT expires_at FROM vip_users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        expires_text = "Не встановлено"
        if row and row[0]:
            try:
                expires = datetime.fromisoformat(row[0])
                expires_text = expires.strftime('%d.%m.%Y %H:%M')
            except:
                expires_text = str(row[0])
        
        def build_keyboard(uid):
            s = get_all_vip_settings(uid)
            def status(k, default='1'):
                return s.get(k, default) == '1'
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔔 Нагадування: {'✅' if status('reminders') else '❌'}", callback_data=f"vip:toggle:reminders")],
                [InlineKeyboardButton(f"📬 Оповіщення про оцінки: {'✅' if status('grade_notifications') else '❌'}", callback_data=f"vip:toggle:grade_notifications")],
                [InlineKeyboardButton("🎯 Аналітика успішності", callback_data="vip:analytics")],
                [InlineKeyboardButton("📄 Експорт даних", callback_data="vip:export")],
                [InlineKeyboardButton("⚙️ Налаштування", callback_data="vip:settings")],
                [InlineKeyboardButton("ℹ️ Інформація", callback_data="vip:info")]
            ])
            return kb
        
        if action == 'toggle' and len(parts) >= 3:
            key = parts[2]
            cur = get_vip_setting(user_id, key, '0')
            new = '0' if cur == '1' else '1'
            set_vip_setting(user_id, key, new)
            text = f"✨ *VIP-меню*\n\n📅 Термін дії до: {expires_text}\n\nОберіть опцію:"
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_keyboard(user_id))
            return
        
        if action == 'analytics':
            # Показываем аналитику оценок
            session = get_session(user_id)
            if not session:
                await query.edit_message_text("❌ Спочатку увійдіть: /start")
                return
            
            await query.edit_message_text("🔄 Завантажую дані для аналітики...")
            
            try:
                # Получаем оценки через API
                today = datetime.now()
                year = today.year
                aug1 = datetime(year, 8, 1)
                if today < aug1:
                    aug1 = datetime(year - 1, 8, 1)
                start = aug1.strftime('%Y-%m-%d')
                end = today.strftime('%Y-%m-%d')
                
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/student-performance",
                    headers={"Authorization": f"Bearer {session['token']}"},
                    json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                )
                
                if r.status_code == 401:
                    new_session = await refresh_session(user_id)
                    if new_session:
                        session = new_session
                        r = scraper.post(
                            f"{API_BASE}/v1/schedule/student-performance",
                            headers={"Authorization": f"Bearer {session['token']}"},
                            json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                        )
                
                subjects_parsed = {}
                api_data = None
                total_api_marks = 0
                
                # Пробуем API
                if r.status_code == 200:
                    api_data = r.json()
                    for subj in api_data.get('subjects', []):
                        total_api_marks += len(subj.get('marks', []) or [])
                    
                    if total_api_marks > 0:
                        # Используем API данные
                        for subj in api_data.get('subjects', []):
                            name = subj.get('subject_name', '').strip()
                            marks = subj.get('marks', []) or []
                            if name:
                                tokens = []
                                for m in marks:
                                    if isinstance(m, (str, int, float)):
                                        tokens.append(str(m))
                                    else:
                                        sig, disp = _extract_mark_info(m)
                                        tokens.append(disp)
                                subjects_parsed[name] = tokens
                
                # Если API пустой или нет данных, пробуем HTML (как в функции avg)
                if not subjects_parsed:
                    grades_url = f"https://nz.ua/schedule/grades-statement"
                    params = {'student_id': session['student_id']}
                    headers = {'User-Agent': 'nz-bot/1.0 (+https://nz.ua)', 'Referer': grades_url}
                    grades_html = None
                    
                    # Пробуем несколько раз с логином (как в avg)
                    for attempt in range(4):
                        try:
                            gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                            if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                grades_html = gresp.text
                                break
                        except Exception as exc:
                            pass
                        
                        # Try logging in and retry
                        try:
                            login_url = "https://nz.ua/login"
                            page = scraper.get(login_url, timeout=10, headers=headers)
                            csrf = None
                            from bs4 import BeautifulSoup
                            login_soup = BeautifulSoup(page.text, 'html.parser')
                            meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                            if meta_csrf:
                                csrf = meta_csrf.get('content')
                            hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                            if hidden_csrf and hidden_csrf.get('value'):
                                csrf = hidden_csrf.get('value')
                            
                            login_data = {
                                "LoginForm[login]": session['username'],
                                "LoginForm[password]": session['password'],
                                "LoginForm[rememberMe]": "1"
                            }
                            lheaders = {'Referer': grades_url}
                            if csrf:
                                login_data['_csrf'] = csrf
                                lheaders['X-CSRF-Token'] = csrf
                            
                            scraper.post(login_url, data=login_data, headers=lheaders, timeout=10)
                            # retry fetch after login
                            try:
                                gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                                if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                    grades_html = gresp.text
                                    break
                            except Exception:
                                pass
                        except Exception:
                            pass
                        
                        time.sleep(1)
                    
                    if grades_html:
                        sd, ed, subs = parse_grades_from_html(grades_html)
                        for name, toks in subs.items():
                            filtered = []
                            for tok_item in toks:
                                if isinstance(tok_item, (list, tuple)) and len(tok_item) >= 2:
                                    tok_text = tok_item[0]
                                else:
                                    tok_text = str(tok_item)
                                filtered.append(tok_text)
                            if filtered:
                                subjects_parsed[name] = filtered
                
                analytics_text = "🎯 *Аналітика успішності*\n\n"
                
                if not subjects_parsed:
                    analytics_text += "❌ Оцінки не знайдено за цей період"
                else:
                    # Собираем статистику
                    all_marks = []
                    subject_stats = {}
                    
                    for name, tokens in subjects_parsed.items():
                        numeric_marks = []
                        for tok in tokens:
                            val = _extract_numeric_from_mark(tok)
                            if val is not None:
                                numeric_marks.append(val)
                                all_marks.append(val)
                        
                        if numeric_marks:
                            avg = sum(numeric_marks) / len(numeric_marks)
                            subject_stats[name] = {
                                'avg': avg,
                                'count': len(numeric_marks),
                                'min': min(numeric_marks),
                                'max': max(numeric_marks)
                            }
                    
                    if all_marks:
                        overall_avg = sum(all_marks) / len(all_marks)
                        analytics_text += f"📊 *Загальна статистика:*\n"
                        analytics_text += f"• Середній бал: {overall_avg:.2f}\n"
                        analytics_text += f"• Всього оцінок: {len(all_marks)}\n"
                        analytics_text += f"• Мінімальна: {min(all_marks)}\n"
                        analytics_text += f"• Максимальна: {max(all_marks)}\n\n"
                        
                        # Топ-3 и худшие предметы
                        sorted_subjects = sorted(subject_stats.items(), key=lambda x: x[1]['avg'], reverse=True)
                        if sorted_subjects:
                            analytics_text += "🏆 *Топ-3 предмети:*\n"
                            for i, (name, stats) in enumerate(sorted_subjects[:3], 1):
                                analytics_text += f"{i}. {name}: {stats['avg']:.2f} ({stats['count']} оцінок)\n"
                            
                            if len(sorted_subjects) > 3:
                                analytics_text += "\n⚠️ *Потребують уваги:*\n"
                                for name, stats in sorted_subjects[-3:]:
                                    analytics_text += f"• {name}: {stats['avg']:.2f}\n"
                    else:
                        analytics_text += "❌ Не знайдено числових оцінок"
                
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="vip:back")]])
                await query.edit_message_text(analytics_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            except Exception as e:
                await query.edit_message_text(f"❌ Помилка: {e}")
                return
        
        if action == 'export':
            # Экспорт данных
            session = get_session(user_id)
            if not session:
                await query.edit_message_text("❌ Спочатку увійдіть: /start")
                return
            
            await query.edit_message_text("🔄 Готую експорт даних...")
            
            try:
                today = datetime.now()
                year = today.year
                aug1 = datetime(year, 8, 1)
                if today < aug1:
                    aug1 = datetime(year - 1, 8, 1)
                start = aug1.strftime('%Y-%m-%d')
                end = today.strftime('%Y-%m-%d')
                
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/student-performance",
                    headers={"Authorization": f"Bearer {session['token']}"},
                    json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                )
                
                if r.status_code == 401:
                    new_session = await refresh_session(user_id)
                    if new_session:
                        session = new_session
                        r = scraper.post(
                            f"{API_BASE}/v1/schedule/student-performance",
                            headers={"Authorization": f"Bearer {session['token']}"},
                            json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                        )
                
                subjects_parsed = {}
                api_data = None
                total_api_marks = 0
                
                # Пробуем API
                if r.status_code == 200:
                    api_data = r.json()
                    for subj in api_data.get('subjects', []):
                        total_api_marks += len(subj.get('marks', []) or [])
                    
                    if total_api_marks > 0:
                        # Используем API данные
                        for subj in api_data.get('subjects', []):
                            name = subj.get('subject_name', '').strip()
                            marks = subj.get('marks', []) or []
                            if name:
                                tokens = []
                                for m in marks:
                                    if isinstance(m, (str, int, float)):
                                        tokens.append(str(m))
                                    else:
                                        sig, disp = _extract_mark_info(m)
                                        tokens.append(disp)
                                subjects_parsed[name] = tokens
                
                # Если API пустой или нет данных, пробуем HTML (как в функции avg)
                if not subjects_parsed:
                    grades_url = f"https://nz.ua/schedule/grades-statement"
                    params = {'student_id': session['student_id']}
                    headers = {'User-Agent': 'nz-bot/1.0 (+https://nz.ua)', 'Referer': grades_url}
                    grades_html = None
                    
                    # Пробуем несколько раз с логином (как в avg)
                    for attempt in range(4):
                        try:
                            gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                            if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                grades_html = gresp.text
                                break
                        except Exception as exc:
                            pass
                        
                        # Try logging in and retry
                        try:
                            login_url = "https://nz.ua/login"
                            page = scraper.get(login_url, timeout=10, headers=headers)
                            csrf = None
                            from bs4 import BeautifulSoup
                            login_soup = BeautifulSoup(page.text, 'html.parser')
                            meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                            if meta_csrf:
                                csrf = meta_csrf.get('content')
                            hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                            if hidden_csrf and hidden_csrf.get('value'):
                                csrf = hidden_csrf.get('value')
                            
                            login_data = {
                                "LoginForm[login]": session['username'],
                                "LoginForm[password]": session['password'],
                                "LoginForm[rememberMe]": "1"
                            }
                            lheaders = {'Referer': grades_url}
                            if csrf:
                                login_data['_csrf'] = csrf
                                lheaders['X-CSRF-Token'] = csrf
                            
                            scraper.post(login_url, data=login_data, headers=lheaders, timeout=10)
                            # retry fetch after login
                            try:
                                gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                                if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                    grades_html = gresp.text
                                    break
                            except Exception:
                                pass
                        except Exception:
                            pass
                        
                        time.sleep(1)
                    
                    if grades_html:
                        sd, ed, subs = parse_grades_from_html(grades_html)
                        for name, toks in subs.items():
                            filtered = []
                            for tok_item in toks:
                                if isinstance(tok_item, (list, tuple)) and len(tok_item) >= 2:
                                    tok_text = tok_item[0]
                                else:
                                    tok_text = str(tok_item)
                                filtered.append(tok_text)
                            if filtered:
                                subjects_parsed[name] = filtered
                
                export_text = "📄 *Експорт даних*\n\n"
                export_text += f"Період: {start} — {end}\n\n"
                
                if subjects_parsed:
                    for name, tokens in subjects_parsed.items():
                        marks_str = ', '.join(tokens)
                        export_text += f"{name}: {marks_str}\n"
                else:
                    export_text += "❌ Оцінки не знайдено"
                
                # Отправляем как файл если слишком длинное
                if len(export_text) > 4000:
                    # Разбиваем на части
                    parts = [export_text[i:i+4000] for i in range(0, len(export_text), 4000)]
                    for part in parts:
                        await query.message.reply_text(part)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="vip:back")]])
                    await query.message.reply_text("✅ Експорт завершено", reply_markup=kb)
                else:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="vip:back")]])
                    await query.edit_message_text(export_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            except Exception as e:
                await query.edit_message_text(f"❌ Помилка: {e}")
                return
        
        if action == 'pdf_report':
            # PDF-отчет об успеваемости
            session = get_session(user_id)
            if not session:
                await query.edit_message_text("❌ Спочатку увійдіть: /start")
                return
            
            await query.edit_message_text("🔄 Готую PDF-звіт про успішність...")
            
            try:
                # Получаем данные для отчета (используем ту же логику что и в analytics)
                today = datetime.now()
                year = today.year
                aug1 = datetime(year, 8, 1)
                if today < aug1:
                    aug1 = datetime(year - 1, 8, 1)
                start = aug1.strftime('%Y-%m-%d')
                end = today.strftime('%Y-%m-%d')
                
                r = scraper.post(
                    f"{API_BASE}/v1/schedule/student-performance",
                    headers={"Authorization": f"Bearer {session['token']}"},
                    json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                )
                
                if r.status_code == 401:
                    new_session = await refresh_session(user_id)
                    if new_session:
                        session = new_session
                        r = scraper.post(
                            f"{API_BASE}/v1/schedule/student-performance",
                            headers={"Authorization": f"Bearer {session['token']}"},
                            json={"student_id": session['student_id'], "start_date": start, "end_date": end}
                        )
                
                subjects_parsed = {}
                api_data = None
                total_api_marks = 0
                
                # Пробуем API
                if r.status_code == 200:
                    api_data = r.json()
                    for subj in api_data.get('subjects', []):
                        total_api_marks += len(subj.get('marks', []) or [])
                    
                    if total_api_marks > 0:
                        for subj in api_data.get('subjects', []):
                            name = subj.get('subject_name', '').strip()
                            marks = subj.get('marks', []) or []
                            if name:
                                tokens = []
                                for m in marks:
                                    if isinstance(m, (str, int, float)):
                                        tokens.append(str(m))
                                    else:
                                        sig, disp = _extract_mark_info(m)
                                        tokens.append(disp)
                                subjects_parsed[name] = tokens
                
                # Если API пустой, пробуем HTML
                if not subjects_parsed:
                    grades_url = f"https://nz.ua/schedule/grades-statement"
                    params = {'student_id': session['student_id']}
                    headers = {'User-Agent': 'nz-bot/1.0 (+https://nz.ua)', 'Referer': grades_url}
                    grades_html = None
                    
                    for attempt in range(4):
                        try:
                            gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                            if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                grades_html = gresp.text
                                break
                        except Exception:
                            pass
                        
                        try:
                            login_url = "https://nz.ua/login"
                            page = scraper.get(login_url, timeout=10, headers=headers)
                            csrf = None
                            from bs4 import BeautifulSoup
                            login_soup = BeautifulSoup(page.text, 'html.parser')
                            meta_csrf = login_soup.find('meta', attrs={'name': 'csrf-token'})
                            if meta_csrf:
                                csrf = meta_csrf.get('content')
                            hidden_csrf = login_soup.find('input', {'name': '_csrf'})
                            if hidden_csrf and hidden_csrf.get('value'):
                                csrf = hidden_csrf.get('value')
                            
                            login_data = {
                                "LoginForm[login]": session['username'],
                                "LoginForm[password]": session['password'],
                                "LoginForm[rememberMe]": "1"
                            }
                            lheaders = {'Referer': grades_url}
                            if csrf:
                                login_data['_csrf'] = csrf
                                lheaders['X-CSRF-Token'] = csrf
                            
                            scraper.post(login_url, data=login_data, headers=lheaders, timeout=10)
                            try:
                                gresp = scraper.get(grades_url, params=params, timeout=10, headers=headers)
                                if gresp and gresp.status_code == 200 and ('Виписка оцінок' in gresp.text or 'Отримані результати' in gresp.text):
                                    grades_html = gresp.text
                                    break
                            except Exception:
                                pass
                        except Exception:
                            pass
                        
                        time.sleep(1)
                    
                    if grades_html:
                        sd, ed, subs = parse_grades_from_html(grades_html)
                        for name, toks in subs.items():
                            filtered = []
                            for tok_item in toks:
                                if isinstance(tok_item, (list, tuple)) and len(tok_item) >= 2:
                                    tok_text = tok_item[0]
                                else:
                                    tok_text = str(tok_item)
                                filtered.append(tok_text)
                            if filtered:
                                subjects_parsed[name] = filtered
                
                if not subjects_parsed:
                    await query.edit_message_text("❌ Не вдалось отримати дані для звіту")
                    return
                
                # Формируем текстовый отчет
                report_text = f"📑 ЗВІТ ПРО УСПІШНІСТЬ\n\n"
                report_text += f"Період: {start} — {end}\n"
                report_text += f"Учень: {session.get('fio', '—')}\n\n"
                report_text += "=" * 50 + "\n\n"
                
                all_marks = []
                subject_stats = {}
                
                for name, tokens in subjects_parsed.items():
                    numeric_marks = []
                    for tok in tokens:
                        val = _extract_numeric_from_mark(tok)
                        if val is not None:
                            numeric_marks.append(val)
                            all_marks.append(val)
                    
                    if numeric_marks:
                        avg = sum(numeric_marks) / len(numeric_marks)
                        subject_stats[name] = {
                            'avg': avg,
                            'count': len(numeric_marks),
                            'min': min(numeric_marks),
                            'max': max(numeric_marks),
                            'marks': numeric_marks
                        }
                
                if all_marks:
                    overall_avg = sum(all_marks) / len(all_marks)
                    report_text += f"📊 ЗАГАЛЬНА СТАТИСТИКА\n\n"
                    report_text += f"Середній бал: {overall_avg:.2f}\n"
                    report_text += f"Всього оцінок: {len(all_marks)}\n"
                    report_text += f"Мінімальна: {min(all_marks)}\n"
                    report_text += f"Максимальна: {max(all_marks)}\n\n"
                    report_text += "=" * 50 + "\n\n"
                    
                    # Сортируем предметы по среднему баллу
                    sorted_subjects = sorted(subject_stats.items(), key=lambda x: x[1]['avg'], reverse=True)
                    
                    report_text += f"📚 СТАТИСТИКА ПО ПРЕДМЕТАМ\n\n"
                    for name, stats in sorted_subjects:
                        report_text += f"{name}:\n"
                        report_text += f"  Середній бал: {stats['avg']:.2f}\n"
                        report_text += f"  Кількість оцінок: {stats['count']}\n"
                        report_text += f"  Мінімальна: {stats['min']}, Максимальна: {stats['max']}\n"
                        report_text += f"  Оцінки: {', '.join(map(str, stats['marks']))}\n\n"
                
                # Отправляем как файл
                from io import BytesIO
                report_file = BytesIO(report_text.encode('utf-8'))
                report_file.name = f"report_{datetime.now().strftime('%Y%m%d')}.txt"
                
                try:
                    await query.message.reply_document(
                        document=report_file,
                        caption="📑 Звіт про успішність",
                        filename=report_file.name
                    )
                    await query.edit_message_text("✅ PDF-звіт готовий!")
                except Exception as e:
                    # Если файл слишком большой, отправляем частями
                    if len(report_text) > 4000:
                        parts = [report_text[i:i+4000] for i in range(0, len(report_text), 4000)]
                        for part in parts:
                            await query.message.reply_text(part)
                        await query.edit_message_text("✅ Звіт надіслано!")
                    else:
                        await query.edit_message_text(f"❌ Помилка при відправці: {e}")
                
                return
            except Exception as e:
                print(f"[VIP PDF REPORT] Error: {e}")
                import traceback
                print(f"[VIP PDF REPORT] Traceback: {traceback.format_exc()}")
                await query.edit_message_text(f"❌ Помилка при створенні звіту: {e}")
                return
        
        if action == 'settings':
            # Настройки VIP
            s = get_all_vip_settings(user_id)
            def status(k, default='1'):
                return s.get(k, default) == '1'
            
            settings_text = "⚙️ *Налаштування VIP*\n\n"
            settings_text += f"🔔 Нагадування: {'✅ Увімкнено' if status('reminders') else '❌ Вимкнено'}\n"
            settings_text += f"📬 Оповіщення про оцінки: {'✅ Увімкнено' if status('grade_notifications') else '❌ Вимкнено'}\n\n"
            settings_text += "Натисніть на опцію для зміни:"
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔔 Нагадування: {'✅' if status('reminders') else '❌'}", callback_data=f"vip:toggle:reminders")],
                [InlineKeyboardButton(f"📬 Оповіщення: {'✅' if status('grade_notifications') else '❌'}", callback_data=f"vip:toggle:grade_notifications")],
                [InlineKeyboardButton("🔙 Назад", callback_data="vip:back")]
            ])
            await query.edit_message_text(settings_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            return
        
        if action == 'info':
            # Информация о VIP
            info_text = "ℹ️ *Інформація про VIP*\n\n"
            info_text += f"📅 Термін дії до: {expires_text}\n\n"
            info_text += "*Доступні функції:*\n"
            info_text += "• 🔔 Нагадування за 5 хв до уроку\n"
            info_text += "• 📬 Сповіщення про нові оцінки\n"
            info_text += "• 🎯 Детальна аналітика успішності\n"
            info_text += "• 📄 Експорт даних\n"
            info_text += "• ⚙️ Налаштування сповіщень\n"
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="vip:back")]])
            await query.edit_message_text(info_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            return
        
        if action == 'back':
            text = f"✨ *VIP-меню*\n\n📅 Термін дії до: {expires_text}\n\nОберіть опцію:"
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_keyboard(user_id))
            return

    # Admin menu callbacks (admin_menu:action)
    if data and data.startswith('admin_menu:'):
        parts = data.split(':')
        action = parts[1] if len(parts) > 1 else None
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.edit_message_text('❌ Тільки адміністратори можуть виконувати цю дію')
            return

        try:
            if action == 'stats':
                # Детальная статистика
                conn = get_db_connection()
                c = conn.cursor()
                
                # Общая статистика
                c.execute('SELECT COUNT(DISTINCT user_id) FROM sessions')
                total_users = c.fetchone()[0] or 0
                
                c.execute('SELECT COUNT(*) FROM vip_users WHERE expires_at > ?', (datetime.now().isoformat(),))
                active_vips = c.fetchone()[0] or 0
                
                c.execute("SELECT COUNT(*) FROM support_tickets WHERE COALESCE(status,'open') = 'open'")
                open_tickets = c.fetchone()[0] or 0
                
                c.execute("SELECT COUNT(*) FROM support_tickets WHERE COALESCE(status,'open') = 'closed'")
                closed_tickets = c.fetchone()[0] or 0
                
                c.execute('SELECT COUNT(*) FROM vip_requests')
                vip_requests = c.fetchone()[0] or 0
                
                # Статистика за последние 7 дней
                week_ago = (datetime.now() - timedelta(days=7)).isoformat()
                c.execute('SELECT COUNT(DISTINCT user_id) FROM sessions WHERE created_at > ?', (week_ago,))
                new_users_week = c.fetchone()[0] or 0
                
                c.execute('SELECT COUNT(*) FROM support_tickets WHERE created_at > ?', (week_ago,))
                new_tickets_week = c.fetchone()[0] or 0
                
                conn.close()
                
                stats_text = "📊 *Детальна статистика бота*\n\n"
                stats_text += "*Користувачі:*\n"
                stats_text += f"• Всього: {total_users}\n"
                stats_text += f"• Нових за тиждень: {new_users_week}\n\n"
                stats_text += "*VIP:*\n"
                stats_text += f"• Активних: {active_vips}\n"
                stats_text += f"• Заявок на VIP: {vip_requests}\n\n"
                stats_text += "*Звернення:*\n"
                stats_text += f"• Відкритих: {open_tickets}\n"
                stats_text += f"• Закритих: {closed_tickets}\n"
                stats_text += f"• Нових за тиждень: {new_tickets_week}\n"
                
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")]])
                await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            
            if action == 'vip_requests':
                # Заявки на VIP
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT id, user_id, contact_text, created_at FROM vip_requests ORDER BY created_at DESC LIMIT 50')
                rows = c.fetchall()
                conn.close()
                
                if not rows:
                    await query.edit_message_text('📋 Заявок на VIP поки немає')
                    return
                
                lines = []
                kb_buttons = []
                for r in rows:
                    req_id, uid, text, created = r
                    text_preview = (text or '')[:50] if text else 'Без тексту'
                    lines.append(f"#{req_id} — {uid} — {created}\n{text_preview}")
                    kb_buttons.append([
                        InlineKeyboardButton(f"Заявка #{req_id}", callback_data=f"admin:view_vip_request:{req_id}"),
                        InlineKeyboardButton("✅ 30д", callback_data=f"admin:grant_vip:{uid}:30")
                    ])
                
                kb_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")])
                await query.edit_message_text('📋 Заявки на VIP:\n\n' + '\n\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb_buttons))
                return
            
            if action == 'management':
                # Управление
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("👥 Управління VIP", callback_data="admin_menu:manage_vips")],
                    [InlineKeyboardButton("📋 Заявки на VIP", callback_data="admin_menu:vip_requests")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")]
                ])
                await query.edit_message_text('⚙️ *Управління*\n\nОберіть опцію:', parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            
            if action == 'manage_vips':
                # Улучшенное управление VIP
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT user_id, expires_at FROM vip_users ORDER BY expires_at DESC LIMIT 50')
                rows = c.fetchall()
                conn.close()
                
                if not rows:
                    await query.edit_message_text('👥 VIP-користувачів поки немає')
                    return
                
                lines = []
                kb_buttons = []
                for r in rows:
                    uid, expires = r
                    expires_text = expires[:10] if expires else 'Не встановлено'
                    lines.append(f"{uid} — до {expires_text}")
                    kb_buttons.append([
                        InlineKeyboardButton(f"👤 {uid}", callback_data=f"admin:view_vip_user:{uid}"),
                        InlineKeyboardButton("❌", callback_data=f"admin:revoke_vip:{uid}")
                    ])
                
                kb_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")])
                await query.edit_message_text('👥 *Управління VIP-користувачами*\n\n' + '\n'.join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb_buttons))
                return
            
            if action == 'list_vips':
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT user_id, expires_at FROM vip_users ORDER BY expires_at DESC')
                rows = c.fetchall()
                conn.close()
                if not rows:
                    await query.edit_message_text('👥 VIP-користувачів поки немає')
                    return
                lines = []
                for r in rows:
                    uid, expires = r
                    expires_text = expires[:10] if expires else 'Не встановлено'
                    try:
                        exp_dt = datetime.fromisoformat(expires)
                        if exp_dt > datetime.now():
                            status = "✅ Активний"
                        else:
                            status = "❌ Закінчився"
                    except:
                        status = "❓"
                    lines.append(f"{uid} — {expires_text} {status}")
                
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")]])
                await query.edit_message_text('👥 *VIP-користувачі:*\n\n' + '\n'.join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return

            if action == 'run_reminders':
                await query.edit_message_text('▶️ Запуск перевірки нагадувань...')
                await check_reminders(context)
                log_admin_action(user_id, 'run_reminders')
                await query.message.reply_text('✅ Перевірка нагадувань завершена')
                return

            if action == 'run_grades':
                await query.edit_message_text('▶️ Запуск перевірки оцінок...')
                await check_grades(context)
                log_admin_action(user_id, 'run_grades')
                await query.message.reply_text('✅ Перевірка оцінок завершена')
                return

            if action == 'view_actions':
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT id, admin_id, action, target_user, ticket_id, details, created_at FROM admin_actions ORDER BY created_at DESC LIMIT 50')
                rows = c.fetchall()
                conn.close()
                if not rows:
                    await query.edit_message_text('ℹ️ Записів дій адміністраторів поки немає')
                    return
                lines = []
                for r in rows:
                    aid, admin_id, action_name, target_user, ticket_id, details, created = r
                    parts = [f"#{aid}", f"admin:{admin_id}", action_name]
                    if target_user:
                        parts.append(f"user:{target_user}")
                    if ticket_id:
                        parts.append(f"ticket:{ticket_id}")
                    if details:
                        parts.append(details)
                    parts.append(str(created))
                    lines.append(" — ".join(parts))
                await query.edit_message_text('🗂️ Останні дії адміністраторів:\n\n' + '\n'.join(lines))
                return

            if action == 'list_tickets':
                # parameter form: admin_menu:list_tickets[:state]
                if len(parts) >= 3:
                    state = parts[2]
                    conn = get_db_connection()
                    c = conn.cursor()
                    if state == 'open':
                        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets WHERE COALESCE(status,'open') = 'open' ORDER BY created_at DESC LIMIT 200")
                    elif state == 'closed':
                        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets WHERE COALESCE(status,'open') = 'closed' ORDER BY created_at DESC LIMIT 200")
                    elif state == 'all':
                        c.execute("SELECT id, user_id, substr(message,1,80) as snippet, created_at FROM support_tickets ORDER BY created_at DESC LIMIT 200")
                    else:
                        await query.edit_message_text('❌ Невідома опція')
                        return
                    rows = c.fetchall()
                    conn.close()
                    if not rows:
                        await query.edit_message_text('📭 Звернень поки немає')
                        return
                    lines = []
                    kb_buttons = []
                    for r in rows:
                        tid, uid, snip, created = r
                        lines.append(f"#{tid} — {uid} — {created} — {snip}")
                        kb_buttons.append([InlineKeyboardButton(f"Тикет #{tid}", callback_data=f"admin:view_ticket:{tid}")])
                    await query.edit_message_text('📭 Останні звернення (' + state + '):\n\n' + '\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb_buttons))
                    return
                else:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔓 Відкриті", callback_data="admin_menu:list_tickets:open")],
                        [InlineKeyboardButton("✅ Закриті", callback_data="admin_menu:list_tickets:closed")],
                        [InlineKeyboardButton("📄 Всі", callback_data="admin_menu:list_tickets:all")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:back")]
                    ])
                    await query.edit_message_text('📭 Оберіть які звернення показувати:', reply_markup=kb)
                    return

            if action == 'broadcast':
                # Запрашиваем текст для рассылки
                await query.answer()
                await query.edit_message_text(
                    "📢 *Розсилка повідомлення всім користувачам*\n\n"
                    "Надішліть текст повідомлення, яке буде надіслано всім зареєстрованим користувачам.\n\n"
                    "⚠️ Будьте обережні з розсилкою!",
                    parse_mode=ParseMode.MARKDOWN
                )
                # Устанавливаем step через context.user_data (привязан к пользователю автоматически)
                context.user_data['step'] = 'admin_broadcast'
                # Отправляем сообщение админу для ввода текста
                await context.bot.send_message(
                    query.from_user.id,
                    "✍️ Введіть текст повідомлення для розсилки всім користувачам:"
                )
                return
            
            if action == 'back':
                # Возвращаемся в главное меню с актуальной статистикой
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT COUNT(DISTINCT user_id) FROM sessions')
                total_users = c.fetchone()[0] or 0
                c.execute('SELECT COUNT(*) FROM vip_users WHERE expires_at > ?', (datetime.now().isoformat(),))
                active_vips = c.fetchone()[0] or 0
                c.execute("SELECT COUNT(*) FROM support_tickets WHERE COALESCE(status,'open') = 'open'")
                open_tickets = c.fetchone()[0] or 0
                c.execute('SELECT COUNT(*) FROM vip_requests')
                vip_requests = c.fetchone()[0] or 0
                conn.close()
                
                stats_text = f"🛠️ *Адмінське меню*\n\n"
                stats_text += f"📊 *Статистика:*\n"
                stats_text += f"👤 Користувачів: {total_users}\n"
                stats_text += f"⭐ VIP активних: {active_vips}\n"
                stats_text += f"📭 Відкритих тикетів: {open_tickets}\n"
                stats_text += f"📋 Заявок на VIP: {vip_requests}\n\n"
                stats_text += "Оберіть дію:"
                
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Статистика", callback_data="admin_menu:stats")],
                    [InlineKeyboardButton("📭 Звернення", callback_data="admin_menu:list_tickets")],
                    [InlineKeyboardButton("👥 VIP-користувачі", callback_data="admin_menu:list_vips")],
                    [InlineKeyboardButton("📋 Заявки на VIP", callback_data="admin_menu:vip_requests")],
                    [InlineKeyboardButton("▶️ Запустити: Нагадування", callback_data="admin_menu:run_reminders"), InlineKeyboardButton("▶️ Запустити: Оцінки", callback_data="admin_menu:run_grades")],
                    [InlineKeyboardButton("🗂️ Лог дій", callback_data="admin_menu:view_actions")],
                    [InlineKeyboardButton("⚙️ Управління", callback_data="admin_menu:management")],
                    [InlineKeyboardButton("📢 Написати оповіщення всім юзерам", callback_data="admin_menu:broadcast")]
                ])
                await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return

        except Exception as e:
            print(f"[ADMIN MENU CALLBACK] Error: {e}")
            await query.edit_message_text('❌ Помилка при виконанні дії')
        return

    # Admin actions: admin:action:params...
    if data and data.startswith('admin:'):
        parts = data.split(':')
        # Structure: admin:action:arg1:arg2...
        action = parts[1] if len(parts) > 1 else None

        # Only admins can use these callbacks
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.edit_message_text('❌ Тільки адміністратори можуть виконувати цю дію')
            return

        try:
            if action == 'grant_vip' and len(parts) >= 4:
                target = int(parts[2])
                days = int(parts[3])
                grant_vip(target, days)
                log_admin_action(user_id, 'grant_vip', target_user=target, details=f'days={days}')
                await query.edit_message_text(f"✅ VIP надано користувачу {target} на {days} днів")
                try:
                    await context.bot.send_message(target, f"✨ Вам було надано VIP на {days} днів!")
                except Exception:
                    pass
                return

            if action == 'revoke_vip' and len(parts) >= 3:
                target = int(parts[2])
                revoke_vip(target)
                log_admin_action(user_id, 'revoke_vip', target_user=target)
                await query.edit_message_text(f"✅ VIP скасовано для користувача {target}")
                try:
                    await context.bot.send_message(target, f"⚠️ Ваш VIP був скасований адміністратором.")
                except Exception:
                    pass
                return

            if action == 'reply_ticket' and len(parts) >= 3:
                ticket_id = int(parts[2])
                # prompt admin to type response
                context.user_data['step'] = 'admin_reply'
                context.user_data['reply_ticket_id'] = ticket_id
                try:
                    await query.message.reply_text(f"✉️ Введіть повідомлення для відповіді на тикет #{ticket_id}.")
                    await _safe_answer(query)
                except Exception:
                    pass
                return

            if action == 'view_ticket' and len(parts) >= 3:
                ticket_id = int(parts[2])
                # Показати деталі тикета
                t = get_ticket(ticket_id)
                if not t:
                    await query.edit_message_text('❌ Тикет не знайдено')
                    return
                t_user = t['user_id']
                t_msg = t['message']
                t_created = t['created_at']
                t_status = t.get('status', 'open')
                profile_url = f"tg://user?id={t_user}"
                kb_buttons = [
                    [InlineKeyboardButton("🔎 Профіль", url=profile_url)],
                    [InlineKeyboardButton("✅ Дати VIP 30д", callback_data=f"admin:grant_vip:{t_user}:30"), InlineKeyboardButton("❌ Забрати VIP", callback_data=f"admin:revoke_vip:{t_user}")]
                ]
                if t_status != 'closed':
                    kb_buttons.append([InlineKeyboardButton("✅ Закрити тикет", callback_data=f"admin:resolve_ticket:{ticket_id}")])
                    kb_buttons.append([InlineKeyboardButton("✉️ Відповісти", callback_data=f"admin:reply_ticket:{ticket_id}")])
                kb = InlineKeyboardMarkup(kb_buttons)
                text = f"🧾 Тикет #{ticket_id}\nВід: {t_user}\nСтатус: {t_status}\nЧас: {t_created}\n\n{t_msg}"
                await query.message.reply_text(text, reply_markup=kb)
                return

            if action == 'view_vip_request' and len(parts) >= 3:
                req_id = int(parts[2])
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT id, user_id, contact_text, created_at FROM vip_requests WHERE id = ?', (req_id,))
                row = c.fetchone()
                conn.close()
                
                if not row:
                    await query.edit_message_text('❌ Заявку не знайдено')
                    return
                
                req_id, uid, text, created = row
                profile_url = f"tg://user?id={uid}"
                
                request_text = f"📋 *Заявка на VIP #{req_id}*\n\n"
                request_text += f"👤 Користувач: {uid}\n"
                request_text += f"📅 Створено: {created}\n\n"
                request_text += f"*Текст заявки:*\n{text or 'Без тексту'}\n"
                
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔎 Профіль", url=profile_url)],
                    [InlineKeyboardButton("✅ Дати VIP 30д", callback_data=f"admin:grant_vip:{uid}:30"),
                     InlineKeyboardButton("✅ Дати VIP 90д", callback_data=f"admin:grant_vip:{uid}:90")],
                    [InlineKeyboardButton("❌ Відхилити", callback_data=f"admin:reject_vip_request:{req_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:vip_requests")]
                ])
                await query.edit_message_text(request_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            
            if action == 'view_vip_user' and len(parts) >= 3:
                target_uid = int(parts[2])
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT expires_at FROM vip_users WHERE user_id = ?', (target_uid,))
                row = c.fetchone()
                
                # Проверяем настройки VIP
                c.execute('SELECT key, value FROM vip_settings WHERE user_id = ?', (target_uid,))
                settings_rows = c.fetchall()
                settings = {r[0]: r[1] for r in settings_rows}
                conn.close()
                
                expires_text = "Не встановлено"
                if row and row[0]:
                    try:
                        expires = datetime.fromisoformat(row[0])
                        expires_text = expires.strftime('%d.%m.%Y %H:%M')
                        if expires > datetime.now():
                            status = "✅ Активний"
                        else:
                            status = "❌ Закінчився"
                    except:
                        expires_text = str(row[0])
                        status = "❓"
                else:
                    status = "❌ Не VIP"
                
                profile_url = f"tg://user?id={target_uid}"
                
                user_text = f"👤 *VIP користувач: {target_uid}*\n\n"
                user_text += f"📅 Термін дії: {expires_text}\n"
                user_text += f"Статус: {status}\n\n"
                user_text += "*Налаштування:*\n"
                user_text += f"🔔 Нагадування: {'✅' if settings.get('reminders', '1') == '1' else '❌'}\n"
                user_text += f"📬 Оповіщення: {'✅' if settings.get('grade_notifications', '1') == '1' else '❌'}\n"
                
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔎 Профіль", url=profile_url)],
                    [InlineKeyboardButton("✅ Продовжити 30д", callback_data=f"admin:grant_vip:{target_uid}:30"),
                     InlineKeyboardButton("✅ Продовжити 90д", callback_data=f"admin:grant_vip:{target_uid}:90")],
                    [InlineKeyboardButton("❌ Забрати VIP", callback_data=f"admin:revoke_vip:{target_uid}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_menu:manage_vips")]
                ])
                await query.edit_message_text(user_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                return
            
            if action == 'reject_vip_request' and len(parts) >= 3:
                req_id = int(parts[2])
                conn = get_db_connection()
                c = conn.cursor()
                c.execute('SELECT user_id FROM vip_requests WHERE id = ?', (req_id,))
                row = c.fetchone()
                if row:
                    target_uid = row[0]
                    # Удаляем заявку
                    c.execute('DELETE FROM vip_requests WHERE id = ?', (req_id,))
                    conn.commit()
                    log_admin_action(user_id, 'reject_vip_request', target_user=target_uid, details=f'request_id={req_id}')
                    try:
                        await context.bot.send_message(target_uid, "❌ Вашу заявку на VIP було відхилено адміністратором.")
                    except:
                        pass
                conn.close()
                await query.edit_message_text(f"✅ Заявку #{req_id} відхилено")
                return

            if action == 'resolve_ticket' and len(parts) >= 3:
                ticket_id = int(parts[2])
                # помічаємо тикет як вирішений
                t = get_ticket(ticket_id)
                if not t:
                    await query.edit_message_text('❌ Тикет не знайдено')
                    return
                resolved = resolve_ticket_db(ticket_id, user_id)
                log_admin_action(user_id, 'resolve_ticket', ticket_id=ticket_id)
                await query.edit_message_text(f"✅ Тикет #{ticket_id} помічено як вирішений")
                # повідомляємо користувача, якщо знайдений
                try:
                    if resolved and resolved.get('user_id'):
                        target_user = resolved.get('user_id')
                        await context.bot.send_message(target_user, f"✅ Ваше звернення #{ticket_id} було позначено як вирішене адміністратором.")
                except Exception as e:
                    print(f"[ADMIN CALLBACK] Could not notify ticket owner {resolved}: {e}")
                return

            if action == 'grant_vip' and len(parts) >= 4:
                target = int(parts[2])
                days = int(parts[3])
                grant_vip(target, days)
                log_admin_action(user_id, 'grant_vip', target_user=target, details=f'days={days}')
                await query.edit_message_text(f"✅ VIP надано користувачу {target} на {days} днів")
                try:
                    await context.bot.send_message(target, f"✨ Вам було надано VIP на {days} днів!")
                except Exception:
                    pass
                return

            await query.edit_message_text('❌ Невідома admin дія')
        except Exception as e:
            print(f"[ADMIN CALLBACK] Error: {e}")
            await query.edit_message_text('❌ Помилка при виконанні дії')
        return

    # Non-admin callbacks (schedule/homework)
    if ':' not in data:
        await query.edit_message_text('❌ Невірні дані')
        return

    kind, day = data.split(':', 1)
    date = await get_date_for_weekday(day)

    if kind == 'schedule':
        await schedule_for_date(query, context, date)
    elif kind == 'homework':
        await homework_for_date(query, context, date)
    else:
        await query.edit_message_text('❌ Невідома дія')

# ============== ЗАПУСК ==============

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Простий HTTP handler для health check"""
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        # Отключаем логирование HTTP запросов
        pass

def run_bot(app):
    """Запускає бота в окремому потоці"""
    try:
        print("[STARTUP] Starting polling...")
        app.run_polling()
    except Exception as exc:
        import traceback
        tb = ''.join(traceback.format_exception(None, exc, exc.__traceback__))
        print(f"[STARTUP ERROR] app.run_polling failed: {exc}\n{tb}")
        raise

def main():
    """Головна функція запуску бота"""
    # Ініціалізація БД
    init_db()
    
    # Токен бота - задається через змінну середовища TELEGRAM_BOT_TOKEN або вбудований в код
    print("[STARTUP] main() reached: checking BOT_TOKEN...")
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    # do not print token value raw; show masked info
    try:
        print(f"[STARTUP] BOT_TOKEN present: {bool(BOT_TOKEN)} length={len(BOT_TOKEN) if BOT_TOKEN else 0}")
    except Exception:
        pass

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ПОМИЛКА: Не вказано токен бота!")
        print("Вставте токен у код або створіть змінну середовища TELEGRAM_BOT_TOKEN")
        return
    
    # Створення застосунку
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        print("[STARTUP] Application built", flush=True)
    except Exception as exc:
        import traceback
        tb = ''.join(traceback.format_exception(None, exc, exc.__traceback__))
        print(f"[STARTUP ERROR] Failed to build Application: {exc}\n{tb}", flush=True)
        return
    
    # ===== РЕЄСТРАЦІЯ ОБРОБНИКІВ =====
    
    # Команди
    app.add_handler(CommandHandler("start", start))
    print("[STARTUP] Registered initial handlers")
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("diary", diary_cmd))
    app.add_handler(CommandHandler("homework", homework_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("vip", vip_cmd))
    app.add_handler(CommandHandler("vip_menu", vip_menu_cmd))
    app.add_handler(CommandHandler("admin_menu", admin_menu_cmd))
    app.add_handler(CommandHandler("vip_request", vip_request_cmd))
    app.add_handler(CommandHandler("grant_vip", grant_vip_cmd))
    app.add_handler(CommandHandler("revoke_vip", revoke_vip_cmd))
    app.add_handler(CommandHandler("policy", policy_cmd))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("logout", logout))
    app.add_handler(CommandHandler("avg_grades", avg))
    app.add_handler(CommandHandler("avg", avg))

    # Callback queries (вибір дня тижня)
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Global error handler
    app.add_error_handler(global_error_handler)

    # Адмінські команди
    app.add_handler(CommandHandler("list_tickets", list_tickets_cmd))
    app.add_handler(CommandHandler("ticket_close", ticket_close_cmd))

    # Кнопки з клавіатури
    app.add_handler(MessageHandler(
        filters.Regex("^(📅 Розклад|📋 Табель|📚 Домашка|📰 Новини|📊 Середній бал|📅 На сьогодні|📅 На завтра|📅 На тиждень|⭐️ VIP|🎁 Free VIP|✉️ Підтримка|🛠 Админ-меню)$"),
        button_handler
    ))

    # Обробка текстових повідомлень (логін/пароль, підтримка) — замінюємо на обгортку з логами
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message_debug))

    # Регістрація фонових задач (JobQueue)
    try:
        app.job_queue.run_repeating(check_reminders, interval=REMINDER_INTERVAL, first=10)
        app.job_queue.run_repeating(check_grades, interval=GRADE_POLL_INTERVAL, first=20)
        if PING_URL:
            app.job_queue.run_repeating(ping_self, interval=PING_INTERVAL, first=15)
        print("[VIP JOB] Background jobs registered: reminders every", REMINDER_INTERVAL, "s; grades every", GRADE_POLL_INTERVAL, "s")
    except Exception as e:
        print("[VIP JOB] Could not register jobs:", e)
    
    print("=" * 50)
    print("🚀 NZ.UA Telegram Bot запущено!")
    print("=" * 50)
    print("📱 Бот готовий до роботи")
    print("💾 База даних:", DB_FILE)
    if CRYPTO_AVAILABLE:
        print("🔐 Шифрування: УВІМКНЕНО")
    else:
        print("⚠️  Шифрування: ВИМКНЕНО (встановіть: pip install cryptography)")
    print("=" * 50)

    # Start polling with error capture
    try:
        print("[STARTUP] Starting polling...")
        # drop_pending_updates=True очищает очередь обновлений при старте
        # Это помогает избежать конфликтов при перезапуске
        app.run_polling(drop_pending_updates=True)
    except Exception as exc:
        import traceback
        tb = ''.join(traceback.format_exception(None, exc, exc.__traceback__))
        print(f"[STARTUP ERROR] app.run_polling failed: {exc}\n{tb}")
        raise

# Global error handler to catch unhandled exceptions from handlers
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        import traceback
        tb = ''.join(traceback.format_exception(None, context.error, context.error.__traceback__))
        print(f"[GLOBAL ERROR] update={update} error={context.error}\n{tb}")
        # notify owner
        try:
            await context.bot.send_message(OWNER_ID, f"[Error] {context.error}\nSee logs for details.")
        except Exception:
            pass
    except Exception as e:
        print(f"[GLOBAL ERROR] failed to log error: {e}")

# NOTE: registrations below moved into main() to avoid indentation issues


# small debug on text handler
async def _handle_message_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        print(f"[MSG] from={update.effective_user and update.effective_user.id} text={getattr(update.message, 'text', None)}")
    except Exception:
        pass
    await handle_message(update, context)

# replace registration with debug wrapper
# (registration moved into main() to ensure proper initialization)


# ----------- KEEPALIVE PING ------------
async def ping_self(context: ContextTypes.DEFAULT_TYPE):
    """Периодически шлёт HTTP-запрос на заданный PING_URL, чтобы не дать хостингу заснуть"""
    if not PING_URL:
        return
    try:
        r = requests.get(PING_URL, timeout=5)
        print(f"[PING] {PING_URL} status={r.status_code}")
    except Exception as e:
        print(f"[PING] failed: {e}")

if __name__ == "__main__":
    main()