import os
import sqlite3
import uuid
import math
import hmac
import secrets
from datetime import datetime, timezone, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify,
    flash, send_from_directory, abort,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from payments import get_provider, available_providers
from services.sports import bet_payout, match_outcome, prediction_points

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.getenv('IUTBET_DB_PATH', os.path.join(DATA_DIR, "iutbet.db"))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

DISCIPLINES = ['Football', 'Basketball', 'Volleyball', 'Handball']
DURATION_MAP = {'Football': 90, 'Basketball': 40, 'Volleyball': 45, 'Handball': 60, 'Default': 60}
OPERATORS = ['ORANGE', 'MTN']

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY must be configured before starting IUT Bet.')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 4MB limit
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    if not os.path.exists(DB_PATH):
        conn = get_db()
        c = conn.cursor()
        c.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            balance INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            team_a TEXT,
            team_b TEXT,
            start_time TEXT,
            competition TEXT,
            discipline TEXT,
            status TEXT DEFAULT 'scheduled',
            odds_a REAL DEFAULT 1.5,
            odds_x REAL DEFAULT 3.0,
            odds_b REAL DEFAULT 2.5,
            score_a INTEGER DEFAULT 0,
            score_b INTEGER DEFAULT 0,
            image TEXT DEFAULT NULL
        );
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            match_id INTEGER,
            choice TEXT,
            amount INTEGER,
            odds REAL,
            won INTEGER DEFAULT NULL,
            processed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            type TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        admin_password = os.getenv('ADMIN_PASSWORD')
        if not admin_password:
            conn.close()
            os.remove(DB_PATH)
            raise RuntimeError('ADMIN_PASSWORD must be configured before creating the first database.')
        c.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (?, ?, ?, ?)",
                  ("admin", generate_password_hash(admin_password), 0, 1))
        conn.commit()
        conn.close()


def ensure_schema():
    """Idempotent migrations — safe to run on every boot."""
    conn = get_db(); c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        operator TEXT,
        status TEXT,
        provider_tx_id TEXT,
        provider TEXT DEFAULT 'manual',
        provider_reference TEXT,
        last_checked_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        phone TEXT,
        operator TEXT,
        status TEXT DEFAULT 'PENDING',
        provider_tx_id TEXT,
        provider TEXT DEFAULT 'manual',
        provider_reference TEXT,
        reserved INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        category TEXT DEFAULT 'system',
        is_read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS online_users (
        user_id INTEGER PRIMARY KEY,
        last_seen TEXT
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS promotions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        subtitle TEXT,
        image TEXT,
        link TEXT,
        active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS admin_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_username TEXT,
        action TEXT,
        target TEXT,
        details TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        match_id INTEGER NOT NULL,
        choice TEXT NOT NULL,
        points_awarded INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, match_id)
    );""")

    # Additive column migrations for DBs created before these existed.
    def add_column(table, coldef):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
        except sqlite3.OperationalError:
            pass

    add_column('bets', 'won INTEGER DEFAULT NULL')
    add_column('bets', 'processed INTEGER DEFAULT 0')
    add_column('bets', 'odds REAL')
    add_column('users', 'blocked INTEGER DEFAULT 0')
    add_column('payments', "provider TEXT DEFAULT 'manual'")
    add_column('payments', 'provider_reference TEXT')
    add_column('payments', 'last_checked_at TEXT')
    add_column('withdrawals', "provider TEXT DEFAULT 'manual'")
    add_column('withdrawals', 'provider_reference TEXT')
    add_column('withdrawals', 'last_checked_at TEXT')
    add_column('withdrawals', 'reserved INTEGER DEFAULT 0')

    conn.commit()

    # Seed a default homepage promotion + demo matches only once, on an
    # otherwise-empty install, so first run isn't blank.
    c.execute("SELECT COUNT(*) AS n FROM matches")
    if c.fetchone()['n'] == 0:
        seed_matches(c)
    c.execute("SELECT COUNT(*) AS n FROM promotions")
    if c.fetchone()['n'] == 0:
        seed_promotions(c)
    conn.commit()
    conn.close()


def seed_matches(c):
    today = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def at(days, hour):
        return (today + timedelta(days=days)).replace(hour=hour).isoformat()

    sample = [
        ("Championnat Inter-Filières — Journée 1", "GEII", "GIL", at(2, 15), "Championnat Inter-Filières", "Football", 1.9, 3.2, 1.8, 0, 0, None),
        ("Championnat Inter-Filières — Journée 1", "Génie Civil", "GRT", at(2, 17), "Championnat Inter-Filières", "Basketball", 1.7, 3.6, 2.1, 0, 0, None),
        ("Coupe du Directeur — Quart de finale", "Finance Comptabilité", "GEII", at(4, 16), "Coupe du Directeur", "Volleyball", 2.0, 3.0, 1.7, 0, 0, None),
        ("Coupe du Directeur — Quart de finale", "GIL", "Génie Civil", at(4, 18), "Coupe du Directeur", "Handball", 1.8, 3.3, 1.9, 0, 0, None),
        ("Tournoi de Rentrée — Poule A", "GRT", "Finance Comptabilité", at(6, 14), "Tournoi de Rentrée", "Football", 2.1, 3.1, 1.65, 0, 0, None),
        ("Tournoi de Rentrée — Poule A", "GEII", "Génie Civil", at(6, 16), "Tournoi de Rentrée", "Basketball", 1.6, 3.8, 2.3, 0, 0, None),
        ("Championnat Inter-Filières — Journée 2", "GIL", "Finance Comptabilité", at(9, 15), "Championnat Inter-Filières", "Volleyball", 1.75, 3.2, 2.0, 0, 0, None),
        ("Championnat Inter-Filières — Journée 2", "Génie Civil", "GEII", at(9, 17), "Championnat Inter-Filières", "Handball", 2.2, 2.9, 1.6, 0, 0, None),
        ("Match amical", "GRT", "GIL", at(12, 16), "Tournoi de Rentrée", "Football", 1.85, 3.1, 1.95, 0, 0, None),
        ("Demi-finale Coupe du Directeur", "Finance Comptabilité", "GRT", at(14, 15), "Coupe du Directeur", "Basketball", 1.9, 3.3, 1.85, 0, 0, None),
    ]
    c.executemany("""INSERT INTO matches (title, team_a, team_b, start_time, competition, discipline,
                     odds_a, odds_x, odds_b, score_a, score_b, image)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", sample)


def seed_promotions(c):
    c.execute("""INSERT INTO promotions (title, subtitle, image, link, active, sort_order)
                 VALUES (?, ?, ?, ?, 1, 1)""",
              ("Bienvenue sur IUT Bet", "Pariez sur les matchs de votre campus", "paris1.jpg", "/matches"))
    c.execute("""INSERT INTO promotions (title, subtitle, image, link, active, sort_order)
                 VALUES (?, ?, ?, ?, 1, 2)""",
              ("Championnat Inter-Filières", "Football, Basketball, Volleyball, Handball", "lige.jpeg", "/competitions"))


init_db()
ensure_schema()


# ---------------------------------------------------------------------------
# Small generic helpers
# ---------------------------------------------------------------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def save_upload(file_storage):
    if not file_storage or not file_storage.filename or not allowed_file(file_storage.filename):
        return None
    filename = secure_filename(file_storage.filename)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    filename = f"{stamp}_{filename}"
    file_storage.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return filename


def get_setting(key, default=None):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))
    conn.commit(); conn.close()


def log_admin_action(action, target='', details=''):
    admin_username = session.get('user', {}).get('username', 'system')
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO admin_audit (admin_username, action, target, details) VALUES (?, ?, ?, ?)",
              (admin_username, action, target, details))
    conn.commit(); conn.close()


def active_provider_name():
    return get_setting('active_payment_provider', 'manual')


def active_provider():
    return get_provider(active_provider_name())


def create_notification(user_id, message, category='system', cursor=None):
    if cursor is not None:
        cursor.execute("INSERT INTO notifications (user_id, message, category) VALUES (?, ?, ?)",
                       (user_id, message, category))
        return
    try:
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO notifications (user_id, message, category) VALUES (?, ?, ?)", (user_id, message, category))
        conn.commit(); conn.close()
    except Exception as e:
        print('Erreur create_notification:', e)

def notify_admins(message, category='system'):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO notifications (user_id, message, category) "
              "SELECT id, ?, ? FROM users WHERE is_admin=1", (message, category))
    conn.commit(); conn.close()


def refresh_session_user():
    if 'user' in session:
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT id, username, balance, is_admin, blocked FROM users WHERE id=?", (session['user']['id'],))
        u = c.fetchone(); conn.close()
        if u and not u['blocked']:
            session['user'] = dict(u)
        else:
            session.pop('user', None)


# ---------------------------------------------------------------------------
# Betting settlement
# ---------------------------------------------------------------------------

def settle_bet(c, bet):
    won = bet['choice'] == match_outcome(bet['score_a'], bet['score_b'])

    if won and bet['locked_odds']:
        winnings = bet_payout(bet['amount'], bet['locked_odds'])
        c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (winnings, bet['user_id']))
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)", (bet['user_id'], winnings, 'bet_win'))
        create_notification(bet['user_id'], f"Vous avez gagné {winnings}F sur le pari #{bet['id']} !", 'bet', cursor=c)

    c.execute("UPDATE bets SET processed=1, won=? WHERE id=?", (1 if won else 0, bet['id']))


def process_finished_matches():
    conn = get_db(); c = conn.cursor()
    c.execute("""SELECT b.*, m.score_a, m.score_b,
                        COALESCE(b.odds, CASE b.choice
                            WHEN '1' THEN m.odds_a WHEN 'X' THEN m.odds_x ELSE m.odds_b END) AS locked_odds
                 FROM bets b JOIN matches m ON m.id = b.match_id
                 WHERE m.status = 'finished' AND b.processed = 0""")
    for bet in c.fetchall():
        settle_bet(c, bet)
    c.execute("""SELECT p.id, p.user_id, p.choice, m.score_a, m.score_b
                 FROM predictions p JOIN matches m ON m.id=p.match_id
                 WHERE m.status='finished' AND p.points_awarded IS NULL""")
    for prediction in c.fetchall():
        c.execute('UPDATE predictions SET points_awarded=? WHERE id=? AND points_awarded IS NULL',
                  (prediction_points(prediction['choice'], prediction['score_a'], prediction['score_b']),
                   prediction['id']))
    conn.commit(); conn.close()


def update_match_statuses():
    now = datetime.now(timezone.utc)
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT id, start_time FROM matches WHERE status = 'scheduled'")
    rows = c.fetchall()
    for r in rows:
        st_raw = r['start_time']
        if not st_raw:
            continue
        try:
            st = datetime.fromisoformat(st_raw)
        except Exception:
            try:
                st = datetime.strptime(st_raw, "%Y-%m-%d %H:%M")
            except Exception:
                continue
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        if now >= st:
            c.execute("UPDATE matches SET status='live' WHERE id=? AND status='scheduled'", (r['id'],))
    conn.commit(); conn.close()


def poll_pending_provider_payments():
    """Check a small batch of PENDING campay/maviance payments, and of
    PROCESSING campay/maviance withdrawals, against the provider (needed for
    Maviance, which has no webhook; also a safety net for Campay if a
    webhook was missed). Rate-limited via last_checked_at so this never
    fires more than once every 15s per transaction."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
    conn = get_db(); c = conn.cursor()

    c.execute("""SELECT * FROM payments WHERE status='PENDING' AND provider IN ('campay','maviance')
                 AND (last_checked_at IS NULL OR last_checked_at < ?) LIMIT 5""", (cutoff,))
    for p in c.fetchall():
        provider = get_provider(p['provider'])
        ref = p['provider_reference'] or p['provider_tx_id']
        result = provider.check_status(ref)
        c.execute("UPDATE payments SET last_checked_at=? WHERE id=?", (now_iso(), p['id']))
        if result.ok and result.status in ('SUCCESSFUL', 'FAILED'):
            _finalize_deposit(c, p, result.status)

    c.execute("""SELECT * FROM withdrawals WHERE status='PROCESSING' AND provider IN ('campay','maviance')
                 AND (last_checked_at IS NULL OR last_checked_at < ?) LIMIT 5""", (cutoff,))
    for w in c.fetchall():
        provider = get_provider(w['provider'])
        ref = w['provider_reference'] or w['provider_tx_id']
        result = provider.check_status(ref)
        c.execute("UPDATE withdrawals SET last_checked_at=? WHERE id=?", (now_iso(), w['id']))
        if result.ok and result.status in ('SUCCESSFUL', 'FAILED'):
            _finalize_withdrawal(c, w, result.status)

    conn.commit(); conn.close()


def _finalize_deposit(c, payment_row, status):
    if status == 'SUCCESSFUL':
        c.execute("UPDATE payments SET status='FINISHED' WHERE id=? AND status='PENDING'", (payment_row['id'],))
        if c.rowcount != 1:
            return False
        c.execute("SELECT balance FROM users WHERE id=?", (payment_row['user_id'],))
        row = c.fetchone()
        if row:
            c.execute("UPDATE users SET balance=balance+? WHERE id=?", (payment_row['amount'], payment_row['user_id']))
            c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
                      (payment_row['user_id'], payment_row['amount'], 'deposit'))
        create_notification(payment_row['user_id'], f"Votre dépôt de {payment_row['amount']}F a été confirmé.", 'deposit', cursor=c)
    else:
        c.execute("UPDATE payments SET status='FAILED' WHERE id=? AND status='PENDING'", (payment_row['id'],))
        if c.rowcount != 1:
            return False
        create_notification(payment_row['user_id'], f"Votre dépôt de {payment_row['amount']}F a échoué.", 'deposit', cursor=c)
    return True


def _finalize_withdrawal(c, w_row, status):
    """For provider-driven withdrawals only: the balance was already
    deducted (and the commission credited) when the admin approved the
    request and triggered the payout, so SUCCESSFUL just closes it out and
    FAILED must refund the user."""
    if status == 'SUCCESSFUL':
        c.execute("UPDATE withdrawals SET status='FINISHED' WHERE id=? AND status='PROCESSING'", (w_row['id'],))
        if c.rowcount != 1:
            return False
        create_notification(w_row['user_id'], f"Votre retrait de {w_row['amount']}F a été envoyé avec succès.", 'withdrawal', cursor=c)
    else:
        c.execute("UPDATE withdrawals SET status='FAILED' WHERE id=? AND status='PROCESSING'", (w_row['id'],))
        if c.rowcount != 1:
            return False
        c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (w_row['amount'], w_row['user_id']))
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
                  (w_row['user_id'], w_row['amount'], 'withdrawal_refund'))
        create_notification(w_row['user_id'], f"Votre retrait de {w_row['amount']}F a échoué côté opérateur et a été remboursé.", 'withdrawal', cursor=c)
    return True


@app.before_request
@app.before_request
def csrf_protect():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.endpoint != 'webhook_campay':
        supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        if not hmac.compare_digest(session['_csrf_token'], supplied):
            return jsonify({'error': 'Jeton CSRF invalide. Rechargez la page et réessayez.'}), 400


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': session.get('_csrf_token', '')}


@app.before_request
def before():
    refresh_session_user()
    try:
        if 'user' in session:
            conn = get_db(); c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO online_users (user_id, last_seen) VALUES (?, ?)",
                      (session['user']['id'], now_iso()))
            conn.commit(); conn.close()
    except Exception as e:
        print('Erreur update online_users:', e)
    try:
        update_match_statuses()
        process_finished_matches()
        poll_pending_provider_payments()
    except Exception as e:
        print('Erreur housekeeping:', e)


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches ORDER BY start_time ASC")
    all_matches = c.fetchall()
    c.execute("SELECT * FROM matches WHERE status='live' ORDER BY start_time ASC")
    live_matches = c.fetchall()
    c.execute("SELECT * FROM promotions WHERE active=1 ORDER BY sort_order ASC")
    promotions = c.fetchall()
    conn.close()
    upcoming = [m for m in all_matches if m['status'] in ('scheduled', 'live', 'halftime')]
    featured = upcoming[:6]
    return render_template('index_v2.html', matches=all_matches, featured=featured,
                            active_matches=live_matches, promotions=promotions, disciplines=DISCIPLINES)


@app.route('/matches')
def matches_page():
    return render_template('matches_page.html', disciplines=DISCIPLINES)


@app.route('/match/<int:match_id>')
def match_detail(match_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    m = c.fetchone(); conn.close()
    if not m:
        abort(404)
    return render_template('match_v2.html', match=m, durations=DURATION_MAP)


@app.route('/competitions')
def competitions():
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT DISTINCT competition FROM matches ORDER BY competition")
    comps = [r['competition'] for r in c.fetchall()]
    conn.close()
    return render_template('competition.html', competitions=comps)


@app.route('/fondateur')
def fondateur():
    return render_template('Fondateur.html')


@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')


@app.route('/robots.txt')
def robots():
    return send_from_directory('static', 'robots.txt')


@app.route('/site.webmanifest')
def webmanifest():
    return send_from_directory('static', 'site.webmanifest')


@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js')


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (username,))
        u = c.fetchone(); conn.close()
        if u and u['blocked']:
            return render_template('login_v2.html', error='Ce compte a été suspendu. Contactez un administrateur.')
        if u and check_password_hash(u['password'], password):
            session['user'] = dict(u)
            return redirect(url_for('index'))
        return render_template('login_v2.html', error='Identifiants incorrects')
    return render_template('login_v2.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip(); password = request.form['password']
        if len(username) < 3 or len(password) < 6:
            return render_template('register_v2.html', error='Identifiant (3+) et mot de passe (6+) requis')
        hashed = generate_password_hash(password)
        conn = get_db(); c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password, balance) VALUES (?, ?, ?)", (username, hashed, 0))
            conn.commit(); conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('register_v2.html', error='Nom déjà pris')
    return render_template('register_v2.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Betting
# ---------------------------------------------------------------------------

@app.route('/place_bet', methods=['POST'])
def place_bet():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    user = session['user']
    try:
        match_id = int(request.form['match_id'])
    except (TypeError, ValueError):
        return jsonify({'error': 'Match invalide'}), 400
    choice = request.form['choice']
    try:
        amount = int(request.form['amount'])
    except Exception:
        return jsonify({'error': 'Montant invalide'}), 400
    if amount < 100 or amount > 5000:
        return jsonify({'error': 'Montant hors limites (100 - 5000)'}), 400
    if choice not in ('1', 'X', '2'):
        return jsonify({'error': 'Choix invalide'}), 400

    conn = get_db(); c = conn.cursor()
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute("SELECT status, odds_a, odds_x, odds_b FROM matches WHERE id=?", (match_id,))
        match = c.fetchone()
        if not match or match['status'] != 'scheduled':
            conn.rollback(); return jsonify({'error': 'Les paris sont clos pour ce match'}), 400

        c.execute("SELECT id FROM bets WHERE user_id=? AND match_id=?", (user['id'], match_id))
        if c.fetchone():
            conn.rollback(); return jsonify({'error': 'Vous avez déjà parié sur ce match'}), 400

        c.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (amount, user['id'], amount))
        if c.rowcount != 1:
            conn.rollback(); return jsonify({'error': 'Solde insuffisant'}), 400

        odds_column = {'1': 'odds_a', 'X': 'odds_x', '2': 'odds_b'}[choice]
        odds = match[odds_column]
        c.execute("INSERT INTO bets (user_id, match_id, choice, amount, odds) VALUES (?, ?, ?, ?, ?)",
                  (user['id'], match_id, choice, amount, odds))
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
                  (user['id'], -amount, 'bet'))
        c.execute("SELECT balance FROM users WHERE id=?", (user['id'],))
        new_bal = c.fetchone()['balance']
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    session['user']['balance'] = new_bal
    return jsonify({'ok': True, 'balance': new_bal})


@app.route('/api/matches/<int:match_id>/bets', methods=['GET'])
def api_get_match_bets(match_id):
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE match_id=? AND user_id=? ORDER BY created_at DESC", (match_id, session['user']['id']))
    bets = [dict(b) for b in c.fetchall()]
    conn.close()
    return jsonify(bets)


@app.route('/api/matches', methods=['GET'])
def api_get_matches():
    discipline = request.args.get('discipline')
    competition = request.args.get('competition')
    status = request.args.get('status')
    q = "SELECT * FROM matches WHERE 1=1"
    params = []
    if discipline:
        q += " AND discipline = ?"; params.append(discipline)
    if competition:
        q += " AND competition = ?"; params.append(competition)
    if status:
        q += " AND status = ?"; params.append(status)
    q += " ORDER BY start_time ASC"
    conn = get_db(); c = conn.cursor()
    c.execute(q, params)
    matches = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(matches)


@app.route('/api/predictions', methods=['POST'])
def api_create_prediction():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json(silent=True) or request.form
    try:
        match_id = int(data.get('match_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Match invalide'}), 400
    choice = data.get('choice')
    if choice not in ('1', 'X', '2'):
        return jsonify({'error': 'Pronostic invalide'}), 400

    conn = get_db(); c = conn.cursor()
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute("SELECT status FROM matches WHERE id=?", (match_id,))
        match = c.fetchone()
        if not match or match['status'] != 'scheduled':
            conn.rollback(); return jsonify({'error': 'Les pronostics sont clos pour ce match'}), 400
        c.execute("""INSERT INTO predictions (user_id, match_id, choice)
                     VALUES (?, ?, ?)
                     ON CONFLICT(user_id, match_id) DO UPDATE SET choice=excluded.choice,
                         points_awarded=NULL""", (session['user']['id'], match_id, choice))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({'ok': True, 'choice': choice})


@app.route('/api/predictions/<int:match_id>')
def api_get_prediction(match_id):
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT choice, points_awarded FROM predictions WHERE user_id=? AND match_id=?",
              (session['user']['id'], match_id))
    prediction = c.fetchone()
    conn.close()
    return jsonify(dict(prediction) if prediction else {})


@app.route('/leaderboard')
def leaderboard():
    conn = get_db(); c = conn.cursor()
    c.execute("""SELECT u.username, COALESCE(SUM(p.points_awarded), 0) AS points,
                        COUNT(p.id) AS predictions
                 FROM users u LEFT JOIN predictions p ON p.user_id=u.id
                 WHERE u.is_admin=0 AND u.blocked=0
                 GROUP BY u.id ORDER BY points DESC, predictions DESC, u.username COLLATE NOCASE ASC""")
    rows = c.fetchall()
    conn.close()
    return render_template('leaderboard.html', entries=rows)


# ---------------------------------------------------------------------------
# Deposits / withdrawals (payment providers)
# ---------------------------------------------------------------------------

@app.route('/deposit', methods=['GET'])
def deposit_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('deposit_v2.html', active_provider=active_provider_name())


@app.route('/deposit_status/<txid>')
def deposit_status(txid):
    conn = get_db(); c = conn.cursor()
    if 'user' not in session:
        conn.close(); return redirect(url_for('login'))
    c.execute("SELECT * FROM payments WHERE provider_tx_id=? AND user_id=?", (txid, session['user']['id']))
    p = c.fetchone(); conn.close()
    if not p:
        flash('Transaction introuvable', 'error'); return redirect(url_for('deposit_page'))
    return render_template('deposit_status.html', payment=p)


@app.route('/api/deposit', methods=['POST'])
def api_deposit():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json() or {}
    phone = (data.get('phone') or '').strip()
    operator = (data.get('operator') or '').upper()
    try:
        amount = int(data.get('amount', 0))
    except Exception:
        return jsonify({'error': 'Montant invalide'}), 400

    if not phone or operator not in OPERATORS or amount < 100 or amount > 1000000:
        return jsonify({'error': 'Données manquantes ou invalides'}), 400

    user_id = session['user']['id']
    external_ref = f"DEP_{user_id}_{uuid.uuid4().hex[:10]}"
    provider = active_provider()
    result = provider.initiate_collection(phone, amount, external_ref, operator=operator)

    conn = get_db(); c = conn.cursor()
    status = result.status if result.ok else 'FAILED'
    c.execute("""INSERT INTO payments (user_id, amount, operator, status, provider_tx_id, provider, provider_reference)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (user_id, amount, operator, status, external_ref, provider.name, result.reference))
    conn.commit(); conn.close()

    if not result.ok:
        return jsonify({'error': result.error or "Le fournisseur de paiement a refusé la demande."}), 502

    notify_admins(f"Nouveau dépôt en attente ({provider.name}): {amount}F par {session['user']['username']}", 'deposit')
    return jsonify({
        'ok': True,
        'provider_tx_id': external_ref,
        'provider': provider.name,
        'ussd': result.ussd,
        'message': result.message or "Dépôt enregistré. En attente de confirmation.",
    })


@app.route('/webhook/campay', methods=['GET', 'POST'])
def webhook_campay():
    # Campay lets the merchant pick GET (query string) or POST (JSON body)
    # for callbacks -- accept either.
    data = request.get_json(silent=True) or request.values.to_dict()

    campay = get_provider('campay')
    verified = campay.verify_webhook_signature(data)
    if verified is not True:
        return jsonify({'received': False, 'error': 'invalid signature'}), 401
    reference = data.get('reference')
    external_reference = data.get('external_reference')

    conn = get_db(); c = conn.cursor()

    p = None
    if external_reference:
        c.execute("SELECT * FROM payments WHERE provider_tx_id=?", (external_reference,))
        p = c.fetchone()
    if not p and reference:
        c.execute("SELECT * FROM payments WHERE provider_reference=?", (reference,))
        p = c.fetchone()

    if p:
        if p['status'] != 'PENDING':
            conn.close(); return jsonify({'received': True, 'note': 'already settled'})
        result = campay.check_status(p['provider_reference'] or p['provider_tx_id'])
        if result.ok and result.status in ('SUCCESSFUL', 'FAILED'):
            _finalize_deposit(c, p, result.status)
        conn.commit(); conn.close()
        return jsonify({'received': True})

    w = None
    if external_reference:
        c.execute("SELECT * FROM withdrawals WHERE provider_tx_id=?", (external_reference,))
        w = c.fetchone()
    if not w and reference:
        c.execute("SELECT * FROM withdrawals WHERE provider_reference=?", (reference,))
        w = c.fetchone()

    if w:
        if w['status'] != 'PROCESSING':
            conn.close(); return jsonify({'received': True, 'note': 'already settled'})
        result = campay.check_status(w['provider_reference'] or w['provider_tx_id'])
        if result.ok and result.status in ('SUCCESSFUL', 'FAILED'):
            _finalize_withdrawal(c, w, result.status)
        conn.commit(); conn.close()
        return jsonify({'received': True})

    conn.close()
    return jsonify({'received': True, 'note': 'transaction not found locally'})


@app.route('/wallet')
def wallet():
    if 'user' not in session:
        return redirect(url_for('login'))
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT amount, type, created_at FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
              (session['user']['id'],))
    transactions = c.fetchall()
    c.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM withdrawals WHERE user_id=? AND status IN ('PENDING', 'PROCESSING')",
              (session['user']['id'],))
    reserved_withdrawals = c.fetchone()['total']
    conn.close()
    return render_template('wallet.html', transactions=transactions, reserved_withdrawals=reserved_withdrawals)


@app.route('/api/withdraw', methods=['POST'])
def api_withdraw():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json() or {}
    try:
        amount = int(data.get('amount'))
    except Exception:
        return jsonify({'error': 'Montant invalide'}), 400
    phone = (data.get('phone') or '').strip()
    operator = (data.get('operator') or '').upper()

    if amount < 500:
        return jsonify({'error': 'Montant minimum: 500F'}), 400
    if not phone.isdigit() or len(phone) != 9:
        return jsonify({'error': 'Numéro de téléphone invalide'}), 400
    if operator not in OPERATORS:
        return jsonify({'error': 'Opérateur non supporté'}), 400

    user = session['user']
    conn = get_db(); c = conn.cursor()
    external_ref = f"WDR_{user['id']}_{uuid.uuid4().hex[:10]}"
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (amount, user['id'], amount))
        if c.rowcount != 1:
            conn.rollback(); return jsonify({'error': 'Solde insuffisant'}), 400
        c.execute("""INSERT INTO withdrawals (user_id, amount, phone, operator, provider, provider_tx_id, reserved)
                     VALUES (?, ?, ?, ?, 'manual', ?, 1)""", (user['id'], amount, phone, operator, external_ref))
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'withdrawal')", (user['id'], -amount))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    session['user']['balance'] -= amount
    notify_admins(f"Nouvelle demande de retrait: {amount}F par {user['username']}", 'withdrawal')
    return jsonify({'ok': True, 'message': "Retrait enregistré. Le montant est réservé jusqu'au traitement de la demande.",
                     'provider_tx_id': external_ref})


# ---------------------------------------------------------------------------
# Notifications / presence
# ---------------------------------------------------------------------------

@app.route('/api/notifications')
def api_notifications():
    if 'user' not in session:
        return jsonify([])
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
              (session['user']['id'],))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/notifications/unread_count')
def api_notifications_unread_count():
    if 'user' not in session:
        return jsonify({'unread': 0})
    uid = session['user']['id']
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) AS c FROM notifications WHERE is_read=0 AND user_id=?", (uid,))
    row = c.fetchone(); conn.close()
    return jsonify({'unread': row['c'] if row else 0})


@app.route('/api/notifications/<int:nid>/read', methods=['POST'])
def api_notification_read(nid):
    if 'user' not in session:
        return jsonify({'error': 'Auth required'}), 401
    conn = get_db(); c = conn.cursor()
    c.execute('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?', (nid, session['user']['id']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/api/online_users')
def api_online_users():
    admin_required()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    conn = get_db(); c = conn.cursor()
    c.execute("""SELECT o.user_id, o.last_seen, u.username FROM online_users o
                 JOIN users u ON u.id = o.user_id WHERE o.last_seen >= ? ORDER BY o.last_seen DESC""", (cutoff,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Admin — dashboard / matches
# ---------------------------------------------------------------------------

def admin_required():
    if 'user' not in session or session['user'].get('is_admin') != 1:
        abort(403)


@app.route('/admin')
def admin():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches ORDER BY start_time DESC")
    matches = c.fetchall()

    c.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin=0")
    total_users = c.fetchone()['n']
    c.execute("SELECT COALESCE(SUM(amount),0) AS s FROM bets")
    total_staked = c.fetchone()['s']
    c.execute("SELECT COALESCE(SUM(amount),0) AS s FROM transactions WHERE type='bet_win'")
    total_payout = c.fetchone()['s']
    c.execute("SELECT COUNT(*) AS n FROM payments WHERE status='PENDING'")
    pending_deposits = c.fetchone()['n']
    c.execute("SELECT COUNT(*) AS n FROM withdrawals WHERE status='PENDING'")
    pending_withdrawals = c.fetchone()['n']
    c.execute("SELECT balance FROM users WHERE id=1")
    row = c.fetchone()
    platform_balance = row['balance'] if row else 0
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    c.execute("SELECT COUNT(*) AS n FROM online_users WHERE last_seen >= ?", (cutoff,))
    online_now = c.fetchone()['n']
    conn.close()

    stats = {
        'total_users': total_users,
        'total_staked': total_staked,
        'total_payout': total_payout,
        'margin': total_staked - total_payout,
        'pending_deposits': pending_deposits,
        'pending_withdrawals': pending_withdrawals,
        'platform_balance': platform_balance,
        'online_now': online_now,
    }
    return render_template('admin_v2.html', matches=matches, stats=stats, disciplines=DISCIPLINES)


@app.route('/api/matches', methods=['POST'])
def api_create_match():
    admin_required()
    title = request.form.get('title'); team_a = request.form.get('team_a'); team_b = request.form.get('team_b')
    start_time_raw = request.form.get('start_time')
    competition = request.form.get('competition'); discipline = request.form.get('discipline')

    if discipline not in DISCIPLINES:
        return jsonify({'error': f"Discipline invalide (attendu: {', '.join(DISCIPLINES)})"}), 400
    try:
        start_time_dt = datetime.fromisoformat(start_time_raw)
    except Exception:
        try:
            start_time_dt = datetime.strptime(start_time_raw, "%Y-%m-%d %H:%M")
        except Exception:
            return jsonify({'error': 'Format de date invalide'}), 400
    if start_time_dt.tzinfo is None:
        start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)

    try:
        odds_a = float(request.form.get('odds_a', 1.5))
        odds_x = float(request.form.get('odds_x', 3.0))
        odds_b = float(request.form.get('odds_b', 2.5))
    except Exception:
        return jsonify({'error': 'Cotes invalides'}), 400
    if any(not math.isfinite(odds) or odds <= 1 for odds in (odds_a, odds_x, odds_b)):
        return jsonify({'error': 'Chaque cote doit être un nombre fini supérieur à 1'}), 400

    image_filename = save_upload(request.files.get('image'))

    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO matches (title, team_a, team_b, start_time, competition, discipline, odds_a, odds_x, odds_b, image)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (title, team_a, team_b, start_time_dt.isoformat(), competition, discipline, odds_a, odds_x, odds_b, image_filename))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    log_admin_action('create_match', str(new_id), title)
    return jsonify({'ok': True, 'id': new_id})


@app.route('/api/matches/<int:match_id>/update', methods=['POST'])
def api_update_match(match_id):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    if not c.fetchone():
        conn.close(); return jsonify({'error': 'Match introuvable'}), 404

    fields, params = [], []
    for key in ('title', 'team_a', 'team_b', 'competition'):
        if request.form.get(key) is not None:
            fields.append(f"{key}=?"); params.append(request.form.get(key))
    if request.form.get('discipline'):
        if request.form['discipline'] not in DISCIPLINES:
            conn.close(); return jsonify({'error': 'Discipline invalide'}), 400
        fields.append('discipline=?'); params.append(request.form['discipline'])
    if request.form.get('start_time'):
        try:
            st = datetime.fromisoformat(request.form['start_time'])
        except Exception:
            st = datetime.strptime(request.form['start_time'], "%Y-%m-%d %H:%M")
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        fields.append('start_time=?'); params.append(st.isoformat())
    for key in ('odds_a', 'odds_x', 'odds_b'):
        if request.form.get(key):
            try:
                odds = float(request.form[key])
            except ValueError:
                conn.close(); return jsonify({'error': 'Cotes invalides'}), 400
            if not math.isfinite(odds) or odds <= 1:
                conn.close(); return jsonify({'error': 'Chaque cote doit être un nombre fini supérieur à 1'}), 400
            fields.append(f"{key}=?"); params.append(odds)
    image_filename = save_upload(request.files.get('image'))
    if image_filename:
        fields.append('image=?'); params.append(image_filename)

    if fields:
        params.append(match_id)
        c.execute(f"UPDATE matches SET {', '.join(fields)} WHERE id=?", params)
        conn.commit()
    conn.close()
    log_admin_action('update_match', str(match_id))
    return jsonify({'ok': True})


@app.route('/api/matches/<int:match_id>/delete', methods=['POST'])
def api_delete_match(match_id):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) AS n FROM bets WHERE match_id=?", (match_id,))
    if c.fetchone()['n'] > 0:
        conn.close()
        return jsonify({'error': 'Impossible de supprimer un match avec des paris. Marquez-le "Terminé" à la place.'}), 400
    c.execute("DELETE FROM matches WHERE id=?", (match_id,))
    conn.commit(); conn.close()
    log_admin_action('delete_match', str(match_id))
    return jsonify({'ok': True})


@app.route('/api/match/<int:match_id>/update_status', methods=['POST'])
def api_update_match_status(match_id):
    admin_required()
    status = request.form.get('status')
    if status not in ('scheduled', 'live', 'halftime', 'finished'):
        return jsonify({'error': 'Statut invalide'}), 400
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE matches SET status=? WHERE id=?", (status, match_id))
    conn.commit(); conn.close()
    if status == 'finished':
        process_finished_matches()
    log_admin_action('match_status', str(match_id), status)
    return jsonify({'ok': True})


@app.route('/api/match/<int:match_id>/update_scores', methods=['POST'])
def api_update_scores(match_id):
    admin_required()
    try:
        score_a = int(request.form.get('score_a', 0))
        score_b = int(request.form.get('score_b', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Scores invalides'}), 400
    if score_a < 0 or score_b < 0:
        return jsonify({'error': 'Les scores ne peuvent pas être négatifs'}), 400
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE matches SET score_a=?, score_b=?, status='live' WHERE id=?", (score_a, score_b, match_id))
    conn.commit(); conn.close()
    log_admin_action('update_score', str(match_id), f"{score_a}-{score_b}")
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Admin — payments / withdrawals
# ---------------------------------------------------------------------------

@app.route('/admin/payments')
def admin_payments():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT p.*, u.username FROM payments p LEFT JOIN users u ON u.id = p.user_id ORDER BY p.created_at DESC")
    rows = c.fetchall(); conn.close()
    return render_template('admin_payments.html', payments=rows)


@app.route('/admin/payment/<int:payment_id>/approve', methods=['POST'])
def admin_payment_approve(payment_id):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute('SELECT * FROM payments WHERE id=?', (payment_id,))
    p = c.fetchone()
    if not p:
        conn.close(); return jsonify({'error': 'Paiement introuvable'}), 404
    if p['status'] != 'PENDING' or p['provider'] != 'manual':
        conn.close(); return jsonify({'error': 'Seul un dépôt manuel en attente peut être approuvé'}), 400
    if not _finalize_deposit(c, p, 'SUCCESSFUL'):
        conn.close(); return jsonify({'error': 'Paiement déjà traité'}), 400
    conn.commit(); conn.close()
    log_admin_action('approve_payment', str(payment_id))
    return jsonify({'ok': True})


@app.route('/admin/payment/<int:payment_id>/reject', methods=['POST'])
def admin_payment_reject(payment_id):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute('SELECT * FROM payments WHERE id=?', (payment_id,))
    p = c.fetchone()
    if not p:
        conn.close(); return jsonify({'error': 'Paiement introuvable'}), 404
    if p['status'] != 'PENDING':
        conn.close(); return jsonify({'error': 'Paiement déjà traité'}), 400
    c.execute("UPDATE payments SET status='REJECTED' WHERE id=? AND status='PENDING'", (payment_id,))
    if c.rowcount != 1:
        conn.close(); return jsonify({'error': 'Paiement déjà traité'}), 400
    conn.commit(); conn.close()
    create_notification(p['user_id'], f"Votre dépôt de {p['amount']}F a été rejeté. Veuillez réessayer.", 'deposit')
    log_admin_action('reject_payment', str(payment_id))
    return jsonify({'ok': True})


@app.route('/admin/withdrawals')
def admin_withdrawals():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT w.*, u.username FROM withdrawals w LEFT JOIN users u ON u.id = w.user_id ORDER BY w.created_at DESC")
    rows = c.fetchall(); conn.close()
    return render_template('admin_withdrawals.html', withdrawals=rows)


@app.route('/admin/withdraw/<int:wid>/approve', methods=['POST'])
def admin_withdraw_approve(wid):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute('SELECT * FROM withdrawals WHERE id=?', (wid,))
    w = c.fetchone()
    if not w:
        conn.close(); return jsonify({'error': 'Retrait introuvable'}), 404
    if w['status'] != 'PENDING':
        conn.close(); return jsonify({'error': 'Déjà traité'}), 400

    if not w['reserved']:
        c.execute('SELECT balance FROM users WHERE id=?', (w['user_id'],))
        row = c.fetchone()
        if not row or (row['balance'] or 0) < w['amount']:
            conn.close(); return jsonify({'error': 'Solde insuffisant pour traiter le retrait'}), 400

    # Attempt an automatic payout through the active provider when it's not
    # the manual fallback; otherwise the admin transfers the money by hand
    # exactly as today and simply confirms it here.
    provider = get_provider(w['provider'] or 'manual')
    if provider.name != 'manual':
        result = provider.initiate_payout(w['phone'], w['amount'], w['provider_tx_id'], operator=w['operator'])
        if not result.ok:
            conn.close()
            return jsonify({'error': f"Échec du paiement automatique ({provider.name}): {result.error}"}), 502
        c.execute('UPDATE withdrawals SET provider_reference=? WHERE id=?', (result.reference, wid))

    if not w['reserved']:
        c.execute('UPDATE users SET balance = balance - ? WHERE id=? AND balance>=?',
                  (w['amount'], w['user_id'], w['amount']))
        if c.rowcount != 1:
            conn.rollback(); conn.close()
            return jsonify({'error': 'Solde insuffisant pour traiter le retrait'}), 400
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'withdrawal')",
                  (w['user_id'], -w['amount']))

    commission_pct = float(get_setting('withdrawal_commission_pct', '1') or '1')
    fee = int(max(1, round(w['amount'] * commission_pct / 100)))
    c.execute('UPDATE users SET balance = balance + ? WHERE id=?', (fee, 1))

    if provider.name == 'manual':
        # The admin already transferred the money by hand before clicking
        # this button, so there is nothing left to confirm.
        c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('FINISHED', wid))
        create_notification(w['user_id'], f"Votre demande de retrait de {w['amount']}F a été approuvée.", 'withdrawal', cursor=c)
    else:
        # The payout was only *initiated* -- Campay/Maviance confirm success
        # or failure asynchronously (webhook or polling), which is when the
        # withdrawal actually becomes FINISHED (or FAILED + refunded).
        c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('PROCESSING', wid))
        create_notification(w['user_id'], f"Votre retrait de {w['amount']}F est en cours de traitement via {provider.name}.", 'withdrawal', cursor=c)
    conn.commit(); conn.close()

    log_admin_action('approve_withdrawal', str(wid))
    return jsonify({'ok': True})


@app.route('/admin/withdraw/<int:wid>/reject', methods=['POST'])
def admin_withdraw_reject(wid):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute('SELECT * FROM withdrawals WHERE id=?', (wid,))
    w = c.fetchone()
    if not w:
        conn.close(); return jsonify({'error': 'Retrait introuvable'}), 404
    if w['status'] != 'PENDING':
        conn.close(); return jsonify({'error': 'Déjà traité'}), 400
    c.execute("UPDATE withdrawals SET status='REJECTED' WHERE id=? AND status='PENDING'", (wid,))
    if c.rowcount != 1:
        conn.close(); return jsonify({'error': 'Déjà traité'}), 400
    if w['reserved']:
        c.execute('UPDATE users SET balance=balance+? WHERE id=?', (w['amount'], w['user_id']))
        c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'withdrawal_refund')",
                  (w['user_id'], w['amount']))
    conn.commit(); conn.close()
    create_notification(w['user_id'], f"Votre demande de retrait de {w['amount']}F a été rejetée.", 'withdrawal')
    log_admin_action('reject_withdrawal', str(wid))
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Admin — users
# ---------------------------------------------------------------------------

@app.route('/admin/users')
def admin_users():
    admin_required()
    q = request.args.get('q', '').strip()
    conn = get_db(); c = conn.cursor()
    if q:
        c.execute("SELECT * FROM users WHERE username LIKE ? ORDER BY id DESC", (f"%{q}%",))
    else:
        c.execute("SELECT * FROM users ORDER BY id DESC")
    users = c.fetchall(); conn.close()
    return render_template('admin_users.html', users=users, q=q)


@app.route('/admin/user/<int:uid>/adjust_balance', methods=['POST'])
def admin_adjust_balance(uid):
    admin_required()
    try:
        amount = int(request.form.get('amount'))
    except Exception:
        return jsonify({'error': 'Montant invalide'}), 400
    reason = request.form.get('reason', '')
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    if not row:
        conn.close(); return jsonify({'error': 'Utilisateur introuvable'}), 404
    new_balance = (row['balance'] or 0) + amount
    if new_balance < 0:
        conn.close(); return jsonify({'error': 'Le solde ne peut pas devenir négatif'}), 400
    c.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, uid))
    c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)", (uid, amount, 'admin_adjustment'))
    conn.commit(); conn.close()
    create_notification(uid, f"Votre solde a été ajusté de {amount:+}F par un administrateur ({reason or 'sans motif'}).", 'system')
    log_admin_action('adjust_balance', str(uid), f"{amount:+}F — {reason}")
    return jsonify({'ok': True, 'balance': new_balance})


@app.route('/admin/user/<int:uid>/toggle_block', methods=['POST'])
def admin_toggle_block(uid):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT blocked, is_admin FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    if not row:
        conn.close(); return jsonify({'error': 'Utilisateur introuvable'}), 404
    if row['is_admin']:
        conn.close(); return jsonify({'error': 'Impossible de bloquer un administrateur'}), 400
    new_state = 0 if row['blocked'] else 1
    c.execute("UPDATE users SET blocked=? WHERE id=?", (new_state, uid))
    conn.commit(); conn.close()
    log_admin_action('toggle_block', str(uid), 'blocked' if new_state else 'unblocked')
    return jsonify({'ok': True, 'blocked': bool(new_state)})


# ---------------------------------------------------------------------------
# Admin — promotions (homepage CMS)
# ---------------------------------------------------------------------------

@app.route('/admin/promotions')
def admin_promotions():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM promotions ORDER BY sort_order ASC, id DESC")
    promos = c.fetchall(); conn.close()
    return render_template('admin_promotions.html', promotions=promos)


@app.route('/admin/promotions/add', methods=['POST'])
def admin_promotions_add():
    admin_required()
    title = request.form.get('title', '')
    subtitle = request.form.get('subtitle', '')
    link = request.form.get('link', '/matches')
    sort_order = int(request.form.get('sort_order', 0) or 0)
    image_filename = save_upload(request.files.get('image'))
    if not image_filename:
        return jsonify({'error': 'Image requise'}), 400
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO promotions (title, subtitle, image, link, sort_order, active)
                 VALUES (?, ?, ?, ?, ?, 1)""", (title, subtitle, image_filename, link, sort_order))
    conn.commit(); conn.close()
    log_admin_action('add_promotion', title)
    return jsonify({'ok': True})


@app.route('/admin/promotions/<int:pid>/toggle', methods=['POST'])
def admin_promotions_toggle(pid):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT active FROM promotions WHERE id=?", (pid,))
    row = c.fetchone()
    if not row:
        conn.close(); return jsonify({'error': 'Introuvable'}), 404
    c.execute("UPDATE promotions SET active=? WHERE id=?", (0 if row['active'] else 1, pid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/admin/promotions/<int:pid>/delete', methods=['POST'])
def admin_promotions_delete(pid):
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM promotions WHERE id=?", (pid,))
    conn.commit(); conn.close()
    log_admin_action('delete_promotion', str(pid))
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Admin — settings
# ---------------------------------------------------------------------------

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    admin_required()
    if request.method == 'POST':
        provider_name = request.form.get('active_payment_provider', 'manual')
        if provider_name not in available_providers():
            flash('Fournisseur de paiement non disponible.', 'error')
            return redirect(url_for('admin_settings'))
        try:
            commission_pct = float(request.form.get('withdrawal_commission_pct', '1'))
        except ValueError:
            commission_pct = -1
        if not math.isfinite(commission_pct) or not 0 <= commission_pct <= 20:
            flash('La commission doit être comprise entre 0 et 20%.', 'error')
            return redirect(url_for('admin_settings'))
        set_setting('active_payment_provider', provider_name)
        set_setting('withdrawal_commission_pct', str(commission_pct))
        set_setting('support_phone', request.form.get('support_phone', ''))
        set_setting('site_tagline', request.form.get('site_tagline', ''))
        log_admin_action('update_settings')
        flash('Réglages enregistrés', 'success')
        return redirect(url_for('admin_settings'))

    settings = {
        'active_payment_provider': active_provider_name(),
        'withdrawal_commission_pct': get_setting('withdrawal_commission_pct', '1'),
        'support_phone': get_setting('support_phone', ''),
        'site_tagline': get_setting('site_tagline', "Pariez sur les événements sportifs de votre campus"),
    }
    return render_template('admin_settings.html', settings=settings, available=available_providers())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
