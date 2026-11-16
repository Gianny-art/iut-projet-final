import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

TEST_DATA_DIR = tempfile.mkdtemp(prefix='iutbet-tests-')
atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)
os.environ['IUTBET_DB_PATH'] = os.path.join(TEST_DATA_DIR, 'test.db')
os.environ['SECRET_KEY'] = 'test-session-key'
os.environ['ADMIN_PASSWORD'] = 'test-admin-password'
os.environ['SESSION_COOKIE_SECURE'] = '0'

from werkzeug.security import generate_password_hash

import app as app_module

app = app_module.app
app.config['TESTING'] = True
initial_conn = app_module.get_db()
INITIAL_USERNAMES = [row['username'] for row in initial_conn.execute('SELECT username FROM users')]
initial_conn.close()


class FinancialFlowTests(unittest.TestCase):
    def setUp(self):
        conn = app_module.get_db()
        cursor = conn.cursor()
        for table in ('bets', 'transactions', 'payments', 'withdrawals', 'notifications',
                      'predictions', 'matches', 'promotions', 'online_users', 'admin_audit'):
            cursor.execute(f'DELETE FROM {table}')
        cursor.execute("DELETE FROM users WHERE username != 'admin'")
        cursor.execute('UPDATE users SET balance=0 WHERE username=?', ('admin',))
        cursor.execute("INSERT INTO users (username, password, balance) VALUES (?, ?, ?)",
                       ('student', generate_password_hash('student-password'), 2000))
        self.user_id = cursor.lastrowid
        cursor.execute("""INSERT INTO matches
                       (title, team_a, team_b, start_time, competition, discipline, status,
                        odds_a, odds_x, odds_b)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       ('Test match', 'North', 'South', '2099-01-01T12:00:00+00:00',
                        'Test cup', 'Football', 'scheduled', 1.5, 3.0, 2.5))
        self.match_id = cursor.lastrowid
        conn.commit()
        conn.close()
        self.client = app.test_client()
        with self.client.session_transaction() as browser_session:
            browser_session['user'] = {'id': self.user_id}
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        pass

    def get_user_balance(self):
        conn = app_module.get_db()
        row = conn.execute('SELECT balance FROM users WHERE id=?', (self.user_id,)).fetchone()
        conn.close()
        return row['balance']

    def test_fresh_database_does_not_seed_demo_accounts(self):
        self.assertEqual(INITIAL_USERNAMES, ['admin'])

    def test_match_page_renders_free_prediction_control(self):
        response = self.client.get(f'/match/{self.match_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Pronostic gratuit', response.data)
        self.assertIn(b'name="csrf-token"', response.data)

    def test_correct_free_prediction_awards_three_points_once(self):
        response = self.client.post('/api/predictions', json={
            'match_id': self.match_id,
            'choice': '1',
        }, headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(response.status_code, 200)

        conn = app_module.get_db()
        conn.execute("UPDATE matches SET score_a=2, score_b=1, status='finished' WHERE id=?",
                     (self.match_id,))
        conn.commit()
        conn.close()
        app_module.process_finished_matches()
        app_module.process_finished_matches()

        conn = app_module.get_db()
        prediction = conn.execute('SELECT points_awarded FROM predictions WHERE user_id=?',
                                  (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(prediction['points_awarded'], 3)
        self.assertEqual(self.get_user_balance(), 2000)
        self.assertIn(b'3', self.client.get('/leaderboard').data)

    def test_bet_uses_the_odds_captured_when_placed(self):
        placed = self.client.post('/place_bet', data={
            'match_id': self.match_id,
            'choice': '1',
            'amount': '500',
        }, headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(placed.status_code, 200)
        conn = app_module.get_db()
        conn.execute("UPDATE matches SET odds_a=7, score_a=1, score_b=0, status='finished' WHERE id=?",
                     (self.match_id,))
        conn.commit()
        conn.close()
        app_module.process_finished_matches()
        self.assertEqual(self.get_user_balance(), 2250)

    def test_unverified_webhook_cannot_credit_account(self):
        provider = unittest.mock.Mock()
        provider.verify_webhook_signature.return_value = False
        with patch.object(app_module, 'get_provider', return_value=provider):
            response = self.client.post('/webhook/campay', json={
                'external_reference': 'DEP_unknown',
                'status': 'SUCCESSFUL',
            })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.get_user_balance(), 2000)

    def test_live_match_rejects_bet_without_charging_balance(self):
        conn = app_module.get_db()
        conn.execute("UPDATE matches SET status='live' WHERE id=?", (self.match_id,))
        conn.commit()
        conn.close()
        response = self.client.post('/place_bet', data={
            'match_id': self.match_id,
            'choice': '1',
            'amount': '500',
        }, headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get_user_balance(), 2000)

    def test_withdrawal_is_reserved_and_refunded_when_rejected(self):
        response = self.client.post('/api/withdraw', json={
            'operator': 'MTN',
            'phone': '699123456',
            'amount': 1000,
        }, headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_user_balance(), 1000)

        conn = app_module.get_db()
        withdrawal = conn.execute('SELECT id, reserved FROM withdrawals').fetchone()
        conn.close()
        self.assertEqual(withdrawal['reserved'], 1)

        admin_client = app.test_client()
        with admin_client.session_transaction() as browser_session:
            browser_session['user'] = {'id': 1}
            browser_session['_csrf_token'] = 'admin-csrf-token'
        rejected = admin_client.post(f"/admin/withdraw/{withdrawal['id']}/reject",
                                     headers={'X-CSRF-Token': 'admin-csrf-token'})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.get_user_balance(), 2000)

    def test_deposit_approval_cannot_credit_twice(self):
        conn = app_module.get_db()
        cursor = conn.cursor()
        cursor.execute("""INSERT INTO payments
                       (user_id, amount, operator, status, provider_tx_id, provider)
                       VALUES (?, ?, ?, 'PENDING', ?, 'manual')""",
                       (self.user_id, 750, 'MTN', 'DEP_test_once'))
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()

        admin_client = app.test_client()
        with admin_client.session_transaction() as browser_session:
            browser_session['user'] = {'id': 1}
            browser_session['_csrf_token'] = 'admin-csrf-token'
        headers = {'X-CSRF-Token': 'admin-csrf-token'}
        first = admin_client.post(f'/admin/payment/{payment_id}/approve', headers=headers)
        second = admin_client.post(f'/admin/payment/{payment_id}/approve', headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.get_user_balance(), 2750)

    def test_state_changing_request_requires_csrf_token(self):
        response = self.client.post('/place_bet', data={
            'match_id': self.match_id,
            'choice': '1',
            'amount': '500',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get_user_balance(), 2000)

    def test_logout_is_post_only_and_csrf_protected(self):
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        response = self.client.post('/logout', headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/wallet').status_code, 302)

    def test_admin_cannot_save_out_of_range_withdrawal_commission(self):
        admin_client = app.test_client()
        with admin_client.session_transaction() as browser_session:
            browser_session['user'] = {'id': 1}
            browser_session['_csrf_token'] = 'admin-csrf-token'
        response = admin_client.post('/admin/settings', data={
            'active_payment_provider': 'manual',
            'withdrawal_commission_pct': '500',
        }, headers={'X-CSRF-Token': 'admin-csrf-token'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app_module.get_setting('withdrawal_commission_pct', '1'), '1')

    def test_withdrawal_alert_is_private_to_admins(self):
        response = self.client.post('/api/withdraw', json={
            'operator': 'MTN',
            'phone': '699123456',
            'amount': 500,
        }, headers={'X-CSRF-Token': 'test-csrf-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/notifications').get_json(), [])

        admin_client = app.test_client()
        with admin_client.session_transaction() as browser_session:
            browser_session['user'] = {'id': 1}
            browser_session['_csrf_token'] = 'admin-csrf-token'
        alerts = admin_client.get('/api/notifications').get_json()
        self.assertEqual(len(alerts), 1)
        self.assertIn('Nouvelle demande de retrait', alerts[0]['message'])


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)