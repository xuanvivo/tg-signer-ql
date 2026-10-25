import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tg_rewards as rewards

spec = importlib.util.spec_from_file_location('tg_progress', ROOT / 'tg_signer_ql.py')
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)


def fixture():
    context = {'accounts': {}, 'rules': {}, 'warnings': [], 'bots': list(rewards.BOTS)}
    for line in (ROOT / 'tests/fixtures/bot-history.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        data = rewards.parse_history(record['bot'], record['messages'])
        context['accounts'].setdefault(record['account'], {})[record['bot']] = data['wallet']
        rewards.merge_rules(context['rules'], {record['bot']: data['rules']})
    return context


class ExchangeTests(unittest.TestCase):
    def test_actual_exchanges_replace_old_points_balances(self):
        context = fixture()
        for name in ['Example A', 'Example B', 'Example C']:
            wallet = context['accounts'][name][rewards.BOTS[0]]
            self.assertEqual((wallet['points']['value'], wallet['credits']['value']), ('15', '5'))
            self.assertEqual(context['accounts'][name][rewards.BOTS[1]]['coins']['value'], '7')

    def test_actual_service_quotes_override_stale_menu(self):
        rules = fixture()['rules'][rewards.BOTS[1]]['products']
        self.assertEqual((rules['verify']['cost'], rules['pixel']['cost'], rules['link_18']['cost']), ('25', '10', '30'))
        self.assertIn('无保修', rules['link_18']['label'])

    def test_gift_rewards_are_not_balances(self):
        data = rewards.parse_history(rewards.BOTS[0], [
            {'date': '2026-10-01', 'text': 'New Balance: 2.5 credits / 40 points'},
            {'date': '2026-10-02', 'text': '🎁 Credits: +0\n🎯 Points: +15'}])
        self.assertEqual((data['wallet']['credits']['value'], data['wallet']['points']['value']), ('2.5', '40'))

    def test_gaps_and_ready_redemptions(self):
        context = fixture()
        rules = context['rules'][rewards.BOTS[0]]
        wallet = {'points': {'value': '15'}, 'credits': {'value': '0'}}
        text = rewards._progress(rewards.BOTS[0], wallet, rules)
        self.assertIn('换1额度差35分', text)
        self.assertIn('Plus差35分', text)
        self.assertIn('Pro差185分', text)
        wallet['credits']['value'] = '5'
        text = rewards._progress(rewards.BOTS[0], wallet, rules)
        self.assertIn('Plus可兑5次', text)
        self.assertIn('Pro可兑1次', text)

    def test_partial_credits_require_whole_credit_exchange(self):
        rules = fixture()['rules'][rewards.BOTS[0]]
        wallet = {'points': {'value': '15'}, 'credits': {'value': '2.5'}}
        self.assertIn('Pro差85分', rewards._progress(rewards.BOTS[0], wallet, rules))

    def test_sheerid_remaining_coins_and_threshold(self):
        rules = fixture()['rules'][rewards.BOTS[1]]
        text = rewards._progress(rewards.BOTS[1], {'coins': {'value': '7'}}, rules)
        self.assertIn('验证差18币', text)
        self.assertIn('年版激活差3币', text)
        self.assertIn('18月链接差23币', text)
        text = rewards._progress(rewards.BOTS[1], {'coins': {'value': '25'}}, rules)
        self.assertIn('验证可兑1次', text)
        self.assertNotIn('差0', text)

    def test_missing_wallet_never_looks_like_zero_balance(self):
        context = fixture()
        text = rewards._progress(rewards.BOTS[0], {}, context['rules'][rewards.BOTS[0]])
        self.assertIn('?积分+?额度', text)
        self.assertIn('差额待查', text)
        self.assertNotIn('差200', text)

    def test_changed_price_and_rate_update_calculation(self):
        context = fixture()
        newer = rewards.parse_history(rewards.BOTS[1], [{'date': '2026-10-26', 'text': '您没有足够的硬币来执行验证。\n需要：20 硬币'}])
        rewards.merge_rules(context['rules'], {rewards.BOTS[1]: newer['rules']})
        self.assertIn('验证差13币', rewards._progress(rewards.BOTS[1], {'coins': {'value': '7'}}, context['rules'][rewards.BOTS[1]]))

    def test_report_preserves_duplicate_status_and_failures(self):
        context = fixture()
        rows = [{'label': name, 'ok': True, 'events': [
            {'t': 'reply', 'from': bot, 'text': '今天已经签到了', 'data': {'status': ('🔁', '今日已签到')}}
            for bot in rewards.BOTS]} for name in context['accounts']]
        rows[1]['ok'] = False
        rows[1]['events'].append({'t': 'error', 'text': 'FLOOD_WAIT'})
        report = tg.render_report(rows, [], 0, context)
        self.assertIn('⚠️ 部分成功 2/3', report)
        self.assertIn('签到得积分｜50积分=1额度；Plus12月1额度；Pro18月4额度', report)
        self.assertIn('签到得硬币｜SheerID验证25币；Gemini Pro1年激活10币；Pro18月链接(无保修)30币', report)
        self.assertEqual(report.count('验证差18币'), 3)
        self.assertIn('Example B：❌', report)
        self.assertNotIn('265', report)
        self.assertNotIn('+5积分', report)

    def test_channel_sections_explain_purpose_and_keep_balances_separate(self):
        context = fixture()
        row = {'label': 'Example A', 'ok': True, 'events': [
            {'t': 'reply', 'from': bot, 'text': '今天已经签到了', 'data': {'status': ('🔁', '今日已签到')}}
            for bot in rewards.BOTS]}
        context['accounts']['Example A'][rewards.BOTS[0]]['credits']['value'] = '0'
        report = tg.render_report([row], [], 0, context)
        first, second = report.split('\n🛠 SheerID验证／Gemini激活与链接', 1)
        self.assertIn('🎁 Gemini会员兑换（@gemini_plusv_bot）', first)
        self.assertIn('15积分+0额度', first)
        self.assertIn('Plus差35分', first)
        self.assertNotIn('7币', first)
        self.assertNotIn('验证差18币', first)
        self.assertIn('（@sheeridverifier_bot）', second)
        self.assertIn('7币', second)
        self.assertIn('SheerID验证差18币', second)
        self.assertNotIn('积分+', second)
        self.assertNotIn('Plus差', second)


if __name__ == '__main__':
    unittest.main()
