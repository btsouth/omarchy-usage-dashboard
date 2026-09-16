#!/usr/bin/env python3
"""Refresh pricing.json from the OpenCode Go documentation table.

Reads https://opencode.ai/docs/go/, maps the documented model names through
the endpoint table to model ids, and regenerates rates, monthly limits, tier
rates, and peak multipliers. Entries not listed in the table are preserved as
manual overrides.

    python3 tools/update_go_pricing.py           # show what would change
    python3 tools/update_go_pricing.py --write   # update pricing.json
    python3 tools/update_go_pricing.py --check   # exit 1 when the file is stale
"""
import argparse
import datetime as dt
import html
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DOCS = 'https://opencode.ai/docs/go/'
PEAK_HOURS = [[1, 4], [6, 10]]
MONTHS = {name: number for number, name in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Omarchy-AI-Usage/0.1 (+pricing refresh)'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode('utf-8', 'replace')


def cell_text(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()


def tables(page):
    result = []
    for table in re.findall(r'<table.*?</table>', page, re.S):
        rows = [[cell_text(cell) for cell in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.S)]
                for row in re.findall(r'<tr>(.*?)</tr>', table, re.S)]
        if rows and rows[0]:
            result.append(rows)
    return result


def key_of(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def money(value, millions=True):
    match = re.search(r'\$([0-9.]+)', value)
    if not match:
        return None
    return round(float(match.group(1)) / (10 ** 6 if millions else 1), 15)


def monthly_limit(value, today):
    amounts = [float(amount) for amount in re.findall(r'\$([0-9.]+)', value)]
    if not amounts:
        return {}
    clean = lambda amount: int(amount) if amount.is_integer() else amount
    limit = {'monthly_limit_usd': clean(amounts[0])}
    if len(amounts) > 1:
        limit['monthly_limit_promo_usd'] = clean(amounts[1])
    ends = re.search(r'Ends (\w{3}) (\d+)', value)
    if ends and ends.group(1) in MONTHS:
        date = dt.date(today.year, MONTHS[ends.group(1)], int(ends.group(2)))
        if date < today - dt.timedelta(days=14):
            date = date.replace(year=date.year + 1)
        limit['promo_ends'] = str(date)
    return limit


def split_name(name):
    suffix = ''
    match = re.search(r'\((off-peak|peak|([<>≤≥]) ?(\d+)k tokens)\)', name, re.I)
    if match:
        if match.group(1).lower() in ('off-peak', 'peak'):
            suffix = match.group(1).lower()
        else:
            suffix = ('above' if match.group(2) in ('>', '≥') else 'atmost') + match.group(3) + 'k'
        name = name[:match.start()].strip()
    return name, suffix


def build(rows, ids, today):
    models, manual_names = {}, []
    for row in rows[1:]:
        if len(row) < 6:
            continue
        display, suffix = split_name(row[0])
        model = ids.get(key_of(display))
        if not model:
            manual_names.append(display)
            continue
        entry = models.setdefault(model, {'base': {}, 'peak': {}, 'tiers': {}, 'limit': {}})
        rates = {'input_cost_per_token': money(row[1]), 'output_cost_per_token': money(row[2]),
                 'cache_read_input_token_cost': money(row[3]), 'cache_creation_input_token_cost': money(row[4])}
        rates = {key: value for key, value in rates.items() if value is not None}
        if suffix == 'peak':
            entry['peak'] = rates
        elif suffix.startswith('above'):
            entry['tiers'][f'_above_{suffix[5:]}_tokens'] = rates
        else:
            entry['base'] = rates
            if not entry['limit']:
                entry['limit'] = monthly_limit(row[5], today)
    result = {}
    for model, entry in models.items():
        if not entry['base']:
            continue
        rates = dict(entry['base'])
        for suffix, tier in entry['tiers'].items():
            rates.update({key + suffix: value for key, value in tier.items()})
        if entry['peak']:
            ratios = {round(entry['peak'][key] / value, 4) for key, value in entry['base'].items() if key in entry['peak'] and value}
            if ratios == {2.0}:
                rates.update({'peak_cost_multiplier': 2, 'peak_hours_utc': PEAK_HOURS, 'peak_weekdays_only': True})
            else:
                raise SystemExit(f'Unexpected peak ratio for {model}: {ratios}')
        rates.update(entry['limit'])
        result[model] = rates
    return result, manual_names


def changes(old, new):
    diffs, manual = [], []
    for model, entry in new.items():
        if model not in old:
            diffs.append(f'add {model}')
            continue
        for key, value in entry.items():
            if old[model].get(key) != value:
                diffs.append(f'{model}: {key} {old[model].get(key)} -> {value}')
        for key in old[model]:
            if key not in entry:
                diffs.append(f'{model}: drop {key} (was {old[model][key]})')
    for model in old:
        if model not in new:
            manual.append(f'keep manual {model}')
    return diffs, manual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='update pricing.json in place')
    parser.add_argument('--check', action='store_true', help='exit 1 when pricing.json is stale')
    args = parser.parse_args()
    today = dt.datetime.now().astimezone().date()
    found = tables(fetch(DOCS))
    price_table = next((t for t in found if len(t[0]) >= 6 and t[0][0] == 'Model' and 'Monthly limit' in t[0]), None)
    id_table = next((t for t in found if t[0][:2] == ['Model', 'Model ID']), None)
    if not price_table or not id_table:
        raise SystemExit('Could not find the Go pricing or model id table on the docs page')
    ids = {}
    for row in id_table[1:]:
        if len(row) >= 2 and row[1]:
            ids[key_of(row[0])] = row[1]
    generated, missing = build(price_table, ids, today)
    path = ROOT / 'pricing.json'
    document = json.loads(path.read_text())
    old = {key.split('/', 1)[-1]: value for key, value in document['models'].items()}
    diff, manual = changes(old, generated)
    for line in diff:
        print(line)
    for line in manual:
        print(line)
    if missing:
        print('no model id for: ' + ', '.join(missing))
    if not diff:
        print('pricing.json matches the documentation')
    if args.check:
        raise SystemExit(1 if diff else 0)
    if not args.write:
        print(f'{len(diff)} change(s) not written; pass --write to update')
        return
    models = {}
    for key, value in document['models'].items():
        name = key.split('/', 1)[-1]
        models[key] = generated.pop(name) if name in generated and name in old else value
    for name, value in generated.items():
        models['opencode-go/' + name] = value
    document['models'] = models
    document['verifiedAt'] = str(today)
    path.write_text(json.dumps(document, indent=2) + '\n')
    print(f'Updated {path} with {len(models)} models; verified {document["verifiedAt"]}')


if __name__ == '__main__':
    main()
