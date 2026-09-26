"""Cloud-scheduled educational paper-trading bot. No real transactions or brokerage API."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import random
import re
import traceback

import bot_core as bot

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
PUBLIC = ROOT / 'docs' / 'data' / 'public.json'
QUOTES = DATA / 'last_quotes.json'
ET = ZoneInfo('America/New_York')

# Accept a news headline only if it actually names the company (or ticker as a
# separate 2+ character word). A related-news search alone isn't strong evidence.
ALIASES = {
    'AAPL':['apple'], 'MSFT':['microsoft'], 'NVDA':['nvidia'],
    'AMZN':['amazon'], 'GOOGL':['alphabet','google'], 'META':['meta platforms','facebook'],
    'TSLA':['tesla'], 'JPM':['jpmorgan','jp morgan','chase bank'],
    'XOM':['exxon'], 'UNH':['unitedhealth'], 'JNJ':['johnson & johnson','johnson and johnson'],
    'V':['visa'], 'PG':['procter & gamble','procter and gamble'],
    'MA':['mastercard'], 'HD':['home depot'], 'COST':['costco'],
    'ABBV':['abbvie'], 'BAC':['bank of america'], 'KO':['coca-cola','coca cola'],
    'PEP':['pepsico','pepsi'], 'CRM':['salesforce'], 'NFLX':['netflix'],
    'AMD':['advanced micro devices','amd'], 'INTC':['intel'],
    'CSCO':['cisco'], 'ORCL':['oracle'], 'QCOM':['qualcomm'],
    'ADBE':['adobe'], 'DIS':['disney'], 'MCD':['mcdonald'],
    'NKE':['nike'], 'WMT':['walmart'], 'TGT':['target'],
    'SBUX':['starbucks'], 'CAT':['caterpillar'], 'BA':['boeing'],
    'GE':['ge aerospace'], 'IBM':['ibm','international business machines'],
    'GS':['goldman sachs'], 'MS':['morgan stanley'], 'C':['citigroup','citibank'],
    'CVX':['chevron'], 'COP':['conocophillips'], 'LLY':['eli lilly','lilly'],
    'MRK':['merck'], 'PFE':['pfizer'], 'TMO':['thermo fisher'],
    'AVGO':['broadcom'], 'TXN':['texas instruments'], 'AMAT':['applied materials'],
    'NOW':['servicenow'], 'UBER':['uber'], 'ABNB':['airbnb'],
    'F':['ford'], 'GM':['general motors'],
}


def mentions_company(ticker: str, title: str) -> bool:
    title = str(title or '').lower()
    if any(re.search(r'(?<![a-z])'+re.escape(alias)+r'(?![a-z])', title) for alias in ALIASES.get(ticker, [])):
        return True
    return len(ticker) > 1 and bool(re.search(r'(?<![a-z0-9])'+re.escape(ticker.lower())+r'(?![a-z0-9])',title))


def filtered_news(client, tickers):
    signals, _ = bot.fetch_news_parallel(client, tickers)
    clean = {}
    for ticker, signal in signals.items():
        items = [item for item in signal.items if mentions_company(ticker, item.title)]
        clean[ticker] = bot.summarize_news(ticker, items)
    return clean


def read_quote_cache():
    try:
        return json.loads(QUOTES.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {}


def store_quote_cache(state, quotes):
    QUOTES.write_text(json.dumps({
        'round_market_date':state.round_market_date,
        'market_date':max(q.market_date for q in quotes.values()),
        'fetched_at':datetime.now(timezone.utc).isoformat(),
        'prices':{t:q.price for t,q in quotes.items()},
        'market_timestamps':{t:q.market_timestamp for t,q in quotes.items()},
    },indent=2), encoding='utf-8')


def previous_close_quotes(client, state):
    """Recover old round's end-of-day prices after a missed scheduled run."""
    result={}
    for ticker in state.allocation:
        chart=client._request(ticker,'1mo','1d')
        stamps=chart.get('timestamp') or []
        quotes=((chart.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
        found=None
        for ts,price in zip(stamps,quotes):
            date=datetime.fromtimestamp(int(ts),ET).date().isoformat()
            if date == state.round_market_date and price is not None and float(price)>0:
                found=bot.Quote(ticker,float(price),int(ts),date)
        if found is None:
            raise RuntimeError(f'No historical closing price for {ticker} on {state.round_market_date}.')
        result[ticker]=found
    return result


def complete_and_clear(state,quotes):
    if set(quotes) != set(state.allocation):
        raise RuntimeError('Missing a selected company price; round was not settled.')
    dates={q.market_date for q in quotes.values()}
    if dates != {state.round_market_date}:
        raise RuntimeError('Inconsistent market dates; round was not settled.')
    snap=bot.make_snapshot(state,quotes)
    bot.settle_round(state,snap)
    state.active_strategy=None
    state.allocation={}
    state.entry_prices={}
    state.round_market_date=None
    state.round_started_at=None
    state.entry_news_signals={}
    state.news_signals={}
    state.selection_scores={}
    QUOTES.unlink(missing_ok=True)


def make_new_pick(state,client,market_date):
    """Automatically reroll at most once per market day, without reusing yesterday's holdings."""
    if state.allocation or state.last_reroll_market_date == market_date:
        raise RuntimeError("This trading day has already received its automatic company picks.")
    previous=set(state.last_round_tickers)
    fresh=[ticker for ticker in state.tickers if ticker not in previous]
    if not fresh: fresh=state.tickers.copy()
    count=min(len(fresh),max(state.max_positions+4,state.candidate_pool_size))
    candidates=random.sample(fresh,count) if len(fresh)>count else fresh
    historical,_=bot.fetch_history_parallel(client,candidates,'1y')
    features=bot.build_features(historical)
    if not features:
        raise RuntimeError('Could not download enough stock history to pick companies.')
    strategy=bot.choose_strategy(state.strategy_scores,state.rounds_completed)
    news=filtered_news(client,features) if state.news_enabled else {}
    allocation,scores=bot.choose_allocation(
        features,strategy,min(state.max_positions,len(features)),
        excluded_tickers=previous,news_signals=news,
        news_weight=bot.effective_news_weight(state)
    )
    selected_quotes=bot.fetch_quotes_parallel(client,allocation)
    if {q.market_date for q in selected_quotes.values()}!={market_date}:
        raise RuntimeError('At least one company price is stale. Waiting for fresh market-day quotes.')
    state.active_strategy=strategy
    state.allocation=allocation
    state.entry_prices={t:q.price for t,q in selected_quotes.items()}
    state.round_market_date=market_date
    state.last_reroll_market_date=market_date
    state.round_started_at=datetime.now(timezone.utc).isoformat()
    state.news_signals={t:s.to_dict() for t,s in news.items()}
    state.entry_news_signals={t:state.news_signals.get(t,{}) for t in allocation}
    state.selection_scores=scores
    state.news_last_checked_at=datetime.now(timezone.utc).isoformat()
    store_quote_cache(state,selected_quotes)


def render_public(state, status='', error=''):
    cache=read_quote_cache()
    live=None
    if state.allocation and cache.get('round_market_date')==state.round_market_date:
        prices=cache.get('prices') or {}
        if set(prices)==set(state.allocation):
            weighted=sum(weight*(float(prices[t])/state.entry_prices[t]-1)
                         for t,weight in state.allocation.items() if state.entry_prices.get(t,0)>0)
            live={
                'points':round(100*weighted,6),
                'money_change':round(state.cash_at_round_start*weighted,6),
                'cash':round(max(0,state.cash_at_round_start*(1+weighted)),6),
                'market_date':cache.get('market_date'),
                'quote_time':cache.get('fetched_at'),
                'holdings':[{
                    'ticker':t,'weight':round(w,6),'entry':state.entry_prices.get(t),
                    'last_price':prices.get(t),
                    'points':round(100*w*(prices[t]/state.entry_prices[t]-1),5),
                    'money_change':round(state.cash_at_round_start*w*(prices[t]/state.entry_prices[t]-1),5),
                    'headline':(state.entry_news_signals.get(t) or {}).get('top_headline',''),
                    'publisher':(state.entry_news_signals.get(t) or {}).get('publisher',''),
                    'link':(state.entry_news_signals.get(t) or {}).get('link',''),
                } for t,w in state.allocation.items()],
            }
    daily=bot.read_daily_history(limit=2000)
    rounds=bot.read_history(limit=5000)
    obj={
        'updated_at':datetime.now(timezone.utc).isoformat(),
        'status':status,
        'error':error[:350],
        'market_data_note':'Prices may be delayed or unavailable; updates are scheduled, not streaming.',
        'starting_cash':20.0,'goal_points':state.goal,
        'auto_reroll':True,
        'last_reroll_market_date':state.last_reroll_market_date,
        'reroll_policy':'Every U.S. trading day, at the first successful scheduled market-data check after 9:35 a.m. Eastern. Previous day is settled first.',
        'banked_points':round(state.banked_points,6),
        'display_points':round(state.banked_points+(live['points'] if live else 0),6),
        'completed_balance':round(state.cash_at_round_start,6),
        'display_balance':round(live['cash'] if live else state.cash_at_round_start,6),
        'completed_rounds':state.rounds_completed,
        'current_strategy':state.active_strategy,
        'strategy_scores':{k:round(float(v),5) for k,v in state.strategy_scores.items()},
        'news_learning_score':round(state.news_learning_score,5),
        'live':live,'daily':daily,
        'rounds':[{k:v for k,v in row.items() if k != 'news_snapshot'} for row in rounds],
        'import_note':'Imported completed desktop rounds; the September 17 open INTC holding was not carried over because its price was stale at migration.',
    }
    PUBLIC.parent.mkdir(parents=True,exist_ok=True)
    PUBLIC.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    return obj


def tick(now=None,client=None):
    now=now or datetime.now(ET)
    now=now.astimezone(ET)
    state=bot.load_state()
    if state is None:
        if bot.STATE_FILE.exists():
            raise RuntimeError("Saved bot state could not be read. Existing game was not reset.")
        state=bot.BotState()
    client=client or bot.YahooChartClient()
    market_date=now.date().isoformat()
    is_weekday=now.weekday()<5
    during_session=is_weekday and time(9,35)<=now.time()<time(16,0)
    after_close=is_weekday and time(16,10)<=now.time()<time(20,0)
    status='Market closed; cloud bot will check again on its next scheduled run.'
    try:
        if state.allocation:
            if state.round_market_date and state.round_market_date<market_date:
                previous=previous_close_quotes(client,state)
                complete_and_clear(state,previous)
                status='Previous trading day settled from its historical closing prices.'
            elif during_session or after_close:
                quotes=bot.fetch_quotes_parallel(client,state.allocation)
                dates={q.market_date for q in quotes.values()}
                if dates=={state.round_market_date}:
                    store_quote_cache(state,quotes)
                    if after_close and state.round_market_date==market_date:
                        complete_and_clear(state,quotes)
                        status='Trading day completed. Points and fake profit/loss saved.'
                    else:
                        status='Tracking today’s fake holdings with available market prices.'
                else:
                    status='Waiting for current market-day prices before changing the fake balance.'
        if (not state.allocation and during_session
                and state.last_reroll_market_date != market_date
                and state.banked_points < state.goal and state.cash_at_round_start > 0.01):
            make_new_pick(state,client,market_date)
            status='Daily auto-reroll complete: new fake company picks for today.'
        elif not state.allocation and state.last_reroll_market_date == market_date:
            status='Today’s company picks are complete. The next automatic reroll is on the next trading day.'
        elif not state.allocation and state.banked_points>=state.goal:
            status='Goal reached! New trades are paused.'
        elif not state.allocation and state.cash_at_round_start<=0.01:
            status='Fake balance is depleted. New trades are paused.'
        bot.save_state(state)
        return render_public(state,status)
    except Exception as exc:
        # A previous round may already have settled before a later pick/download failed.
        # Persist that completed state so a retry cannot double-book the same round.
        bot.save_state(state)
        return render_public(state,'Data update could not finish; last valid paper results were preserved.',str(exc))


if __name__=='__main__':
    result=tick()
    print(json.dumps({k:result[k] for k in ('updated_at','status','error','banked_points','display_points','display_balance','completed_rounds')},indent=2))
