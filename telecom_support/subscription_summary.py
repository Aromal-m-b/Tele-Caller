"""Compact caller-scoped plan overview and paginated channel drill-down."""
from collections import Counter
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from .account_tools import DATA_NOTICE,truth
from .channel_support import diagnose


class SummaryArgs(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    language: str | None=Field(default=None,min_length=1,max_length=80)
    genre: str | None=Field(default=None,min_length=1,max_length=80)
    quality: Literal['HD','SD'] | None=None
    channel_type: Literal['Free','Paid','Unspecified'] | None=None
    include_channels: bool=Field(default=False,strict=True)
    offset: int=Field(default=0,ge=0,strict=True)
    limit: int=Field(default=10,ge=1,le=20,strict=True)


DESCRIPTION='Read the bound caller’s actual active package names, validity and deduplicated channel totals by language, genre, HD/SD and Free/Paid. Default returns counts only. Filter and set include_channels=true for paginated names. No customer ID argument.'
GUIDANCE='''For “which plan am I using?”, included-channel summaries or language/genre/HD/SD
follow-ups, call get_subscription_summary. Start with package names, validity and
total channels, then relevant language/genre/format counts. Do not read a full
channel list by default. For deeper questions reuse the caller’s preferences and
filter by language, genre, quality or channel_type; request names only when needed,
in short pages. Counts are distinct channels across active packages and add-ons.
Unspecified means missing source metadata: never infer a channel’s language or
genre from its name or a package name. Paid/Free describes catalog channel price,
not whether the overall subscription is free. Historical plans are not active.
If no current subscription exists, say so; do not assign or claim a plan. No tool
here buys, renews or changes a subscription. Summaries describe database eligibility,
not a guarantee of actual reception.'''


def feature(row,key):
    return row.get(key) or 'Unspecified'


def channel_type(row):
    if row.get('price')=='Free': return 'Free'
    value=row.get('price_inr',row.get('monthly_price_inr'))
    if value is None: return 'Unspecified'
    from .account_tools import money
    return 'Free' if money(value)=='0.00' else 'Paid'


def quality(row):
    if row.get('hd_sd') in {'HD','SD'}: return row['hd_sd']
    if 'is_hd' in row: return 'HD' if truth(row['is_hd']) else 'SD'
    return 'Unspecified'


def execute(account,arguments):
    args=SummaryArgs.model_validate(arguments)
    state=diagnose(account)
    all_rows=[state['catalog'][cid] for cid in state['eligible']]
    dimensions={'language':lambda r:feature(r,'language'),'genre':lambda r:feature(r,'genre'),
                'quality':quality,
                'channel_type':channel_type}
    selected=all_rows
    for key,fn in dimensions.items():
        value=getattr(args,key)
        if value is not None: selected=[r for r in selected if fn(r).casefold()==value.casefold()]
    counts=lambda rows:{key:dict(sorted(Counter(fn(row) for row in rows).items())) for key,fn in dimensions.items()}
    def plan_view(row):
        return {key:value for key,value in row.items() if key!='channel_ids'} | {'channel_count':len(row['channel_ids'])}
    result=dict(ok=True,data_notice=DATA_NOTICE,checked_at=state['checked_at'],
        state='account_inactive' if not state['account_active'] else 'active' if state['active'] else 'no_current_subscription',
        active_plans=[plan_view(p) for p in state['active']],
        noncurrent_plans=[plan_view(p) for p in sorted(state['inactive'],key=lambda p:p['start_date'],reverse=True)[:5]],
        total_eligible_channels=len(all_rows),breakdown=counts(all_rows),
        filters={key:getattr(args,key) for key in dimensions if getattr(args,key) is not None},
        matching_channels=len(selected),matching_breakdown=counts(selected),
        instruction='Summarize counts first. Missing source metadata stays unspecified. Historical plans do not confer access. No account action was performed.')
    if args.include_channels:
        selected.sort(key=lambda r:(r['channel_name'].casefold(),str(r['channel_id'])))
        page=selected[args.offset:args.offset+args.limit]
        result['channels']=[dict(channel_name=r['channel_name'],channel_number=r.get('channel_number'),
            language=feature(r,'language'),genre=feature(r,'genre'),quality=dimensions['quality'](r),
            channel_type=channel_type(r)) for r in page]
        result['next_offset']=args.offset+len(page) if args.offset+len(page)<len(selected) else None
    return result
