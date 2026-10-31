"""Assign one unquoted synthetic Malayalam/English/Kids/Discovery demo plan."""
import argparse
from datetime import datetime,timedelta
from pathlib import Path
import sys
import uuid

from bson import json_util

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telecom_support.account_tools import INDIA
from telecom_support.settings import ROOT

PACKAGE_ID='BQ_DEMO_ML_EN_KIDS_DISCOVERY'
PACKAGE_NAME='Tech Five Malayalam English Family'
BASIS='USER_REQUESTED_SYNTHETIC_DEMO'
SECTIONS={'Malayalam','English','Kids','Infotainment'}


def selection(channels):
    imported=[r for r in channels if r.get('data_type')=='IMPORTED_HTML']
    if {r['source_section'] for r in imported}&SECTIONS != SECTIONS:
        raise ValueError('A required source section is missing.')
    rows=[r for r in imported if r['source_section'] in SECTIONS or 'discovery' in r['channel_name'].casefold()]
    if not any(r['channel_name'].casefold()=='discovery channel' for r in rows):
        raise ValueError('Discovery Channel is missing from the source.')
    if len({r['source_sha256'] for r in rows})!=1 or len({r['channel_id'] for r in rows})!=len(rows):
        raise ValueError('Source revisions or channel identities are inconsistent.')
    return sorted(rows,key=lambda r:r['channel_id'])


def assign(database,phone):
    from telecom_support.caller import phone_number
    from telecom_support.service_schema import mongo_transaction
    number=phone_number(phone)
    query={'$or':[{'phone_normalized':number},{'phone':{'$in':[number,number[3:],number[1:]]}}]}
    customers=list(database.customers.find(query,{'_id':0,'customer_id':1,'data_type':1,'account_status':1}).limit(2))
    if len(customers)!=1 or customers[0].get('data_type')!='SYNTHETIC' or customers[0].get('account_status','').casefold()!='active':
        raise ValueError('One active, explicitly synthetic customer is required.')
    customer=customers[0]
    rows=selection(list(database.channels.find({})))
    revision=rows[0]['source_sha256']
    sid='SUB_DEMO_'+uuid.uuid5(uuid.NAMESPACE_URL,customer['customer_id']+':'+PACKAGE_ID).hex[:20].upper()
    start=datetime.now(INDIA).date()
    subscription=dict(subscription_id=sid,customer_id=customer['customer_id'],bouquet_id=PACKAGE_ID,
        start_date=start.isoformat(),expiry_date=(start+timedelta(days=29)).isoformat(),
        subscription_status='Active',auto_renew=False,billing_cycle='Demo30Days',
        data_type='SYNTHETIC',assignment_basis=BASIS,payment_executed=False,
        channel_catalog_revision=revision,channel_membership_basis=BASIS)
    package=dict(bouquet_id=PACKAGE_ID,bouquet_name=PACKAGE_NAME,primary_language='Multi',
        monthly_price_inr=None,currency='INR',is_active=True,pricing_status='UNQUOTED_SYNTHETIC_DEMO',
        data_type='SYNTHETIC',membership_basis=BASIS,channel_catalog_revision=revision,
        membership_rule='Malayalam, English, Kids and Infotainment sections plus all source-listed Discovery variants')
    memberships=[dict(bouquet_id=PACKAGE_ID,channel_id=r['channel_id'],membership_basis=BASIS,source_sha256=revision) for r in rows]
    before=dict(customer_binding=customer,subscriptions=list(database.subscriptions.find({'customer_id':customer['customer_id']})),
        package=list(database.bouquets.find({'bouquet_id':PACKAGE_ID})),
        memberships=list(database.bouquet_channels.find({'bouquet_id':PACKAGE_ID})))
    folder=ROOT/'output'/'demo-assignments'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder/'before.json').write_text(json_util.dumps(before,indent=2),encoding='utf-8')
    database.bouquets.create_index('bouquet_id',unique=True)
    database.subscriptions.create_index('subscription_id',unique=True)
    def persist(session):
        current=list(database.customers.find(query,{'_id':0,'customer_id':1,'data_type':1,'account_status':1},session=session).limit(2))
        if current!=customers: raise ValueError('Customer binding changed; assignment cancelled.')
        current_rows=selection(list(database.channels.find({},session=session)))
        if json_util.dumps(current_rows,sort_keys=True)!=json_util.dumps(rows,sort_keys=True):
            raise ValueError('Channel source changed; assignment cancelled.')
        existing=list(database.subscriptions.find({'customer_id':customer['customer_id']},session=session))
        if existing:
            if len(existing)==1 and existing[0].get('subscription_id')==sid and existing[0].get('assignment_basis')==BASIS:
                return existing[0]  # Do not extend or reactivate on a repeat run.
            raise ValueError('Customer already has a subscription; nothing will be replaced.')
        if database.bouquets.find_one({'bouquet_id':PACKAGE_ID},session=session):
            raise ValueError('Demo package already exists without this subscription; review before changing it.')
        database.bouquets.insert_one(package,session=session)
        database.bouquet_channels.insert_many(memberships,session=session)
        database.subscriptions.insert_one(subscription,session=session)
        return database.subscriptions.find_one({'subscription_id':sid},session=session)
    result=mongo_transaction(database,persist)
    return dict(package_name=PACKAGE_NAME,channel_count=len(rows),start_date=result['start_date'],
        expiry_date=result['expiry_date'],pricing_status='UNQUOTED_SYNTHETIC_DEMO',
        payment_executed=False,backup=str(folder/'before.json'))


def main():
    import json
    import os
    from telecom_support.database import MongoSettings,connect
    settings=MongoSettings.load()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phone',default=os.getenv('CALLER_PHONE'))
    args=parser.parse_args()
    if not args.phone: parser.error('Set CALLER_PHONE or pass --phone.')
    client,db=connect(settings)
    try: print(json.dumps(assign(db,args.phone),indent=2))
    finally:client.close()


if __name__=='__main__': main()
