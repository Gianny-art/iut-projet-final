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
