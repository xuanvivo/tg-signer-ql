"""Read bot histories to show balances, redemption prices and remaining gaps."""
import asyncio
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import os
import re

BOTS = ('gemini_plusv_bot', 'sheeridverifier_bot')
NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'


def number(value):
    return Decimal(str(value).replace(',', ''))


def display(value):
    return format(number(value).normalize(), 'f')


def prefer(current, candidate):
    """Service quotations take precedence over the bot's stale welcome menu."""
    return candidate if not current or (candidate.get('rank', 0), candidate['at']) > (current.get('rank', 0), current['at']) else current


def parse_history(bot, messages):
    wallet, rules = {}, {'products': {}}

    def balance(key, value, at):
        wallet[key] = prefer(wallet.get(key), {'value': display(value), 'at': at})

    def product(key, label, cost, at, rank):
        if number(cost) > 0:
            rules['products'][key] = prefer(rules['products'].get(key),
                {'label': label, 'cost': display(cost), 'at': at, 'rank': rank})

    for message in messages:
        text, at = message['text'], message['date']
        if bot == BOTS[0]:
            for key, unit in [('points', 'Points?'), ('credits', 'Credits?')]:
                # Explicit balances only: "Credits: +0" is a gift, not a balance.
                pattern = rf'^[^\w\n]*(?:(?:Your|Total|Remaining|Current)\s+)?{unit}\s*[:：]\s*({NUMBER})(?![\d,])'
                for match in re.finditer(pattern, text, re.I | re.M):
                    balance(key, match[1], at)
            match = re.search(rf'New Balance\s*[:：]\s*({NUMBER})\s*credits?\s*/\s*({NUMBER})\s*points?', text, re.I)
            if match:
                balance('credits', match[1], at)
                balance('points', match[2], at)
            match = re.search(rf'({NUMBER})\s*points?\s*=\s*({NUMBER})\s*credits?', text, re.I)
            if match and number(match[1]) > 0 and number(match[2]) > 0:
                rules['rate'] = prefer(rules.get('rate'), {'value': display(number(match[1]) / number(match[2])), 'at': at})
            for match in re.finditer(rf'Gemini\s+(Plus|Pro)\s*[—–-]\s*(\d+)\s*Months?\s*[→>]\s*({NUMBER})\s*Credits?', text, re.I):
                product(match[1].lower(), match[1].title() + match[2] + '月', match[3], at, 1)
        elif bot == BOTS[1]:
            for match in re.finditer(rf'(?:平衡|(?:您的)?余额|目前可用|当前可用|硬币数量|(?:Current\s+)?Balance|Currently available)\s*[:：]\s*({NUMBER})', text, re.I):
                balance('coins', match[1], at)
            for match in re.finditer(rf'(?:双子座专业版|Gemini\s+Pro)\s*[:：]\s*({NUMBER})\s*(?:硬币|coins?)', text, re.I):
                product('pixel', 'Pro1年', match[1], at, 1)
            for match in re.finditer(rf'(?:纯粹\s*ID|SheerID)\s*[:：]\s*({NUMBER})\s*(?:硬币|coins?)', text, re.I):
                product('verify', '验证', match[1], at, 1)
            if re.search(r'执行验证|perform.*verif', text, re.I):
                match = re.search(rf'(?:需要|Required|Need)\s*[:：]\s*({NUMBER})\s*(?:硬币|coins?)', text, re.I)
                if match:
                    product('verify', '验证', match[1], at, 2)
            match = re.search(rf'(?:像素费用|Pixel\s+(?:fee|cost))\s*[:：]\s*({NUMBER})\s*(?:硬币|coins?)', text, re.I)
            if match:
                product('pixel', 'Pro1年', match[1], at, 2)
            for match in re.finditer(rf'Gemini\s+Pro\s*(\d+)\s*(?:个月|months?).*?[—→]\s*({NUMBER})\s*(?:xu|硬币|coins?)', text, re.I):
                label = match[1] + '月链接' + ('(无保修)' if '无保修' in match[0] else '')
                product('link_' + match[1], label, match[2], at, 2)
    return {'wallet': wallet, 'rules': rules}


def merge_rules(destination, incoming):
    for bot, rules in incoming.items():
        dest = destination.setdefault(bot, {'products': {}})
        if rules.get('rate'):
            dest['rate'] = prefer(dest.get('rate'), rules['rate'])
        for key, value in rules.get('products', {}).items():
            dest['products'][key] = prefer(dest['products'].get(key), value)


async def _collect(accounts, tg, wanted, saved_rules):
    from pyrogram import Client

    context = {'accounts': {}, 'rules': saved_rules, 'warnings': [], 'bots': wanted}
    for account, session in accounts:
        label = tg.account_label(account, session)
        print('[兑换信息] 读取 ' + label + ' 最近的余额与报价', flush=True)
        context['accounts'][label] = {}
        kwargs = {'session_string': session, 'in_memory': True} if session else {'workdir': str(tg.SESSION_DIR)}
        client = Client('rewards_readonly' if session else account,
            api_id=int(os.environ.get('TG_API_ID') or 611335),
            api_hash=os.environ.get('TG_API_HASH') or 'd524b414d21f4d37f08684c1df41ac9c',
            proxy=tg.parse_proxy(), no_updates=True, **kwargs)
        try:
            await asyncio.wait_for(client.connect(), 20)
            for bot in wanted:
                async def read():
                    messages = []
                    async for message in client.get_chat_history(bot, limit=250):
                        if not message.outgoing:
                            messages.append({'date': message.date.isoformat(sep=' '), 'text': message.text or message.caption or ''})
                    return messages

                try:
                    messages = await asyncio.wait_for(read(), 35)
                    parsed = parse_history(bot, messages)
                    context['accounts'][label][bot] = parsed['wallet']
                    merge_rules(context['rules'], {bot: parsed['rules']})
                except Exception as error:
                    context['warnings'].append(label + '/' + bot + ': ' + type(error).__name__)
        except Exception as error:
            context['warnings'].append(label + ': ' + type(error).__name__)
        finally:
            if client.is_connected:
                try:
                    await asyncio.wait_for(client.disconnect(), 10)
                except Exception as error:
                    context['warnings'].append(label + '/disconnect: ' + type(error).__name__)
    return context


def collect_context(accounts, rows, tg):
    wanted = [bot for bot in BOTS if any(e['t'] == 'reply' and e['from'].lstrip('@').lower() == bot for r in rows for e in r['events'])]
    if not wanted:
        return None
    cache = tg.DATA_DIR / 'reward_rules.json'
    try:
        rules = json.loads(cache.read_text())
    except (OSError, ValueError):
        rules = {}
    try:
        context = asyncio.run(_collect(accounts, tg, wanted, rules))
    except Exception as error:
        print('[兑换信息] ' + type(error).__name__)
        return {'accounts': {}, 'rules': rules, 'warnings': ['读取未完成'], 'bots': wanted}
    try:
        stage = cache.with_suffix('.tmp')
        stage.write_text(json.dumps(context['rules'], ensure_ascii=False, indent=2))
        stage.replace(cache)
    except Exception as error:
        context['warnings'].append('报价缓存: ' + type(error).__name__)
    for warning in context['warnings']:
        print('[兑换信息] ' + warning)
    return context


def _products(bot, rules):
    products = rules.get('products', {})
    order = ['plus', 'pro'] if bot == BOTS[0] else ['verify', 'pixel'] + sorted(k for k in products if k.startswith('link_'))
    return [(key, products[key]) for key in order if key in products]


def _status(events, bot):
    replies = [e for e in events if e['t'] == 'reply' and e['from'].lstrip('@').lower() == bot]
    if not replies:
        return '⚠️未收到回复'
    data = replies[-1]['data']
    if data.get('status', ('',))[0] == '🔁':
        return '已签'
    if data.get('status'):
        return '+' + data['earned'] + data.get('earned_unit', '') if data.get('earned') else '成功'
    return '回复待确认'


def _wallet_value(wallet, key):
    return number(wallet[key]['value']) if key in wallet else None


def _progress(bot, wallet, rules):
    products = _products(bot, rules)
    if bot == BOTS[0]:
        points, credits = _wallet_value(wallet, 'points'), _wallet_value(wallet, 'credits')
        balance = (display(points) if points is not None else '?') + '积分+' + (display(credits) if credits is not None else '?') + '额度'
        rate = number(rules['rate']['value']) if rules.get('rate') else None
        bits = []
        if points is not None and rate is not None:
            remainder = points % rate
            convertible = (points / rate).to_integral_value(rounding=ROUND_FLOOR)
            if convertible:
                bits.append('可换' + display(convertible) + '额度，再1额度差' + display(rate - remainder) + '分')
            else:
                bits.append('换1额度差' + display(rate - points) + '分')
        for key, product in products:
            name, cost = ('Plus' if key == 'plus' else 'Pro'), number(product['cost'])
            if points is None or credits is None or rate is None:
                bits.append(name + '差额待查')
                continue
            available = credits + (points / rate).to_integral_value(rounding=ROUND_FLOOR)
            if available >= cost:
                bits.append(name + '可兑' + display((available / cost).to_integral_value(rounding=ROUND_FLOOR)) + '次')
            else:
                required = (cost - credits).to_integral_value(rounding=ROUND_CEILING) * rate
                bits.append(name + '差' + display(max(Decimal(0), required - points)) + '分')
    else:
        coins = _wallet_value(wallet, 'coins')
        balance = (display(coins) if coins is not None else '?') + '币'
        bits = []
        for key, product in products:
            name = {'verify': 'SheerID验证', 'pixel': '年版激活'}.get(key, product['label'].split('(')[0])
            cost = number(product['cost'])
            if coins is None:
                bits.append(name + '差额待查')
            elif coins >= cost:
                bits.append(name + '可兑' + display((coins / cost).to_integral_value(rounding=ROUND_FLOOR)) + '次')
            else:
                bits.append(name + '差' + display(cost - coins) + '币')
    return balance + ('｜' + '／'.join(bits) if bits else '｜兑换价待查')


def render_report(rows, context, verdict):
    lines = [verdict]
    for bot in context.get('bots', BOTS):
        if bot not in context['rules'] and not any(bot in a for a in context['accounts'].values()):
            continue
        rules = context['rules'].get(bot, {})
        unit = '额度' if bot == BOTS[0] else '币'
        prices = []
        for key, product in _products(bot, rules):
            label = product['label']
            if bot == BOTS[1]:
                label = {'verify': 'SheerID验证', 'pixel': 'Gemini Pro1年激活'}.get(key, 'Pro' + label)
            prices.append(label + product['cost'] + unit)
        if bot == BOTS[0] and rules.get('rate'):
            prices.insert(0, rules['rate']['value'] + '积分=1额度')
        title = '🎁 Gemini会员兑换' if bot == BOTS[0] else '🛠 SheerID验证／Gemini激活与链接'
        reward = '签到得积分' if bot == BOTS[0] else '签到得硬币'
        lines += ['', title + '（@' + bot + '）', reward + '｜' + '；'.join(prices)]
        for row in rows:
            wallet = context['accounts'].get(row['label'], {}).get(bot, {})
            lines.append(row['label'] + ' ' + _status(row['events'], bot) + '｜' + _progress(bot, wallet, rules))
    lines += ['', '两个频道余额独立；差额按最近报价估算，各项目单独计算']
    return '\n'.join(lines)
