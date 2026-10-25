"""Regression checks for the actual bot replies and their distinct currencies."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tg_rewards', ROOT / 'tg_signer_ql.py')
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

GEMINI = '✅ Check-in Successful!\n🎉 You earned 5 points!\n🎯 Total Points: 265\n📅 Next check-in: Tomorrow (00:00 UTC)'
SHEERID = '🎉 出席率 成功！\n💎 +1 硬币| $0.04\n💰 平衡： 7 硬币| $0.28\n📅 日期系列： 6 天'


def reply(source, text):
    return {'t': 'reply', 'from': source, 'text': text, 'data': tg.parse_reply(text)}


class RewardTests(unittest.TestCase):
    def test_gemini_points(self):
        data = tg.parse_reply(GEMINI)
        self.assertEqual((data['earned'], data['earned_unit']), ('5', '积分'))
        self.assertEqual((data['total'], data['total_unit']), ('265', '积分'))
        self.assertNotIn('earned_usd', data)

    def test_sheerid_coins_and_quoted_dollars(self):
        data = tg.parse_reply(SHEERID)
        self.assertEqual((data['earned'], data['earned_unit'], data['earned_usd']), ('1', '硬币', '0.04'))
        self.assertEqual((data['total'], data['total_unit'], data['total_usd']), ('7', '硬币', '0.28'))

    def test_different_units_in_one_reply(self):
        text = 'Check-in Successful! You earned 5 points! Total Credits: 1.5'
        data = tg.parse_reply(text)
        self.assertEqual((data['earned_unit'], data['total_unit'], data['total']), ('积分', '服务额度', '1.5'))
        row = {'label': 'Example A', 'ok': True, 'events': [reply('gemini_plusv_bot', text)]}
        self.assertIn('Gemini 积分+5（余额1.5服务额度）', tg.compact_line(row))

    def test_credit_translation_keeps_points_distinct(self):
        self.assertEqual(tg.translate('5 credits / 50 points'), '5 服务额度 / 50 积分')

    def test_already_checked_in_never_claims_new_reward(self):
        text = 'Already checked in today. Balance: 7 coins | $0.28'
        row = {'label': 'Example A', 'ok': True, 'events': [reply('sheeridverifier_bot', text)]}
        self.assertEqual(tg.compact_line(row), 'Example A：SheerID 已签（余额7硬币≈$0.28）')

    def test_no_invented_exchange_rate_or_currency(self):
        text = 'Check-in Successful! You earned 1 coin! Total Coins: 7'
        row = {'label': 'Other', 'ok': True, 'events': [reply('other_bot', text)]}
        self.assertNotIn('$', tg.compact_line(row))
        self.assertNotIn('用途', tg.render_report([row], [], 0))
        changed_rate = tg.parse_reply(SHEERID.replace('$0.04', '$0.05').replace('$0.28', '$0.35'))
        self.assertEqual((changed_rate['earned_usd'], changed_rate['total_usd']), ('0.05', '0.35'))

    def test_report_keeps_currencies_and_accounts_separate(self):
        events = [reply('gemini_plusv_bot', GEMINI), reply('sheeridverifier_bot', SHEERID)]
        rows = [{'label': label, 'ok': True, 'events': events} for label in ['Example A', 'Example B', 'Example C']]
        report = tg.render_report(rows, [], 74)
        for label in ['Example A', 'Example B', 'Example C']:
            self.assertIn(label + '：Gemini 积分+5（余额265）｜SheerID 硬币+1≈$0.04（余额7≈$0.28）', report)
        self.assertIn('Gemini积分兑换机器人服务额度', report)
        self.assertIn('美元为站内折算', report)
        self.assertIn('余额以本次回复为准', report)
        self.assertNotIn('+18', report)

    def test_detailed_reply_retains_units(self):
        text = '\n'.join(tg.render_reply(reply('sheeridverifier_bot', SHEERID)))
        self.assertIn('+1硬币（约$0.04）', text)
        self.assertIn('回复时余额 7硬币（约$0.28）', text)

    def test_errors_remain_visible(self):
        row = {'label': 'Example A', 'ok': False, 'events': [
            reply('gemini_plusv_bot', GEMINI), {'t': 'error', 'text': 'FLOOD_WAIT'}]}
        self.assertIn('❌', tg.compact_line(row))
        self.assertIn('❌ 全部失败', tg.render_report([row], [], 0))



if __name__ == '__main__':
    unittest.main()
