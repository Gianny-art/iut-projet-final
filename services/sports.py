def match_outcome(score_a, score_b):
    if score_a > score_b:
        return '1'
    if score_a < score_b:
        return '2'
    return 'X'


def prediction_points(choice, score_a, score_b):
    return 3 if choice == match_outcome(score_a, score_b) else 0


def bet_payout(amount, odds):
    return int(amount * odds)