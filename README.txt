IUT Bet — Flask + SQLite backend, Android app via Capacitor
=============================================================

Lancer le site en local :
  1. python -m venv venv
  2. source venv/bin/activate  (ou venv\Scripts\activate sous Windows)
  3. pip install -r requirements.txt
  4. cp .env.example .env  puis remplir au minimum SECRET_KEY et ADMIN_PASSWORD
  5. python app.py
  6. Ouvrir http://127.0.0.1:5000 (ou le port affiché dans le terminal)

Compte admin :
  - Défini par ADMIN_PASSWORD dans .env au tout premier lancement (identifiant "admin").
  - Aller sur /admin pour gérer les matchs, /admin/payments, /admin/withdrawals,
    /admin/users, /admin/promotions et /admin/settings.

Paiements (Campay / Maviance) :
  - Tant qu'aucune clé n'est renseignée dans .env, les dépôts/retraits restent
    en mode manuel (code USSD affiché + validation par l'admin), comme avant.
  - Une fois les comptes marchands créés, renseignez les variables décrites
    dans .env.example (CAMPAY_*, MAVIANCE_*), puis activez le fournisseur
    voulu dans /admin/settings. Le code des providers est dans payments/.
  - Vérifiez les noms de champs exacts contre la documentation officielle de
    chaque fournisseur avant la mise en production (voir commentaires dans
    payments/campay.py et payments/maviance.py).

Application Android (APK) :
  - Le dossier android/ est un projet Capacitor déjà généré et configuré
    (icône, splash, nom "IUT Bet", package cm.iutbet.app) qui charge le site
    en ligne (capacitor.config.json > server.url).
  - Pour obtenir le fichier .apk :
      1. Installer Android Studio (https://developer.android.com/studio).
      2. Ouvrir le dossier android/ avec Android Studio (il installe son
         propre JDK/SDK au premier lancement).
      3. Build > Build App Bundle(s) / APK(s) > Build APK(s).
      4. Le fichier apparaît dans android/app/build/outputs/apk/debug/.
  - Pour une build signée destinée au Play Store ou à une distribution hors
    store, suivez le guide officiel Capacitor :
    https://capacitorjs.com/docs/android/deploying-to-google-play
  - Si l'URL du site change, mettre à jour "server.url" dans
    capacitor.config.json puis relancer `npx cap sync android`.
  - Pour régénérer icône/splash après avoir modifié assets/logo.png :
    `npm run gen:assets`.

Notes :
  - Les mots de passe sont hashés avec werkzeug.security.
  - 1 FCFA = 1 F sur la plateforme. Les dépôts/retraits sont de l'argent
    réel transféré via Mobile Money — vérifiez la conformité réglementaire
    locale avant d'opérer à plus grande échelle.
  - Disciplines supportées : Football, Basketball, Volleyball, Handball.
