import sys
import os
from pathlib import Path

# --- CONFIGUREZ CES VALEURS ---
# Remplacez <PA_USER> par votre nom d'utilisateur PythonAnywhere
# Remplacez <VENV_NAME> par le nom de votre virtualenv (si vous en utilisez un)
PYUSER = '<PA_USER>'
PROJECT_DIR = f'/home/{PYUSER}/iut_bet_project_final'
VENV_NAME = '<VENV_NAME>'  # ex: .venv or myenv
PYTHON_VERSION = '3.11'   # modifiez si nécessaire

# --- Ajouter le projet au PYTHONPATH ---
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# --- Virtualenv site-packages (optionnel) ---
if VENV_NAME and VENV_NAME != '<VENV_NAME>':
    venv_site = f'/home/{PYUSER}/.virtualenvs/{VENV_NAME}/lib/python{PYTHON_VERSION}/site-packages'
    if os.path.isdir(venv_site) and venv_site not in sys.path:
        sys.path.insert(0, venv_site)

# --- Variables d'environnement à définir pour votre projet ---
# Remplacez les valeurs ci-dessous directement sur PythonAnywhere (Web > Environment variables)
# ou modifiez ici temporairement (ne mettez jamais vos secrets en clair dans le repo public).
os.environ.setdefault('FLASK_APP', 'app')
# En production, mieux vaut laisser FLASK_ENV non défini ou le mettre à 'production'
os.environ.setdefault('FLASK_ENV', 'production')
# Désactiver le debug
os.environ.setdefault('FLASK_DEBUG', '0')

# Vars spécifiques à ce projet
# Exemple (laissez vides ici et configurez via l'interface PythonAnywhere):
os.environ.setdefault('PAWAPAY_API_TOKEN', '')
os.environ.setdefault('PAWAPAY_BASE', '')
os.environ.setdefault('MY_WEBHOOK_URL', '')
# Clé secrète pour Flask sessions (remplacez en production)
os.environ.setdefault('SECRET_KEY', 'change_me_in_prod')

# Si vous utilisez une base de données externe vous pouvez définir DATABASE_URL
# os.environ.setdefault('DATABASE_URL', 'sqlite:////home/<PA_USER>/iut_bet_project_final/data/iutbet.db')

# --- Option pour charger un .env local si vous en avez besoin (non recommandé en prod) ---
# from dotenv import load_dotenv
# load_dotenv(os.path.join(PROJECT_DIR, '.env'))

# --- Importer l'application WSGI ---
# Votre application expose `app` dans le module app.py
from app import app as application
