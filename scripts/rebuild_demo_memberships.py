"""Rebuild synthetic package memberships from explicit imported HTML sections."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from bson import json_util

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telecom_support.account_tools import truth
from telecom_support.settings import ROOT

BASIS='SYNTHETIC_SECTION_BASED_NOT_OFFICIAL'
REGIONAL={'Tamil Starter':('Tamil',True),'Tamil Family':('Tamil',False),
    'Malayalam Starter':('Malayalam',True),'Malayalam Family':('Malayalam',False),
    'Telugu Starter':('Telugu',True),'Telugu Family':('Telugu',False),
    'Kannada Family':('Kannada',False),'Hindi Entertainment':('Hindi',False)}
SPECIAL={'Sports Plus','South Combo','Kids and Learning','All-Rounder'}
# Explicit synthetic catalog migration aliases, not speech/intent keyword rules.
ALIASES={'Flowers TV':'Flowers','Sony Entertainment Television':'SET'}
COLLECTIONS=('channels','bouquets','bouquet_channels','subscriptions','subscription_channels')


def snapshot(database,session=None):
    options={'session':session} if session is not None else {}
    result={name:list(database[name].find({},**options)) for name in COLLECTIONS}
    result['synthetic_customer_ids']=database.customers.distinct('customer_id',{'data_type':'SYNTHETIC'},**options)
    return result


def canonical(value):
    return json_util.dumps(value,sort_keys=True)


def fingerprint(state):
    return canonical({k:sorted(v,key=canonical) for k,v in state.items()})


def build_plan(state):
    channels={row['channel_id']:row for row in state['channels']}
    if len(channels)!=len(state['channels']): raise ValueError('Duplicate channel identifiers.')
    source=[row for row in channels.values() if row.get('data_type')=='IMPORTED_HTML']
    hashes={row.get('source_sha256') for row in source}
    if not source or len(hashes)!=1 or None in hashes:
        raise ValueError('A single complete imported HTML catalog is required.')
    revision=next(iter(hashes))
    package_names={row['bouquet_name'] for row in state['bouquets']}
    if package_names!=set(REGIONAL)|SPECIAL or len(package_names)!=len(state['bouquets']):
        raise ValueError('This command supports only the twelve reviewed demo packages.')
    package_ids={row['bouquet_id'] for row in state['bouquets']}
    if len(package_ids)!=len(state['bouquets']): raise ValueError('Duplicate package identifiers.')
    subscription_ids={row['subscription_id'] for row in state['subscriptions']}
    if len(subscription_ids)!=len(state['subscriptions']): raise ValueError('Duplicate subscription identifiers.')
    if not {row.get('customer_id') for row in state['subscriptions']}<=set(state.get('synthetic_customer_ids',[])):
        raise ValueError('Only subscriptions belonging to explicitly synthetic customers can be rebuilt.')
    if any(row.get('bouquet_id') is not None and row['bouquet_id'] not in package_ids for row in state['subscriptions']):
        raise ValueError('A subscription references an unknown package.')
    memberships=[]
    counts={}
    for package in state['bouquets']:
        name=package['bouquet_name']
        if name in REGIONAL:
            section,starter=REGIONAL[name]
            chosen=[c for c in source if c['source_section']==section and
                (not starter or (c['price']=='Free' and not truth(c['is_hd'])))]
            rule=f'{section} section' + ('; Free SD channels only' if starter else '; all listed formats')
        elif name=='Sports Plus':
            chosen=[c for c in source if c['source_section']=='Sports']
            rule='Sports section; all listed formats'
        elif name=='South Combo':
            chosen=[c for c in source if c['source_section'] in {'Tamil','Telugu','Kannada','Malayalam'}]
            rule='Tamil, Telugu, Kannada and Malayalam sections; all listed formats'
        elif name=='Kids and Learning':
            chosen=[c for c in source if c['source_section'] in {'Kids','Infotainment'}]
            rule='Kids and Infotainment sections; all listed formats'
        else:
            chosen=source
            rule='All imported sections, including Hd Channels; all listed formats'
        if not chosen: raise ValueError('A reviewed demo package would have no channels.')
        for c in sorted(chosen,key=lambda c:c['channel_id']):
            memberships.append(dict(bouquet_id=package['bouquet_id'],channel_id=c['channel_id'],
                membership_basis=BASIS,source_sha256=revision))
        counts[name]=dict(channel_count=len(chosen),rule=rule)
    by_name={(c['channel_name'].casefold(),truth(c.get('is_hd'))):c for c in source}
    legacy_updates=[]
    for channel in channels.values():
        if channel.get('data_type')=='IMPORTED_HTML': continue
        row=dict(channel,catalog_reference_status='LEGACY_NOT_IN_IMPORTED_SOURCE')
        alias=ALIASES.get(channel['channel_name'])
        target=by_name.get((alias.casefold(),truth(channel.get('is_hd')))) if alias else None
        if target: row['canonical_channel_id']=target['channel_id']
        legacy_updates.append(row)
    changed_addons=[]
    addon_pairs=set()
    for old in state['subscription_channels']:
        if old['subscription_id'] not in subscription_ids or old['channel_id'] not in channels:
            raise ValueError('An add-on has an unknown subscription or channel.')
        channel=channels[old['channel_id']]
        row=dict(old)
        if channel.get('data_type')!='IMPORTED_HTML':
            alias=ALIASES.get(channel['channel_name'])
            target=by_name.get((alias.casefold(),truth(channel.get('is_hd')))) if alias else None
            if target is None: raise ValueError('An unmapped legacy add-on requires explicit review.')
            row.update(channel_id=target['channel_id'],catalog_migration={
                'previous_channel_id':old['channel_id'],'previous_channel_name':channel['channel_name'],
                'replacement_channel_name':target['channel_name'],'basis':BASIS,'source_sha256':revision})
            changed_addons.append(row)
        pair=(row['subscription_id'],row['channel_id'])
        if pair in addon_pairs: raise ValueError('Migration would duplicate an individual add-on.')
        addon_pairs.add(pair)
    return dict(memberships=memberships,changed_addons=changed_addons,legacy_updates=legacy_updates,revision=revision,
        summary=dict(package_count=len(counts),package_rules=counts,package_channel_links=len(memberships),
            individual_links=len(addon_pairs),individual_links_remapped=len(changed_addons),
            subscriptions=len(subscription_ids),source_channels=len(source),membership_basis=BASIS,
            subscription_dates_status_prices_changed=False))


def apply_plan(database,state,plan):
    from pymongo import InsertOne,ReplaceOne
    from telecom_support.service_schema import mongo_transaction
    # These collections are normalized joins; do not duplicate channel metadata
    # inside subscriptions or turn package channels into paid individual add-ons.
    database.bouquet_channels.create_index([('bouquet_id',1),('channel_id',1)],unique=True)
    database.subscription_channels.create_index([('subscription_id',1),('channel_id',1)],unique=True)
    def persist(session):
        if fingerprint(snapshot(database,session))!=fingerprint(state):
            raise ValueError('Catalog or subscriptions changed since planning; rerun.')
        database.bouquet_channels.delete_many({},session=session)
        database.bouquet_channels.bulk_write([InsertOne(row) for row in plan['memberships']],session=session)
        if plan['legacy_updates']:
            database.channels.bulk_write([ReplaceOne({'channel_id':r['channel_id']},r)
                for r in plan['legacy_updates']],session=session)
        for row in state['bouquets']:
            database.bouquets.update_one({'_id':row['_id']},{'$set':{
                'membership_basis':BASIS,'channel_catalog_revision':plan['revision'],
                'membership_rule':plan['summary']['package_rules'][row['bouquet_name']]['rule']}},session=session)
        if plan['changed_addons']:
            database.subscription_channels.bulk_write([ReplaceOne({'_id':r['_id']},r)
                for r in plan['changed_addons']],session=session)
        database.subscriptions.update_many({}, {'$set':{'channel_catalog_revision':plan['revision'],
            'channel_membership_basis':BASIS}},session=session)
        after=snapshot(database,session)
        verify_result(state,after,plan)
    mongo_transaction(database,persist)


def verify_result(before,after,plan):
    expected_channels={r['channel_id']:r for r in before['channels']}
    expected_channels.update({r['channel_id']:r for r in plan['legacy_updates']})
    if expected_channels!={r['channel_id']:r for r in after['channels']}:
        raise ValueError('Channel source metadata changed unexpectedly.')
    expected_pairs={(r['bouquet_id'],r['channel_id']) for r in plan['memberships']}
    actual_pairs={(r['bouquet_id'],r['channel_id']) for r in after['bouquet_channels']}
    if expected_pairs!=actual_pairs or len(actual_pairs)!=len(after['bouquet_channels']):
        raise ValueError('Package membership verification failed.')
    for collection,key,allowed in [('subscriptions','subscription_id',{'channel_catalog_revision','channel_membership_basis'}),
                                  ('bouquets','bouquet_id',{'membership_basis','channel_catalog_revision','membership_rule'})]:
        strip=lambda row:{k:v for k,v in row.items() if k not in allowed}
        if {r[key]:strip(r) for r in before[collection]}!={r[key]:strip(r) for r in after[collection]}:
            raise ValueError('Subscription terms or package prices changed unexpectedly.')
    expected_addons={r['_id']:r for r in before['subscription_channels']}
    expected_addons.update({r['_id']:r for r in plan['changed_addons']})
    if expected_addons!={r['_id']:r for r in after['subscription_channels']}:
        raise ValueError('Individual add-on verification failed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from telecom_support.database import MongoSettings,connect
    client,db=connect(MongoSettings.load())
    try:
        state=snapshot(db)
        plan=build_plan(state)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        folder=ROOT/'output'/'membership-rebuilds'/stamp
        folder.mkdir(parents=True)
        (folder/'before.json').write_text(json_util.dumps(state,indent=2),encoding='utf-8')
        (folder/'plan.json').write_text(json_util.dumps(plan,indent=2),encoding='utf-8')
        if args.apply: apply_plan(db,state,plan)
        summary=dict(plan['summary'],applied=args.apply,backup=str(folder/'before.json'))
        (folder/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2))
    finally:client.close()


if __name__=='__main__':main()
