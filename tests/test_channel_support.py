from datetime import datetime

import mongomock
import pytest
from pydantic import ValidationError

from telecom_support.account_tools import AccountTools, CustomerIdentity, INDIA, ToolError
from telecom_support.channel_support import execute


@pytest.fixture
def account():
    db = mongomock.MongoClient().telecaller
    db.customers.insert_many([
        dict(customer_id='C1',data_type='SYNTHETIC',account_status='Active',phone='private-number',email='private-email'),
        dict(customer_id='C2',data_type='SYNTHETIC',account_status='Active')])
    db.channels.insert_many([dict(channel_id=str(i),channel_name=name,is_active=True,
        monthly_price_inr=price) for i,name,price in [(1,'Movie SD',10),(2,'Movie HD',20),(3,'News',0),(4,'Sports',30)]])
    db.bouquets.insert_many([dict(bouquet_id=bid,bouquet_name=name,is_active=True,monthly_price_inr=price)
        for bid,name,price in [('B1','Movies',100),('B2','News and Sports',150),('B3','HD and Sports',200)]])
    db.bouquet_channels.insert_many([dict(bouquet_id=b,channel_id=c)
        for b,c in [('B1','1'),('B2','3'),('B2','4'),('B3','2'),('B3','4')]])
    db.subscriptions.insert_one(dict(subscription_id='S1',customer_id='C1',bouquet_id='B1',
        start_date='2026-10-01',expiry_date='2026-10-31',subscription_status='Active'))
    return AccountTools(db,CustomerIdentity('C1',verified=True,synthetic_demo=True),
        now=lambda:datetime(2026,10,31,12,tzinfo=INDIA))


def test_all_valid_plans_and_addons_are_unioned_deduplicated_and_private(account):
    db = account.db
    db.subscriptions.insert_many([
        dict(subscription_id='S2',customer_id='C1',bouquet_id='B2',start_date='2026-10-01',expiry_date='2026-10-31',subscription_status='Active'),
        dict(subscription_id='OTHER',customer_id='C2',bouquet_id='B3',start_date='2026-10-01',expiry_date='2026-10-31',subscription_status='Active')])
    db.subscription_channels.insert_many([
        dict(subscription_id='S1',channel_id='3',activated_on='2026-10-01',status='Active'),
        dict(subscription_id='S1',channel_id='2',activated_on='2026-10-01',expiry_date='2026-10-30',status='Active')])
    result = execute(account,{'channel':'Sports'})
    assert result['entitled'] and result['state']=='already_entitled'
    assert {r['channel_id'] for r in result['eligible_channels']}=={'1','3','4'}
    assert len(result['active_subscriptions'])==2
    assert result['options']==[]
    for private in ['private-number','private-email','customer_id','subscription_id','C2']:
        assert private not in str(result)
    assert not result['subscription_changed'] and not result['payment_executed']


def test_individual_only_subscription_and_future_addons(account):
    account.db.subscriptions.update_one({}, {'$set':{'bouquet_id':None}})
    account.db.subscription_channels.insert_many([
        dict(subscription_id='S1',channel_id='4',activated_on='2026-10-01',status='Active'),
        dict(subscription_id='S1',channel_id='2',activated_on='2026-11-01',status='Active')])
    result = execute(account,{'channel':'Sports'})
    assert result['entitled'] and result['eligible_channel_count']==1
    assert not execute(account,{'channel':'Movie HD'})['entitled']


def test_addon_validity_inherits_parent_and_independent_expiry_is_respected(account):
    account.db.subscription_channels.insert_one(dict(subscription_id='S1',channel_id='4',activated_on='2026-10-01',status='Active'))
    assert execute(account,{'channel':'Sports'})['entitled']
    account.db.subscriptions.update_one({}, {'$set':{'expiry_date':'2026-10-30'}})
    result = execute(account,{'channel':'Sports'})
    assert not result['entitled'] and result['state']=='subscription_not_current'


def test_catalog_options_sorted_and_replacement_losses_explained(account):
    result = execute(account,{'channel':'Sports'})
    assert result['state']=='not_in_active_subscription'
    assert result['options'][0]['kind']=='individual_channel'
    assert result['options'][0]['monthly_catalog_price_inr']=='30.00'
    packages = result['options'][1:]
    assert [p['monthly_catalog_price_inr'] for p in packages]==['150.00','200.00']
    assert packages[0]['channels_lost_if_replacing_all_current_plans']==['Movie SD']
    assert set(packages[1]['additional_channels'])=={'Movie HD','Sports'}
    assert account.db.payments.count_documents({})==0


@pytest.mark.parametrize('state,change,collection',[
    ('account_inactive',{'account_status':'Suspended'},'customers'),
    ('channel_unavailable',{'is_active':False},'channels')])
def test_no_sales_for_suspended_account_or_inactive_channel(account,state,change,collection):
    query = {'customer_id':'C1'} if collection=='customers' else {'channel_id':'4'}
    account.db[collection].update_one(query,{'$set':change})
    result=execute(account,{'channel':'Sports'})
    assert result['state']==state and result['options']==[]


def test_ambiguous_channel_asks_instead_of_guessing(account):
    result=execute(account,{'channel':'Movie'})
    assert result['state']=='channel_ambiguous' and len(result['candidates'])==2
    assert execute(account,{'channel':'Unknown'})['state']=='channel_not_found'


def test_authentication_scope_and_argument_injection_fail_closed(account):
    with pytest.raises(ValidationError): execute(account,{'channel':'Sports','customer_id':'C2'})
    with pytest.raises(ValidationError): execute(account,{'channel':{'$ne':None}})
    account.identity=None
    with pytest.raises(ToolError,match='Authenticate'): execute(account,{'channel':'Sports'})


def test_broken_mapping_and_invalid_price_do_not_produce_recommendations(account):
    account.db.bouquet_channels.insert_one(dict(bouquet_id='B1',channel_id='MISSING'))
    with pytest.raises(ToolError,match='mapping'): execute(account,{'channel':'Sports'})
    account.db.bouquet_channels.delete_one({'channel_id':'MISSING'})
    account.db.channels.update_one({'channel_id':'4'},{'$set':{'monthly_price_inr':-1}})
    with pytest.raises(ToolError,match='price'): execute(account,{'channel':'Sports'})


def test_future_plan_and_misleading_active_status_do_not_grant_access(account):
    account.db.subscriptions.update_one({}, {'$set':{'start_date':'2026-11-01','expiry_date':'2026-11-30'}})
    result = execute(account,{'channel':'Movie SD'})
    assert result['state']=='subscription_not_current' and not result['entitled']


def test_deepgram_service_dispatch_uses_bound_account_and_sharing_gate(account):
    from types import SimpleNamespace
    from telecom_support.customer_services import CustomerServices
    caller = SimpleNamespace(customer=account._customer())
    service = CustomerServices(account,caller,share_details=True)
    result = service.execute('diagnose_channel_access',{'channel':'Sports'})
    assert result['state']=='not_in_active_subscription'
    assert 'private-number' not in service.for_agent(result)
    assert 'monthly_catalog_price_inr' in service.for_agent(result)
    service.share_details=False
    assert 'monthly_catalog_price_inr' not in service.for_agent(result)


def test_expired_addon_and_no_subscription_are_distinct(account):
    account.db.subscription_channels.insert_one(dict(subscription_id='S1',channel_id='4',
        activated_on='2026-10-01',expiry_date='2026-10-30',status='Active'))
    assert execute(account,{'channel':'Sports'})['state']=='individual_addon_not_current'
    account.db.subscriptions.delete_many({})
    assert execute(account,{'channel':'Sports'})['state']=='no_current_subscription'


def test_imported_price_without_period_is_not_quoted_as_monthly(account):
    from bson.decimal128 import Decimal128
    account.db.channels.update_one({'channel_id':'4'},{'$set':{
        'price':'₹10.62','price_inr':Decimal128('10.62'),'monthly_price_inr':None,'billing_period':None}})
    option=execute(account,{'channel':'Sports'})['options'][0]
    assert option['catalog_price_inr']=='10.62' and option['source_price_text']=='₹10.62'
    assert option['monthly_catalog_price_inr'] is None and option['billing_period'] is None
    # Catalog joins must also tolerate the source's unknown billing period.
    channels=account._channels('B2')
    assert next(r for r in channels if r['channel_id']=='4')['monthly_price_inr'] is None
