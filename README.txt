IUT Bets - Final package (HTML/CSS/JS frontend + Flask backend + SQLite)
-----------------------------------------------------------------------
How to run:
  1. python -m venv venv
  2. source venv/bin/activate  (or venv\Scripts\activate on Windows)
  3. pip install -r requirements.txt
  4. python app.py
  5. Open http://127.0.0.1:5000
Admin:
  - default admin: admin / adminpass
  - go to /admin to create matches and upload images
Notes:
  - Passwords are hashed using werkzeug.security
  - Deposits are simulated (no real payment gateway)
  - Place bets on match pages (1F = 1 betcoin)

Changelog - Paiements & Admin (2026-01-11):
  - Les dépôts créent désormais une demande en statut PENDING et doivent être validés par l'admin.
  - La page de dépôt génère un code USSD dynamique (ORANGE / MTN) et un QR (Google Chart) que l'utilisateur peut utiliser pour lancer la transaction sur son téléphone. Bouton "Payer maintenant" (tel:USSD) et fonction "Copier" ajoutés.
  - Les admins peuvent valider ou rejeter les dépôts depuis /admin/payments.
  - Les demandes de retrait notifient l'admin; l'admin peut valider (marque FINISHED et prend 1% de commission) ou rejeter (rembourse l'utilisateur).
  - Notifications système et suivi des utilisateurs connectés ajoutés; endpoints API disponibles: /api/notifications, /api/online_users.
  - Tous les changements conservent la structure responsive et la logique UX existante.

Usage rapide (admin):
  - Aller dans /admin/payments pour valider/rejeter les dépôts.
  - Aller dans /admin/withdrawals pour gérer les retraits.

Note: Ce changelog est un résumé court; pour toute modification de logique (ex: commission différente, changement d'operateurs USSD), modifiez la fonction generateUSSD() dans templates/deposit_v2.html et les endpoints d'admin dans app.py.
