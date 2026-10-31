"""Import channel TD cells without guessing missing features or breaking IDs."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

from bs4 import BeautifulSoup
from bson import json_util
from bson.decimal128 import Decimal128

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telecom_support.account_tools import truth
from telecom_support.settings import ROOT

LANGUAGES = {'Tamil','Telugu','Kannada','Malayalam','Bengali','Marathi','Odia',
             'Hindi','English','Gujarathi','Punjabi','Bhojpuri'}
GENRES = {'News','Infotainment','Sports','Kids','Devotional'}


def identity(row):
    return (' '.join(row['channel_name'].casefold().split()),truth(row.get('is_hd')))


def parse_html(path):
    raw = Path(path).read_bytes()
    soup = BeautifulSoup(raw.decode('utf-8'),'html.parser')
    digest = hashlib.sha256(raw).hexdigest()
    records, seen = [], set()
    for table in soup.find_all('table'):
        headers = [cell.get_text(' ',strip=True) for cell in table.select('thead th')]
        container = table.find_parent('div',class_='_chltw')
        section_node = container.select_one('div._hd') if container else None
        section = section_node.get_text(' ',strip=True) if section_node else None
        heading = container.find('h2') if container else None
        for tr in table.select('tbody tr'):
            cells = tr.find_all('td',recursive=False)
            if not cells: continue
            if len(cells)!=5 or len(headers)!=5:
                raise ValueError('Expected exactly five headers and TD cells in every channel row.')
            values = [cell.get_text(' ',strip=True) for cell in cells]
            name, number, quality, price, _ = values
            if not name or not number or quality not in {'HD','SD'}:
                raise ValueError('Missing channel name/number or unsupported HD/SD value.')
            if price=='Free': amount=Decimal('0')
            elif re.fullmatch(r'₹\d+(?:\.\d{1,2})?',price): amount=Decimal(price[1:])
            else: raise ValueError('Unrecognized source price; refusing to invent a numeric amount.')
            generated = 'CH-'+uuid.uuid5(uuid.NAMESPACE_URL,'tech-five:html-channel:'+name+'|'+number+'|'+quality).hex.upper()
            record = dict(channel_id=generated,channel_name=name,channel_number=number,
                hd_sd=quality,price=price,price_inr=Decimal128(amount),monthly_price_inr=None,
                billing_period=None,language=section if section in LANGUAGES else None,
                genre=section if section in GENRES else None,is_hd=quality=='HD',is_active=True,
                availability_status='SOURCE_LISTED_NOT_VERIFIED',pricing_status='SOURCE_LISTED_UNVERIFIED',
                data_type='IMPORTED_HTML',source_section=section,
                source_heading=heading.get_text(' ',strip=True) if heading else None,
                source_file=Path(path).name,source_sha256=digest,
                source_headers=headers,source_values=dict(zip(headers,values)),
                source_cells=[dict(text=v,html=cell.decode_contents(),attributes=dict(cell.attrs))
                              for cell,v in zip(cells,values)],source_row_attributes=dict(tr.attrs))
            logo = cells[4].find('img')
            record['logo_attributes'] = dict(logo.attrs) if logo else None
            record['logo_url'] = (logo.get('data-original') or logo.get('src')) if logo else None
            key = identity(record)
            if key in seen: raise ValueError('Duplicate channel name/format requires manual review.')
            seen.add(key)
            records.append(record)
    if not records: raise ValueError('No channel TD rows found; database will not be changed.')
    return records


def plan_import(records, existing):
    by_name = defaultdict(list)
    ids = set()
    for row in existing:
        if row.get('channel_id') in ids or not row.get('channel_id'):
            raise ValueError('Existing channel IDs are missing or duplicated.')
        ids.add(row['channel_id'])
        by_name[identity(row)].append(row)
    planned, matched = [], set()
    for row in records:
        row = dict(row)
        matches = by_name[identity(row)]
        if len(matches)>1: raise ValueError('Ambiguous existing channel name/format; no changes made.')
        if matches:
            old = matches[0]
            row['channel_id'] = old['channel_id']
            if '_id' in old: row['_id'] = old['_id']
            matched.add(row['channel_id'])
        elif row['channel_id'] in ids:
            raise ValueError('Generated ID collides with an unrelated existing channel.')
        planned.append(row)
    return planned,dict(source_rows=len(records),updated_existing=len(matched),
        added=len(records)-len(matched),retained_existing=len(existing)-len(matched),
        expected_total=len(existing)+len(records)-len(matched),linked_ids_changed=0)


def fingerprint(rows):
    return json_util.dumps(sorted(rows,key=lambda row:row['channel_id']),sort_keys=True)


def apply_import(database, planned, existing):
    from pymongo import ReplaceOne
    from telecom_support.service_schema import mongo_transaction
    database.channels.create_index('channel_id',unique=True)
    def persist(session):
        current=list(database.channels.find({},session=session))
        if fingerprint(current)!=fingerprint(existing):
            raise ValueError('Channel catalog changed since planning; rerun the import.')
        database.channels.bulk_write([ReplaceOne({'channel_id':row['channel_id']},row,upsert=True)
                                      for row in planned],ordered=True,session=session)
        imported=list(database.channels.find({'channel_id':{'$in':[r['channel_id'] for r in planned]}},
                                             {'_id':0},session=session))
        expected=[{k:v for k,v in row.items() if k!='_id'} for row in planned]
        if fingerprint(imported)!=fingerprint(expected):
            raise ValueError('Imported data did not match the source; transaction will be rolled back.')
        ids=set(database.channels.distinct('channel_id',session=session))
        for name in ('bouquet_channels','subscription_channels'):
            references=set(database[name].distinct('channel_id',session=session))
            if not references.issubset(ids):
                raise ValueError('Broken channel references detected; transaction will be rolled back.')
    mongo_transaction(database,persist)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html',type=Path)
    parser.add_argument('--apply',action='store_true',help='Apply the validated merge in a MongoDB transaction.')
    args=parser.parse_args()
    records=parse_html(args.html)
    from telecom_support.database import MongoSettings,connect
    client,database=connect(MongoSettings.load())
    try:
        existing=list(database.channels.find({}))
        planned,summary=plan_import(records,existing)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        folder=ROOT/'output'/'imports'/stamp
        folder.mkdir(parents=True)
        (folder/'channels-before.json').write_text(json_util.dumps(existing,indent=2),encoding='utf-8')
        (folder/'channels-planned.json').write_text(json_util.dumps(planned,indent=2,ensure_ascii=False),encoding='utf-8')
        summary.update(applied=False,backup=str(folder/'channels-before.json'))
        if args.apply:
            apply_import(database,planned,existing)
            summary['applied']=True
        (folder/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2))
    finally: client.close()


if __name__=='__main__': main()
