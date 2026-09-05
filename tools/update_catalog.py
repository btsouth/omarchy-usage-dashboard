#!/usr/bin/env python3
"""Rebuild the attributed pricing subset from an explicit LiteLLM revision."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import urllib.request

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('revision',help='full 40-character upstream Git commit SHA')
args=parser.parse_args()
if not re.fullmatch('[0-9a-f]{40}',args.revision):parser.error('Use a full lowercase commit SHA')
url=f'https://raw.githubusercontent.com/BerriAI/litellm/{args.revision}/model_prices_and_context_window.json'
with urllib.request.urlopen(url,timeout=30) as response:raw=json.load(response)
models={}
for name,rate in raw.items():
    if isinstance(rate,dict) and rate.get('litellm_provider') in ('openai','anthropic','gemini'):
        fields={k:v for k,v in rate.items() if ('cost_per_token' in k or k.startswith('cache_')) and isinstance(v,(int,float))}
        if fields:models[name]=fields
if not models:raise SystemExit('No eligible models found; catalog not changed')
value={'source':'LiteLLM pricing snapshot','revision':args.revision,'url':url,
       'fetchedAtMs':int(dt.datetime.now().timestamp()*1000),'document':models}
path=Path(__file__).resolve().parents[1]/'catalog.json';path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(f'Updated {len(models)} model entries. Review rates, licensing, and THIRD_PARTY_NOTICES.md before release.')
