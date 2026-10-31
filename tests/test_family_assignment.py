from copy import deepcopy

import mongomock
import pytest

from scripts import assign_family_demo as module
from telecom_support.account_tools import AccountTools,CustomerIdentity
from telecom_support.subscription_summary import execute


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr('telecom_support.service_schema.mongo_transaction',lambda db,callback:callback(None))
    db=mongomock.MongoClient().telecaller
    db.customers.insert_one(dict(customer_id='C1',phone_normalized='+910000000001',data_type='SYNTHETIC',account_status='Active'))
    rows=[('Malayalam','Flowers'),('English','HBO'),('Kids','Discovery Kids'),
          ('Infotainment','Discovery Channel'),('Hd Channels','Discovery HD World')]
    db.channels.insert_many([dict(channel_id=name,channel_name=name,data_type='IMPORTED_HTML',source_sha256='fixture',
        source_section=section,language=section if section in ['Malayalam','English'] else None,
        genre=section if section in ['Kids','Infotainment'] else None,
        is_hd=section=='Hd Channels',is_active=True,price='Free',monthly_price_inr=None,price_inr=0)
        for section,name in rows])
    return db


def test_assignment_creates_only_synthetic_plan_is_idempotent_and_visible_to_summary(db):
    source=list(db.channels.find({}))
    result=module.assign(db,'0000000001')
    assert result['channel_count']==5 and result['payment_executed'] is False
    account=AccountTools(db,CustomerIdentity('C1',verified=True,synthetic_demo=True))
    summary=execute(account,{})
    assert summary['state']=='active' and summary['total_eligible_channels']==5
    assert summary['active_plans'][0]['monthly_catalog_price_inr'] is None
    before=deepcopy(list(db.subscriptions.find({})))
    module.assign(db,'0000000001')
    assert list(db.subscriptions.find({}))==before
    assert db.bouquets.count_documents({})==1 and db.bouquet_channels.count_documents({})==5
    assert list(db.channels.find({}))==source and db.payments.count_documents({})==0
    assert db.subscriptions.find_one({})['auto_renew'] is False
    assert account.get_active_plan()['has_active_plan']
    assert account.recommend_plan('Malayalam',max_price_inr=500)['recommendations']==[]


def test_existing_subscription_real_customer_or_missing_discovery_is_not_overwritten(db):
    db.subscriptions.insert_one(dict(subscription_id='EXISTING',customer_id='C1'))
    with pytest.raises(ValueError,match='already has'): module.assign(db,'0000000001')
    assert db.bouquets.count_documents({})==0
    db.subscriptions.delete_many({})
    db.customers.update_one({}, {'$set':{'data_type':'REAL'}})
    with pytest.raises(ValueError,match='synthetic'): module.assign(db,'0000000001')
    db.customers.update_one({}, {'$set':{'data_type':'SYNTHETIC'}})
    db.channels.delete_one({'channel_name':'Discovery Channel'})
    with pytest.raises(ValueError,match='section|Discovery'): module.assign(db,'0000000001')
