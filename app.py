import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import json
from dotenv import load_dotenv
import os
import uuid
from datetime import timedelta
import requests
load_dotenv()  # charge ton fichier .env automatiquement

PAWAPAY_API_TOKEN = os.getenv("PAWAPAY_API_TOKEN")
PAWAPAY_BASE = os.getenv("PAWAPAY_BASE")
MY_WEBHOOK_URL = os.getenv("MY_WEBHOOK_URL")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "iutbet.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "change_this_secret_in_prod"
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 4MB limit

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
            is_admin INTEGER DEFAULT 0
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
        # sample users (admin + two users)
        c.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (?, ?, ?, ?)",
                  ("admin", generate_password_hash("adminpass"), 0, 1))
        c.execute("INSERT INTO users (username, password, balance) VALUES (?, ?, ?)",
                  ("alice", generate_password_hash("password"), 5000))
        c.execute("INSERT INTO users (username, password, balance) VALUES (?, ?, ?)",
                  ("bob", generate_password_hash("password"), 2000))
        # sample matches
        sample_matches = [
            ("Coupe du Proviseur - Finale", "IUT Team A", "IUT Team B", "2025-11-10 15:00", "Coupe du Proviseur", "Football", "scheduled", 1.8, 3.2, 2.0, 0, 0, NULL),
            ("Rally de Dschang - Etape 3", "Pilote A", "Pilote B", "2025-11-12 18:00", "Rally de Dschang", "Rally", "scheduled", 2.0, 3.5, 1.9, 0, 0, NULL),
            ("Jeux Universitaires - Match Amical", "Equipe Dschang", "Equipe Foumban", "2025-11-05 10:00", "Jeux Universitaires", "Football", "scheduled", 1.6, 3.4, 2.8, 0, 0, NULL),
            ("Coupe du Doyen - Demi", "Dept Informatique", "Dept Genie", "2025-11-07 14:00", "Coupe du Doyen", "Basketball", "scheduled", 1.9, 3.1, 2.2, 0, 0, NULL)
        ]
        c.executemany("""INSERT INTO matches (title, team_a, team_b, start_time, competition, discipline, status, odds_a, odds_x, odds_b, score_a, score_b, image)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", sample_matches)
        conn.commit()
        conn.close()

init_db()


def ensure_payments_table():
    conn = get_db(); c = conn.cursor()
    # Table des dépôts
    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        operator TEXT,
        status TEXT,
        provider_tx_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Table des retraits
    c.execute("""
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        phone TEXT,
        operator TEXT,
        status TEXT DEFAULT 'PENDING',
        provider_tx_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)
    conn.commit(); conn.close()

ensure_payments_table()
#Pawapay simulation database
PAWAPAY_SIM_DB = os.path.join(os.path.dirname(__file__), 'data', 'pawapay_sim.json')

def ensure_pawapay_sim_db():
    if not os.path.exists(PAWAPAY_SIM_DB):
        with open(PAWAPAY_SIM_DB, 'w', encoding='utf-8') as f:
            json.dump({}, f)

ensure_pawapay_sim_db()


# helpers
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def refresh_session_user():
    if 'user' in session:
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT id, username, balance, is_admin FROM users WHERE id=?", (session['user']['id'],))
        u = c.fetchone(); conn.close()
        if u:
            session['user'] = dict(u)
        else:
            session.pop('user', None)

def process_finished_matches():
    """Traite tous les paris non-traités des matchs terminés"""
    conn = get_db()
    c = conn.cursor()
    
    # Récupérer tous les paris non traités des matchs terminés
    c.execute("""
        SELECT b.*, m.score_a, m.score_b, m.odds_a, m.odds_x, m.odds_b 
        FROM bets b 
        JOIN matches m ON m.id = b.match_id 
        WHERE m.status = 'finished' AND b.processed = 0
    """)
    bets = c.fetchall()
    
    for bet in bets:
        won = False
        odds_used = None

        # Vérifier le résultat du match et le choix du pari
        if bet['score_a'] > bet['score_b']:  # L'équipe A gagne
            if bet['choice'] == '1':  # Pari sur équipe A
                won = True
                odds_used = bet['odds_a']
        elif bet['score_a'] < bet['score_b']:  # L'équipe B gagne
            if bet['choice'] == '2':  # Pari sur équipe B
                won = True
                odds_used = bet['odds_b']
        else:  # Match nul
            if bet['choice'] == 'X':  # Pari sur match nul
                won = True
                odds_used = bet['odds_x']

        # Si le pari est gagné, calculer et créditer les gains
        if won and odds_used:
            winnings = int(bet['amount'] * odds_used)
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?",
                    (winnings, bet['user_id']))
            c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
                    (bet['user_id'], winnings, 'bet_win'))

        # Marquer le pari comme traité
        c.execute("UPDATE bets SET processed = 1, won = ? WHERE id = ?",
                (1 if won else 0, bet['id']))
    
    conn.commit()
    conn.close()

@app.before_request
def before():
    refresh_session_user()
    # update match statuses based on timeouts
    try:
        update_match_statuses()
        process_finished_matches()  # Traiter les paris après la mise à jour des statuts
    except Exception as e:
        print("Erreur lors de la mise à jour des matchs:", e)
        pass

# Public pages
@app.route('/')
def index():
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches ORDER BY start_time DESC LIMIT 8")
    matches = c.fetchall()
    conn.close()
    featured = matches[:4]
    return render_template('index_v2.html', matches=matches, featured=featured)

@app.route('/matches')
def matches_page():
    return render_template('matches_page.html')

@app.route('/match/<int:match_id>')
def match_detail(match_id):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    m = c.fetchone(); conn.close()
    if not m:
        abort(404)
    return render_template('match_v2.html', match=m)

# Authentication
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (username,))
        u = c.fetchone(); conn.close()
        if u and check_password_hash(u['password'], password):
            session['user'] = dict(u)
            return redirect(url_for('index'))
        return render_template('login_v2.html', error='Identifiants incorrects')
    return render_template('login_v2.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        hashed = generate_password_hash(password)
        conn = get_db(); c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password, balance) VALUES (?, ?, ?)", (username, hashed, 0))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except Exception as e:
            conn.close()
            return render_template('register_v2.html', error='Nom déjà pris')
    return render_template('register_v2.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))


# Deposit status and simulation
@app.route('/deposit_status/<txid>')
def deposit_status(txid):
    with open(PAWAPAY_SIM_DB, 'r', encoding='utf-8') as f:
        data = json.load(f)
    info = data.get(txid)
    if not info:
        flash('Transaction introuvable', 'error'); return redirect(url_for('deposit'))
    return render_template('deposit_status.html', info=info, txid=txid)


# Page de dépôt (GET) - affiche le formulaire
@app.route('/deposit', methods=['GET'])
def deposit_page():
    # si l'utilisateur n'est pas connecté, rediriger vers login
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('deposit_v2.html')


# === DEPOT ===
@app.route("/api/deposit", methods=["POST"])
def api_deposit():
    """Endpoint de dépôt simplifié - simulation locale"""
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json() or {}
    phone = data.get('phone')
    operator = data.get('operator')
    try:
        amount = int(data.get('amount', 0))
    except:
        return jsonify({'error': 'Montant invalide'}), 400
    
    if not phone or not operator or amount < 100:
        return jsonify({'error': 'Données manquantes ou invalides'}), 400

    # Créer un ID unique pour la transaction
    user_id = session['user']['id']
    provider_tx_id = f"DEPOT_{user_id}_{uuid.uuid4().hex[:8]}"

    # Enregistrer le paiement en base
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO payments (user_id, amount, operator, status, provider_tx_id) VALUES (?, ?, ?, ?, ?)",
              (user_id, amount, operator, 'PENDING', provider_tx_id))
    
    # Pour la démo, on crédite directement le compte (en prod, attendrait confirmation Mobile Money)
    c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
    c.execute("UPDATE payments SET status = ? WHERE provider_tx_id = ?", ('FINISHED', provider_tx_id))
    c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
              (user_id, amount, 'deposit'))
    conn.commit()
    
    # Recharger le solde en session
    c.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
    new_balance = c.fetchone()['balance']
    conn.close()
    session['user']['balance'] = new_balance

    # Simuler l'enregistrement Pawapay
    with open(PAWAPAY_SIM_DB, 'r+', encoding='utf-8') as f:
        data = json.load(f)
        data[provider_tx_id] = {
            'status': 'FINISHED',
            'amount': amount,
            'operator': operator,
            'phone': phone,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        f.seek(0); f.truncate(); json.dump(data, f)

    return jsonify({'ok': True, 'provider_tx_id': provider_tx_id})

# === WEBHOOK ===
@app.route("/webhook/pawapay", methods=["POST"])
def webhook_pawapay():
    data = request.json
    print("🔔 Paiement reçu :", data)
    status = data.get("status")
    transaction_id = data.get('externalId') or data.get('provider_tx_id') or data.get('transactionId')

    # Update payments table and credit user on SUCCESSFUL
    if not transaction_id:
        return jsonify({'received': False, 'error': 'no external id provided'}), 400

    # mark in pawapay_sim_db if present
    try:
        with open(PAWAPAY_SIM_DB, 'r+', encoding='utf-8') as f:
            sim = json.load(f)
            if transaction_id in sim:
                sim[transaction_id]['status'] = status
                f.seek(0); f.truncate(); json.dump(sim, f)
    except Exception:
        pass

    conn = get_db(); c = conn.cursor()
    c.execute('SELECT * FROM payments WHERE provider_tx_id=?', (transaction_id,))
    p = c.fetchone()
    if not p:
        conn.close(); return jsonify({'received': True, 'note': 'payment not found locally'})

    if status == 'SUCCESSFUL':
        # credit user
        user_id = p['user_id']
        amount = p['amount']
        # update user balance
        c.execute('SELECT balance FROM users WHERE id=?', (user_id,))
        row = c.fetchone()
        if row:
            new_bal = (row['balance'] or 0) + amount
            c.execute('UPDATE users SET balance=? WHERE id=?', (new_bal, user_id))
            # create transaction record
            c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)", (user_id, amount, 'deposit'))
        # mark payment finished
        c.execute('UPDATE payments SET status=? WHERE id=?', ('FINISHED', p['id']))
        conn.commit(); conn.close()
        print(f"✅ Transaction confirmée : {transaction_id}")
    else:
        c.execute('UPDATE payments SET status=? WHERE id=?', (status or 'FAILED', p['id']))
        conn.commit(); conn.close()
        print(f"❌ Transaction échouée : {transaction_id}")

    return jsonify({'received': True})


def update_match_statuses():
    """Mets à jour les statuts des matches en fonction du temps écoulé.
    Utilise un mapping discipline->durée (minutes). Si l'heure actuelle est passée au-delà de start_time + durée, status -> 'finished'.
    """
    duration_map = {
        'Football': 90,
        'Basketball': 40,
        'Rally': 180,
        'Tennis': 180,
        'Handball': 60,
        'Default': 120
    }
    now = datetime.now(timezone.utc)
    conn = get_db(); c = conn.cursor()
    
    # D'abord, mettre à jour tous les matchs qui devraient être terminés
    c.execute("SELECT id, start_time, discipline, status FROM matches WHERE status != 'finished'")
    rows = c.fetchall()
    
    for r in rows:
        st_raw = r['start_time']
        if not st_raw:
            continue
        st = None
        try:
            # Essayer d'abord le format avec timezone
            st = datetime.fromisoformat(st_raw)
            # Si pas de timezone, ajouter UTC
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
        except Exception:
            try:
                # Format sans timezone
                st = datetime.strptime(st_raw, "%Y-%m-%d %H:%M")
                # Ajouter UTC
                st = st.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        disc = r['discipline'] or 'Default'
        dur = duration_map.get(disc, duration_map['Default'])
        end = st + timedelta(minutes=dur)
        if now >= end:
            # Marquer le match comme terminé
            match_id = r['id']
            c.execute("UPDATE matches SET status = 'finished' WHERE id = ?", (match_id,))
            conn.commit()
            match_id = r['id']
            c.execute("UPDATE matches SET status=? WHERE id=?", ('finished', match_id))

            # Process bets for this match
            c.execute("""SELECT b.*, m.score_a, m.score_b, m.odds_a, m.odds_x, m.odds_b 
                        FROM bets b 
                        JOIN matches m ON m.id = b.match_id 
                        WHERE b.match_id = ? AND b.processed = 0""", (match_id,))
            bets = c.fetchall()
            
            for bet in bets:
                won = False
                odds_used = None

                # Vérifier le résultat du match et le choix du pari
                if bet['score_a'] > bet['score_b']:  # L'équipe A gagne
                    if bet['choice'] == '1':  # Pari sur équipe A
                        won = True
                        odds_used = bet['odds_a']
                elif bet['score_a'] < bet['score_b']:  # L'équipe B gagne
                    if bet['choice'] == '2':  # Pari sur équipe B
                        won = True
                        odds_used = bet['odds_b']
                else:  # Match nul
                    if bet['choice'] == 'X':  # Pari sur match nul
                        won = True
                        odds_used = bet['odds_x']

                # Si le pari est gagné, calculer et créditer les gains
                if won and odds_used:
                    winnings = int(bet['amount'] * odds_used)
                    c.execute("UPDATE users SET balance = balance + ? WHERE id = ?",
                            (winnings, bet['user_id']))
                    c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
                            (bet['user_id'], winnings, 'bet_win'))

                # Marquer le pari comme traité avec son statut (gagné ou perdu)
                c.execute("UPDATE bets SET processed = 1, won = ? WHERE id = ?",
                        (1 if won else 0, bet['id']))
    conn.commit(); conn.close()

def init_bets_won_column():
    """Add won and processed columns to bets table if they don't exist."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("ALTER TABLE bets ADD COLUMN won INTEGER DEFAULT NULL")
    except:
        pass
    try:
        c.execute("ALTER TABLE bets ADD COLUMN processed INTEGER DEFAULT 0")
    except:
        pass
    conn.commit(); conn.close()

init_bets_won_column()# Admin payments view
@app.route('/admin/payments')
def admin_payments():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT p.*, u.username FROM payments p LEFT JOIN users u ON u.id = p.user_id ORDER BY p.created_at DESC")
    rows = c.fetchall(); conn.close()
    return render_template('admin_payments.html', payments=rows)


# Admin panel (only for is_admin users)
def admin_required():
    if 'user' not in session or session['user'].get('is_admin') != 1:
        abort(403)

@app.route('/admin')
def admin():
    admin_required()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM matches ORDER BY start_time DESC")
    matches = c.fetchall()
    conn.close()
    return render_template('admin_v2.html', matches=matches)

# API endpoints
# Portefeuille et retraits
@app.route('/wallet')
def wallet():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Récupérer l'historique des transactions (dépôts, retraits, paris, gains)
    c.execute("""
        SELECT amount, type, created_at 
        FROM transactions 
        WHERE user_id = ? 
        ORDER BY created_at DESC
        LIMIT 50
    """, (session['user']['id'],))
    
    transactions = c.fetchall()
    conn.close()
    
    return render_template('wallet.html', transactions=transactions)

@app.route('/api/withdraw', methods=['POST'])
def api_withdraw():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json()
    amount = data.get('amount')
    phone = data.get('phone')
    operator = data.get('operator')
    
    # Validations
    if not amount or amount < 500:
        return jsonify({'error': 'Montant minimum: 500F'}), 400
    if not phone or not phone.isdigit() or len(phone) != 9:
        return jsonify({'error': 'Numéro de téléphone invalide'}), 400
    if not operator in ['orange', 'mtn']:
        return jsonify({'error': 'Opérateur non supporté'}), 400
        
    user = session['user']
    if user['balance'] < amount:
        return jsonify({'error': 'Solde insuffisant'}), 400
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        # Générer un ID unique pour la transaction
        provider_tx_id = f"WITHDRAW_{user['id']}_{uuid.uuid4().hex[:8]}"
        
        # Pour la simulation, on va utiliser le même fichier JSON que pour les dépôts
        withdraw_data = {
            'amount': amount,
            'phone': phone,
            'operator': operator,
            'type': 'withdrawal',
            'status': 'PENDING'
        }
        
        with open(PAWAPAY_SIM_DB, 'r+') as f:
            data = json.load(f)
            data[provider_tx_id] = withdraw_data
            f.seek(0)
            f.truncate()
            json.dump(data, f)
        
        # Enregistrer le retrait
        c.execute("""
            INSERT INTO withdrawals (user_id, amount, phone, operator, provider_tx_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user['id'], amount, phone, operator, provider_tx_id))
        
        # Déduire le montant du solde
        c.execute("UPDATE users SET balance = balance - ? WHERE id = ?",
                 (amount, user['id']))
        
        # Ajouter la transaction
        c.execute("""
            INSERT INTO transactions (user_id, amount, type)
            VALUES (?, ?, ?)
        """, (user['id'], -amount, 'withdrawal'))
        
        conn.commit()
        
        # Mettre à jour le solde en session
        session['user']['balance'] -= amount
        
        return jsonify({
            'ok': True,
            'message': 'Retrait en cours de traitement',
            'provider_tx_id': provider_tx_id
        })
        
    except Exception as e:
        conn.rollback()
        return jsonify({'error': 'Erreur lors du retrait'}), 500
    finally:
        conn.close()

@app.route('/api/matches/<int:match_id>/bets', methods=['GET'])
def api_get_match_bets(match_id):
    """Get user's bets for a specific match"""
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    conn = get_db(); c = conn.cursor()
    c.execute("""SELECT * FROM bets 
                 WHERE match_id = ? AND user_id = ?
                 ORDER BY created_at DESC""", 
              (match_id, session['user']['id']))
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
    rows = c.fetchall(); conn.close()
    matches = [dict(r) for r in rows]
    return jsonify(matches)

@app.route('/api/matches', methods=['POST'])
def api_create_match():
    admin_required()
    title = request.form.get('title'); team_a = request.form.get('team_a'); team_b = request.form.get('team_b')
    start_time_raw = request.form.get('start_time')
    competition = request.form.get('competition'); discipline = request.form.get('discipline')
    
    # Convertir start_time en UTC si ce n'est pas déjà fait
    try:
        start_time_dt = datetime.fromisoformat(start_time_raw)
        if start_time_dt.tzinfo is None:
            start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
        start_time = start_time_dt.isoformat()
    except Exception:
        try:
            start_time_dt = datetime.strptime(start_time_raw, "%Y-%m-%d %H:%M")
            start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
            start_time = start_time_dt.isoformat()
        except Exception:
            return jsonify({"error": "Format de date invalide"}), 400

    try:
        odds_a = float(request.form.get('odds_a', 1.5)); odds_x = float(request.form.get('odds_x', 3.0)); odds_b = float(request.form.get('odds_b', 2.5))
    except Exception:
        return jsonify({"error":"Cotes invalides"}), 400
    image_filename = None
    if 'image' in request.files:
        f = request.files['image']
        if f and allowed_file(f.filename):
            filename = secure_filename(f.filename)
            now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            filename = f"{now}_{filename}"
            dest = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            f.save(dest)
            image_filename = filename
    conn = get_db(); c = conn.cursor()
    c.execute("""INSERT INTO matches (title, team_a, team_b, start_time, competition, discipline, odds_a, odds_x, odds_b, image)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (title, team_a, team_b, start_time, competition, discipline, odds_a, odds_x, odds_b, image_filename))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return jsonify({"ok":True, "id": new_id})

@app.route('/api/match/<int:match_id>/update_scores', methods=['POST'])
def api_update_scores(match_id):
    admin_required()
    score_a = int(request.form.get('score_a', 0))
    score_b = int(request.form.get('score_b', 0))
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE matches SET score_a=?, score_b=?, status=? WHERE id=?", (score_a, score_b, 'live', match_id))
    conn.commit(); conn.close()
    return jsonify({"ok":True})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
# Competition matches view
@app.route('/competitions')
def competitions():
    """
    Affiche la page des compétitions de l'IUT.
    """
    return render_template("competition.html")


@app.route('/place_bet', methods=['POST'])
def place_bet():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    user = session['user']
    match_id = int(request.form['match_id'])
    choice = request.form['choice']
    
    try:
        amount = int(request.form['amount'])
    except:
        return jsonify({'error': 'Montant invalide'}), 400
    if amount < 100 or amount > 5000:
        return jsonify({'error': 'Montant hors limites (100 - 5000)'}), 400
        
    # Vérifier si l'utilisateur a déjà parié sur ce match
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM bets WHERE user_id = ? AND match_id = ?", (user['id'], match_id))
    existing_bet = c.fetchone()
    if existing_bet:
        conn.close()
        return jsonify({'error': 'Vous avez déjà parié sur ce match'}), 400
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (user['id'],))
    row = c.fetchone()
    if not row or row['balance'] < amount:
        conn.close(); return jsonify({'error': 'Solde insuffisant'}), 400
    new_bal = row['balance'] - amount
    c.execute("UPDATE users SET balance=? WHERE id=?", (new_bal, user['id']))
    c.execute("INSERT INTO bets (user_id, match_id, choice, amount) VALUES (?, ?, ?, ?)", (user['id'], match_id, choice, amount))
    c.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)", (user['id'], -amount, 'bet'))
    conn.commit(); conn.close()
    
    session['user']['balance'] = new_bal
    return jsonify({'ok': True, 'balance': new_bal})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
