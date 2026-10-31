from copy import deepcopy

import pytest

from scripts.rebuild_demo_memberships import BASIS,REGIONAL,SPECIAL,build_plan,verify_result


@pytest.fixture
def state():
    channels=[]
    for language in ('Tamil','Telugu','Malayalam','Kannada','Hindi'):
        for price in ('Free','₹10'):
            channels.append(dict(channel_id=f'{language}-{price}',channel_name=f'{language} {price}',
                source_section=language,is_hd=False,price=price,data_type='IMPORTED_HTML',source_sha256='fixture'))
    for section,name,hd in [('Sports','Sports HD',True),('Sports','Sports SD',False),('Kids','Kids',False),
                            ('Infotainment','Learning',False),('Hd Channels','HD channel',True),
                            ('Malayalam','Flowers',False),('Hindi','SET',False)]:
        channels.append(dict(channel_id=name,channel_name=name,source_section=section,is_hd=hd,
            price='₹10',data_type='IMPORTED_HTML',source_sha256='fixture'))
    channels += [dict(channel_id='OLD_FLOWERS',channel_name='Flowers TV',is_hd=False),
                 dict(channel_id='OLD_SET',channel_name='Sony Entertainment Television',is_hd=False)]
    packages=[dict(_id=name,bouquet_id=name,bouquet_name=name,monthly_price_inr=100)
              for name in sorted(set(REGIONAL)|SPECIAL)]
    return dict(channels=channels,bouquets=packages,bouquet_channels=[],
        synthetic_customer_ids=['C1'],subscriptions=[dict(_id='subscription',subscription_id='S1',customer_id='C1',
            bouquet_id='Malayalam Family',start_date='2026-01-01',expiry_date='2026-02-01',subscription_status='Expired')],
        subscription_channels=[dict(_id='addon1',subscription_id='S1',channel_id='OLD_FLOWERS',status='Expired',activated_on='2026-01-01'),
                               dict(_id='addon2',subscription_id='S1',channel_id='OLD_SET',status='Active',activated_on='2026-01-01')])


def test_rules_use_source_sections_formats_and_preserve_addon_terms(state):
    before=deepcopy(state)
    plan=build_plan(state)
    get=lambda name:{r['channel_id'] for r in plan['memberships'] if r['bouquet_id']==name}
    assert get('Tamil Starter')=={'Tamil-Free'}
    assert get('Tamil Family')=={'Tamil-Free','Tamil-₹10'}
    assert get('Sports Plus')=={'Sports HD','Sports SD'}
    assert get('Kids and Learning')=={'Kids','Learning'}
    assert 'HD channel' in get('All-Rounder')
    assert get('All-Rounder')=={r['channel_id'] for r in state['channels'] if r.get('data_type')=='IMPORTED_HTML'}
    assert 'OLD_FLOWERS' not in get('Malayalam Family')
    assert plan['changed_addons'][0]['channel_id']=='Flowers'
    assert plan['changed_addons'][0]['status']=='Expired'
    assert plan['changed_addons'][1]['channel_id']=='SET'
    assert state==before
    assert all(r['membership_basis']==BASIS for r in plan['memberships'])


def resulting_state(state,plan):
    after=deepcopy(state)
    after['bouquet_channels']=deepcopy(plan['memberships'])
    after['subscription_channels']=deepcopy(plan['changed_addons'])
    updates={r['channel_id']:r for r in plan['legacy_updates']}
    after['channels']=[deepcopy(updates.get(r['channel_id'],r)) for r in after['channels']]
    for row in after['subscriptions']: row.update(channel_catalog_revision=plan['revision'],channel_membership_basis=BASIS)
    return after


def test_verification_rejects_accidental_price_or_expiry_changes(state):
    plan=build_plan(state)
    after=resulting_state(state,plan)
    verify_result(state,after,plan)
    after['subscriptions'][0]['expiry_date']='2099-01-01'
    with pytest.raises(ValueError,match='terms'): verify_result(state,after,plan)
    after=resulting_state(state,plan)
    after['bouquets'][0]['monthly_price_inr']=1
    with pytest.raises(ValueError,match='prices'): verify_result(state,after,plan)


def test_verification_rejects_missing_memberships_and_changed_addon_status(state):
    plan=build_plan(state)
    after=resulting_state(state,plan)
    after['bouquet_channels'].pop()
    with pytest.raises(ValueError,match='membership'): verify_result(state,after,plan)
    after=resulting_state(state,plan)
    after['subscription_channels'][0]['status']='Active'
    with pytest.raises(ValueError,match='add-on'): verify_result(state,after,plan)


def test_non_synthetic_owner_or_unknown_package_fails_closed(state):
    state['synthetic_customer_ids']=[]
    with pytest.raises(ValueError,match='synthetic'): build_plan(state)
    state['synthetic_customer_ids']=['C1']
    state['bouquets'][0]['bouquet_name']='Unknown'
    with pytest.raises(ValueError,match='twelve'): build_plan(state)


def test_alias_never_changes_sd_to_hd_and_duplicate_addons_fail(state):
    next(r for r in state['channels'] if r['channel_name']=='Flowers')['is_hd']=True
    with pytest.raises(ValueError,match='unmapped'): build_plan(state)
    next(r for r in state['channels'] if r['channel_name']=='Flowers')['is_hd']=False
    state['subscription_channels'].append(dict(_id='duplicate',subscription_id='S1',channel_id='Flowers'))
    with pytest.raises(ValueError,match='duplicate'): build_plan(state)
