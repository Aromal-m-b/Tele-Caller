import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import mongomock
import pytest
from pydantic import ValidationError

from telecom_support.account_tools import AccountTools, CustomerIdentity, ToolError
from telecom_support.caller import CallerSession
from telecom_support.customer_services import CustomerServices, submission_question, tool_definitions
from telecom_support.customer_services import affirmative
from telecom_support.service_schema import ensure_service_schema


def mock_transaction(db, callback):
    """Test-only rollback emulator; production always uses MongoDB transactions."""
    names = ['customers','service_requests','orders','deliveries']
    before = {name:list(db[name].find()) for name in names}
    try:
        return callback(None)
    except Exception:
        for name in names:
            db[name].delete_many({})
            if before[name]: db[name].insert_many(before[name])
        raise


@pytest.fixture
def services():
    db = mongomock.MongoClient().telecaller
    db.customers.insert_many([
        dict(customer_id='C1',full_name='Fixture Owner',phone_normalized='+910000000001',phone='+910000000001',account_status='Active',data_type='SYNTHETIC',smartcard_id='PRIVATE-CARD'),
        dict(customer_id='C2',full_name='Other Owner',phone_normalized='+910000000002',phone='+910000000002',account_status='Active',data_type='SYNTHETIC')])
    # mongomock cannot enforce JSON-schema validators; create before index setup.
    for name in ['service_requests','orders','deliveries']: db.create_collection(name)
    ensure_service_schema(db)
    account = AccountTools(db)
    caller = CallerSession(account,'0000000001')
    service = CustomerServices(account,caller,call_id='fixture-call',share_details=True,transact=mock_transaction,clock=lambda:100)
    return service


def arm_and_confirm(service,text='Yes, please. Yes.'):
    service.arm(service.pending['draft_id'],90)
    return service.resolve_reply(text,95)


def order_args(**changes):
    return dict(items=[dict(product_code='REMOTE',quantity=1)],delivery_address='Fixture delivery address',**changes)


def test_draft_is_not_written_and_early_yes_cannot_submit(services):
    result = services.execute('draft_complaint',dict(category='no_signal',description='The television has no signal.'))
    assert result['requires_confirmation']
    assert services.db.service_requests.count_documents({}) == 0
    early = services.resolve_reply('yes please',95)
    assert not early['ok'] and services.db.service_requests.count_documents({}) == 0
    result = arm_and_confirm(services)
    assert result['ok'] and services.db.service_requests.count_documents({}) == 1
    assert services.resolve_reply('yes',101) is None


def test_order_and_delivery_have_owned_links_and_no_invented_fulfilment(services):
    services.execute('draft_equipment_order',order_args())
    result = arm_and_confirm(services)
    order, delivery = result['order'], result['delivery']
    assert order['delivery_id'] == delivery['delivery_id']
    assert delivery['order_id'] == order['order_id']
    assert delivery['item_ids'] == [order['items'][0]['item_id']]
    assert order['status'] == 'AwaitingQuote' and order['total_price_inr'] is None
    assert delivery['status'] == 'NotDispatched'
    assert delivery['estimated_arrival_at'] is None and delivery['delivery_charge_inr'] is None
    assert delivery['current_holder'] is None and delivery['current_location'] is None
    raw_order = services.db.orders.find_one()
    assert raw_order['customer_id'] == 'C1'
    tracked = services.execute('track_delivery',dict(order_id=order['order_id']))
    assert tracked['delivery']['delivery_id'] == delivery['delivery_id']


def test_order_delivery_transaction_rolls_back_and_retry_reuses_ids(services,monkeypatch):
    services.execute('draft_equipment_order',order_args())
    before = deepcopy(services.pending)
    update = services.db.deliveries.update_one
    monkeypatch.setattr(services.db.deliveries,'update_one',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('write failed')))
    with pytest.raises(RuntimeError): arm_and_confirm(services)
    assert services.db.orders.count_documents({}) == 0 and services.db.deliveries.count_documents({}) == 0
    assert services.pending['order_id'] == before['order_id']
    monkeypatch.setattr(services.db.deliveries,'update_one',update)
    result = arm_and_confirm(services)
    assert result['order']['order_id'] == before['order_id']
    assert services.db.orders.count_documents({}) == 1


def test_uncertain_commit_retry_is_idempotent(services):
    services.execute('draft_equipment_order',order_args())
    def lost_ack(db,callback):
        callback(None)
        raise RuntimeError('commit reply lost')
    services.transact = lost_ack
    with pytest.raises(RuntimeError): arm_and_confirm(services)
    services.transact = mock_transaction
    arm_and_confirm(services)
    assert services.db.orders.count_documents({}) == 1
    assert services.db.deliveries.count_documents({}) == 1


def test_no_cross_customer_reads_or_linking(services):
    services.execute('draft_equipment_order',order_args())
    order = arm_and_confirm(services)['order']
    own_caller = services.caller
    services.caller = CallerSession(services.account,'0000000002')
    assert services.execute('get_orders',dict(order_id=order['order_id']))['orders'] == []
    with pytest.raises(ToolError):
        services.execute('draft_complaint',dict(category='other',description='Complaint about that order',order_id=order['order_id']))
    services.caller = own_caller
    with pytest.raises(ToolError): services.execute('get_orders',{})  # identity is still C2, caller is C1


@pytest.mark.parametrize('name,args', [
    ('get_orders',{'customer_id':'C2'}),('track_delivery',{'order_id':{'$ne':None}}),
    ('get_service_requests',{'limit':100}),('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=True)],delivery_address='Fixture address')),
    ('draft_equipment_order',dict(items=[dict(product_code='COAXIAL_CABLE',quantity=1)],delivery_address='Fixture address'))])
def test_strict_arguments_prevent_query_and_identity_injection(services,name,args):
    with pytest.raises(ValidationError): services.execute(name,args)
    assert services.db.orders.count_documents({}) == 0


def test_revised_draft_needs_new_confirmation_and_no_cancels_only_draft(services):
    services.execute('draft_complaint',dict(category='pricing_concern',description='The package price is too high.'))
    services.arm(services.pending['draft_id'],90)
    services.resolve_reply('Please change the description.',95)
    assert not services.resolve_reply('yes',96)['ok']
    services.resolve_reply('cancel',97)
    assert services.pending is None and services.db.service_requests.count_documents({}) == 0


def test_cancellation_records_request_without_modifying_subscription(services):
    services.db.subscriptions.insert_one(dict(customer_id='C1',subscription_status='Active'))
    services.execute('draft_service_request',dict(service_type='cancellation',description='Please cancel the connection after review.'))
    result = arm_and_confirm(services)
    assert result['service_request']['request_type'] == 'cancellation'
    assert services.db.subscriptions.find_one()['subscription_status'] == 'Active'
    assert services.db.payments.count_documents({}) == 0


def test_guest_new_connection_creates_lead_only_after_confirmation(services):
    services.caller = CallerSession(services.account,'0000000009')
    services.account.identity = None
    services.execute('draft_service_request',dict(service_type='new_connection',description='I need a new television connection.',location_address='Fixture new installation address',contact_name='New Fixture'))
    assert services.db.customers.count_documents({}) == 2
    result = arm_and_confirm(services)
    assert services.db.customers.count_documents({}) == 3
    assert services.caller.customer['customer_lifecycle'] == 'Prospect'
    assert services.db.subscriptions.count_documents({}) == 0
    assert result['service_request']['scheduled_at'] is None


def test_protected_customer_record_is_not_replaced_by_new_connection(services):
    services.db.customers.insert_one(dict(customer_id='REAL',phone_normalized='+910000000008',data_type='REAL',account_status='Active'))
    services.caller = CallerSession(services.account,'0000000008')
    services.account.identity = None
    with pytest.raises(ToolError):
        services.execute('draft_service_request',dict(service_type='new_connection',description='A new connection enquiry.',location_address='Fixture address',contact_name='Fixture'))
    assert services.db.customers.count_documents({}) == 3


def test_suspended_synthetic_customer_can_complain_without_reactivation(services):
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'account_status':'Suspended'}})
    services.caller = CallerSession(services.account,'0000000001',allow_suspended=True)
    services.execute('draft_complaint',dict(category='payment_not_reflected',description='Payment was made but the service remains suspended.'))
    assert arm_and_confirm(services)['ok']
    assert services.db.customers.find_one({'customer_id':'C1'})['account_status'] == 'Suspended'


def test_visit_and_order_use_saved_address_without_dictation(services):
    saved = dict(house_number=123,street_address=456789,city='Fixture City',state='Fixture State')
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'service_address':saved}})
    context = services.execute('get_service_context',{})
    assert context['saved_service_address_available'] and context['saved_delivery_address_available']
    draft = services.execute('draft_service_request',dict(service_type='service_booking',description='Please fix the missing TV signal.'))
    assert draft['address_source'] == 'customer_record'
    assert draft['draft']['location_address'] == 'House 123, Street 456789, Fixture City, Fixture State'
    assert arm_and_confirm(services)['service_request']['location_address'] == draft['draft']['location_address']
    order = services.execute('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=1)]))
    assert order['address_source'] == 'customer_record'
    submitted = arm_and_confirm(services)
    assert submitted['order']['delivery_address'] == draft['draft']['location_address']
    assert submitted['delivery']['delivery_address'] == draft['draft']['location_address']


def test_city_state_only_is_not_a_delivery_address(services):
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'city':'Fixture City','state':'Fixture State'}})
    with pytest.raises(ToolError,match='No complete delivery address'):
        services.execute('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=1)]))
    assert services.pending is None
    # A complaint can still be filed without a delivery/visit address.
    assert services.execute('draft_complaint',dict(category='no_signal',description='There is no TV signal.'))['ok']


def test_relocation_requires_new_destination_and_order_override_does_not_edit_profile(services):
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'service_address':'Original fixture home address'}})
    with pytest.raises(ToolError,match='new destination'):
        services.execute('draft_service_request',dict(service_type='relocation',description='Move the connection to my new house.'))
    preview = services.execute('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=1)],delivery_address='Different fixture office address'))
    assert preview['address_source'] == 'caller_supplied'
    arm_and_confirm(services)
    assert services.db.customers.find_one({'customer_id':'C1'})['service_address'] == 'Original fixture home address'


def test_saved_address_change_requires_new_confirmation(services):
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'service_address':'Original fixture address'}})
    services.execute('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=1)]))
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'service_address':'Updated fixture address'}})
    with pytest.raises(ToolError,match='saved address changed'): arm_and_confirm(services)
    assert services.db.orders.count_documents({}) == 0


def test_saved_address_is_hidden_in_draft_when_sharing_is_disabled(services):
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'service_address':'PRIVATE SAVED FIXTURE ADDRESS'}})
    services.share_details = False
    preview = services.execute('draft_equipment_order',dict(items=[dict(product_code='REMOTE',quantity=1)]))
    exported = services.for_agent(preview)
    assert 'PRIVATE SAVED FIXTURE ADDRESS' not in exported
    assert json.loads(exported)['address_on_file']


def test_synthetic_address_seed_is_idempotent_preserves_real_records_and_demo_entry(services):
    from telecom_support.synthetic_addresses import seed_synthetic_addresses
    services.db.customers.insert_one(dict(customer_id='REAL',data_type='REAL',account_status='Active'))
    result = seed_synthetic_addresses(services.db,'0000000001',randbelow=lambda bound:0)
    assert result['addresses_added'] == 2 and result['demo_customer_has_saved_address']
    row = services.db.customers.find_one({'customer_id':'C1'})
    assert row['house_number'] == 100 and row['street_address'] == 100000
    assert row['smartcard_id'] == 'PRIVATE-CARD'
    assert services.db.customers.find_one({'customer_id':'REAL'}).get('service_address') is None
    assert seed_synthetic_addresses(services.db,'0000000001')['addresses_added'] == 0


def test_synthetic_seed_preserves_existing_saved_address(services):
    from telecom_support.synthetic_addresses import seed_synthetic_addresses
    services.db.customers.update_one({'customer_id':'C1'},{'$set':{'home_address':'Existing fixture house address'}})
    seed_synthetic_addresses(services.db,'0000000001',randbelow=lambda bound:0)
    row = services.db.customers.find_one({'customer_id':'C1'})
    assert row['home_address'] == 'Existing fixture house address'
    assert 'service_address' not in row


def test_agent_view_shares_only_approved_service_fields(services):
    services.execute('draft_equipment_order',order_args())
    result = arm_and_confirm(services)
    services.db.orders.update_one({}, {'$set':dict(customer_secret='DO-NOT-EXPORT',full_name='Private Name',phone='Private Phone')})
    serialized = services.for_agent(services.execute('get_orders',{}))
    assert 'Fixture delivery address' in serialized
    assert all(value not in serialized for value in ['DO-NOT-EXPORT','Private Name','Private Phone','PRIVATE-CARD','customer_id'])
    services.share_details = False
    assert 'Fixture delivery address' not in services.for_agent(result)


def test_tracking_uses_recorded_facts_and_filters_nested_private_values(services):
    from bson.decimal128 import Decimal128
    services.execute('draft_equipment_order',order_args())
    result = arm_and_confirm(services)
    eta = datetime(2026,11,1,10,tzinfo=timezone.utc)
    services.db.deliveries.update_one({}, {'$set':dict(status='InTransit',current_location='Fixture depot',
        current_holder='Fixture courier',estimated_arrival_at=eta,delivery_charge_inr=Decimal128('35.50'),
        tracking_events=[dict(at=eta,status='InTransit',location='Fixture depot',holder='Fixture courier',note='Transferred to courier',private_phone='DO-NOT-EXPORT')])})
    tracked = services.execute('track_delivery',{})['delivery']
    assert tracked['current_location'] == 'Fixture depot'
    assert tracked['delivery_charge_inr'] == '35.50'
    assert tracked['estimated_arrival_at'].startswith('2026-11-01T10:00:00')
    assert 'DO-NOT-EXPORT' not in json.dumps(tracked)
    services.db.deliveries.update_one({}, {'$set':{'current_holder':{'phone':'DO-NOT-EXPORT'}}})
    assert services.execute('track_delivery',{})['delivery']['current_holder'] is None


def test_tools_have_no_confirm_or_identity_arguments():
    tools = tool_definitions()
    assert not any('confirm' in tool['name'] for tool in tools)
    assert all(tool['defer_until_eot'] for tool in tools)
    assert all('customer_id' not in tool['parameters']['properties'] and 'phone' not in tool['parameters']['properties'] for tool in tools)


def test_explicit_submission_question_is_required():
    assert submission_question('Shall I submit this complaint for you?')
    assert submission_question('Would you like me to place this order?')
    assert not submission_question('Is there anything else?')
    assert not submission_question('Is that your address?')


@pytest.mark.parametrize('text',['Yes, please submit the complaint.','Go ahead and place the order.','Please register this request.','Yes, please. Yes.'])
def test_submission_affirmative_handles_action_phrases(text):
    assert affirmative(text)


@pytest.mark.parametrize('text',['Yes, but change the address first.','Yes, what is the price?','Do not submit the complaint.'])
def test_qualified_yes_is_not_submission_consent(text):
    assert not affirmative(text)


def test_agent_session_confirms_from_real_reply_after_question_playback(services):
    from telecom_support.deepgram_agent import AgentSession, AgentConfig, agent_settings
    from telecom_support.speech_input import SpeechSettings
    async def run():
        sent = []
        async def send(text): sent.append(json.loads(text))
        now = [100]
        session = AgentSession(SimpleNamespace(customer_services=services),SpeechSettings('fake'),AgentConfig(),
            SimpleNamespace(send=send),SimpleNamespace(),write=lambda _:None,clock=lambda:now[0])
        await session.function(dict(id='f1',name='draft_complaint',arguments=json.dumps(dict(category='no_signal',description='There is no television signal.'))))
        assert services.db.service_requests.count_documents({}) == 0
        await session.event(dict(type='ConversationText',role='assistant',content='Shall I submit this complaint?'))
        session.audio_done()
        await asyncio.gather(*session.business_tasks)
        now[0] = 101
        await session.event(dict(type='UserStartedSpeaking'))
        await session.event(dict(type='ConversationText',role='user',content='Yes, please. Yes.'))
        await asyncio.gather(*session.business_tasks)
        assert services.db.service_requests.count_documents({}) == 1
        notice = [item for item in sent if item['type'] == 'InjectUserMessage'][-1]
        assert 'Submitted' in notice['content'] and 'customer_id' not in notice['content']
        assert 'PRIVATE-CARD' not in str(sent) and '+910000000001' not in str(sent)
        payload = agent_settings(SpeechSettings('fake'),AgentConfig(),service_tools=True)
        assert len(payload['agent']['think']['functions']) == 9
    asyncio.run(run())


def test_model_cannot_submit_by_function_or_confirmed_argument(services):
    from telecom_support.deepgram_agent import AgentSession, AgentConfig
    from telecom_support.speech_input import SpeechSettings
    async def run():
        sent = []
        async def send(text): sent.append(json.loads(text))
        session = AgentSession(SimpleNamespace(customer_services=services),SpeechSettings('fake'),AgentConfig(),
            SimpleNamespace(send=send),SimpleNamespace(),write=lambda _:None)
        await session.function(dict(id='f1',name='confirm_service_request',arguments='{}'))
        await session.function(dict(id='f2',name='draft_complaint',arguments=json.dumps(dict(category='no_signal',description='There is no signal.',confirmed=True))))
        assert services.db.service_requests.count_documents({}) == 0
        assert services.pending is None
    asyncio.run(run())


def test_foreign_link_is_rechecked_at_commit(services):
    services.execute('draft_service_request',dict(service_type='cancellation',description='Please record a cancellation request.'))
    request = arm_and_confirm(services)['service_request']
    services.execute('draft_equipment_order',order_args(service_request_id=request['request_id']))
    services.db.service_requests.update_one({}, {'$set':{'customer_id':'C2'}})
    with pytest.raises(ToolError): arm_and_confirm(services)
    assert services.db.orders.count_documents({}) == 0
