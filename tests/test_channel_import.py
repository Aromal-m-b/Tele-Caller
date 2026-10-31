from decimal import Decimal

import pytest

from scripts.import_html_channels import parse_html,plan_import


def html(tmp_path,price='₹10.62',quality='SD'):
    path=tmp_path/'catalog.html'
    path.write_text('''<div class="_chltw"><div class="_hd">Malayalam</div>
    <h2>Original supplier title</h2><table><thead><tr><th>Channel</th><th>Number</th>
    <th>HD/SD</th><th>Price</th><th>Logo</th></tr></thead><tbody><tr class="original-row">
    <td>Mixed CASE Channel</td><td>00406</td><td>'''+quality+'''</td><td>'''+price+'''</td>
    <td><img src="spacer.png" data-original="https://example.invalid/logo.jpg" alt="Original alt"></td>
    </tr></tbody></table></div>''',encoding='utf-8')
    return path


def test_td_values_logo_attributes_and_unknown_features_are_preserved(tmp_path):
    path=html(tmp_path)
    row=parse_html(path)[0]
    assert row['channel_name']=='Mixed CASE Channel'
    assert row['channel_number']=='00406'
    assert row['price']=='₹10.62' and row['price_inr'].to_decimal()==Decimal('10.62')
    assert row['monthly_price_inr'] is None and row['billing_period'] is None
    assert row['source_values']['Price']=='₹10.62'
    assert row['source_cells'][0]['html']=='Mixed CASE Channel'
    assert row['logo_attributes']['src']=='spacer.png'
    assert row['logo_url']=='https://example.invalid/logo.jpg'
    assert row['language']=='Malayalam' and row['genre'] is None
    assert row['source_heading']=='Original supplier title'
    assert parse_html(path)[0]['channel_id']==row['channel_id']


def test_free_and_hd_are_derived_without_changing_original_values(tmp_path):
    row=parse_html(html(tmp_path,price='Free',quality='HD'))[0]
    assert row['price']=='Free' and row['price_inr'].to_decimal()==0
    assert row['is_hd'] is True and row['hd_sd']=='HD'


@pytest.mark.parametrize('price,quality',[('Unknown','SD'),('₹-1','SD'),('Free','4K')])
def test_invalid_source_cannot_silently_become_a_catalog_price(tmp_path,price,quality):
    with pytest.raises(ValueError): parse_html(html(tmp_path,price,quality))


def test_existing_ids_and_unmatched_references_are_retained(tmp_path):
    rows=parse_html(html(tmp_path))
    old=[dict(_id='mongo-old',channel_id='OLD',channel_name='mixed case channel',is_hd=False),
         dict(channel_id='LINKED',channel_name='Older linked channel',is_hd=False)]
    planned,summary=plan_import(rows,old)
    assert planned[0]['channel_id']=='OLD' and planned[0]['_id']=='mongo-old'
    assert planned[0]['channel_name']=='Mixed CASE Channel'
    assert summary['updated_existing']==1 and summary['added']==0
    assert summary['retained_existing']==1 and summary['linked_ids_changed']==0
    assert old[1]['channel_id']=='LINKED'


def test_format_mismatch_does_not_remap_sd_entitlement_to_hd(tmp_path):
    rows=parse_html(html(tmp_path,quality='HD'))
    planned,summary=plan_import(rows,[dict(channel_id='OLD_SD',channel_name='Mixed CASE Channel',is_hd=False)])
    assert planned[0]['channel_id']!='OLD_SD'
    assert summary['added']==1 and summary['retained_existing']==1


def test_unknown_section_is_not_invented_as_a_language(tmp_path):
    path=html(tmp_path)
    path.write_text(path.read_text(encoding='utf-8').replace('Malayalam','Hd Channels'),encoding='utf-8')
    row=parse_html(path)[0]
    assert row['source_section']=='Hd Channels' and row['language'] is None and row['genre'] is None
