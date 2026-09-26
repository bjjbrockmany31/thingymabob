import json
from pathlib import Path
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import ANY, Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import bot_core as bot
import run_cloud as cloud


class CloudTests(unittest.TestCase):
    def test_imported_history_preserved(self):
        state=bot.load_state()
        self.assertEqual(state.rounds_completed,22)
        self.assertAlmostEqual(state.cash_at_round_start,18.559054624708317)
        self.assertAlmostEqual(state.banked_points,-6.731036037493596)
        self.assertEqual(state.allocation,{})  # Old open INTC round is deliberately not counted.
        self.assertEqual(len(bot.read_history(limit=5000)),22)
        self.assertEqual(len(bot.read_daily_history(limit=5000)),15)

    def test_scoring_and_losses(self):
        state=bot.BotState()
        state.allocation={'AAPL':0.5,'MSFT':0.5}
        state.entry_prices={'AAPL':100,'MSFT':100}
        snap=bot.make_snapshot(state,{
            'AAPL':bot.Quote('AAPL',102,0,'2026-09-25'),
            'MSFT':bot.Quote('MSFT',99,0,'2026-09-25')})
        self.assertAlmostEqual(snap.round_points,0.5)
        self.assertAlmostEqual(snap.live_cash,20.1)
        down=bot.make_snapshot(state,{
            'AAPL':bot.Quote('AAPL',98,0,'2026-09-25'),
            'MSFT':bot.Quote('MSFT',99,0,'2026-09-25')})
        self.assertAlmostEqual(down.round_points,-1.5)
        self.assertAlmostEqual(down.live_cash,19.7)

    def test_unrelated_news_not_attributed_to_company(self):
        self.assertFalse(cloud.mentions_company('INTC','SpaceX stock climbs on new launch'))
        self.assertTrue(cloud.mentions_company('INTC','Intel announces new manufacturing plans'))
        self.assertFalse(cloud.mentions_company('F','Fear rises as markets wobble'))
        self.assertTrue(cloud.mentions_company('F','Ford opens a new assembly plant'))

    def test_public_json_reports_daily_money_and_points(self):
        sample=json.loads((ROOT/'docs/data/public.json').read_text())
        self.assertEqual(len(sample['daily']),15)
        self.assertEqual(sample['completed_rounds'],22)
        for day in sample['daily']:
            self.assertAlmostEqual(day['cash_after']-day['cash_before'],day['cash_change'])
            self.assertIsInstance(day['point_change'],float)

    def test_closed_market_does_not_select_new_companies(self):
        fake_time=datetime(2026,9,26,12,0,tzinfo=ZoneInfo('America/New_York'))
        with patch.object(cloud,'render_public',return_value={'status':'Market closed'}), \
             patch.object(bot,'save_state') as save, \
             patch.object(cloud,'make_new_pick') as pick:
            result=cloud.tick(now=fake_time,client=object())
            self.assertEqual(result['status'],'Market closed')
            pick.assert_not_called()
            save.assert_called_once()


    def test_auto_reroll_runs_once_per_trading_day(self):
        state=bot.BotState()
        state.last_reroll_market_date='2026-09-24'
        today=datetime(2026,9,25,10,0,tzinfo=ZoneInfo('America/New_York'))
        yesterday=datetime(2026,9,24,10,0,tzinfo=ZoneInfo('America/New_York'))
        with patch.object(bot,'load_state',return_value=state), \
             patch.object(cloud,'render_public',side_effect=lambda s, status, error='':{'status':status}), \
             patch.object(bot,'save_state'), \
             patch.object(cloud,'make_new_pick') as pick:
            cloud.tick(now=yesterday,client=object())
            pick.assert_not_called()
            cloud.tick(now=today,client=object())
            pick.assert_called_once_with(state, ANY, '2026-09-25')

    def test_new_company_pick_excludes_previous_and_sets_daily_guard(self):
        state=bot.BotState()
        state.tickers=['AAPL','MSFT','NVDA','GOOGL','META','ORCL']
        state.last_round_tickers=['AAPL','MSFT']
        state.candidate_pool_size=8
        chosen={'NVDA':0.6,'GOOGL':0.4}
        quotes={t:bot.Quote(t,100,1000,'2026-09-25') for t in chosen}
        fake_features={t:{'close':100} for t in state.tickers}
        with patch.object(bot,'fetch_history_parallel',side_effect=lambda client,tickers,p:( {t:[(0,100)]*60 for t in tickers},[])) as histories, \
             patch.object(bot,'build_features',return_value=fake_features), \
             patch.object(bot,'choose_strategy',return_value='steady'), \
             patch.object(cloud,'filtered_news',return_value={}), \
             patch.object(bot,'choose_allocation',return_value=(chosen,{})) as allocation, \
             patch.object(bot,'fetch_quotes_parallel',return_value=quotes), \
             patch.object(cloud,'store_quote_cache'):
            cloud.make_new_pick(state,object(),'2026-09-25')
            self.assertEqual(state.last_reroll_market_date,'2026-09-25')
            self.assertEqual(set(state.allocation),{'NVDA','GOOGL'})
            self.assertTrue(set(histories.call_args.args[1]).isdisjoint({'AAPL','MSFT'}))
            self.assertEqual(allocation.call_args.kwargs['excluded_tickers'],{'AAPL','MSFT'})
            with self.assertRaisesRegex(RuntimeError,'already received'):
                cloud.make_new_pick(state,object(),'2026-09-25')

    def test_rollover_settles_previous_day_before_next_pick(self):
        state=bot.BotState()
        state.round_market_date='2026-09-24'
        state.last_reroll_market_date='2026-09-24'
        state.allocation={'AAPL':1.0}
        state.entry_prices={'AAPL':100.0}
        state.active_strategy='trend'
        state.round_started_at='2026-09-24T14:00:00Z'
        today=datetime(2026,9,25,10,0,tzinfo=ZoneInfo('America/New_York'))
        def fake_settle(s,snap):
            s.cash_at_round_start=snap.live_cash
            s.banked_points += snap.round_points
            s.rounds_completed += 1
            s.last_round_tickers=list(s.allocation)
        def fake_pick(s,client,date):
            self.assertEqual(s.allocation,{})
            self.assertEqual(s.rounds_completed,1)
            self.assertAlmostEqual(s.banked_points,1.0)
            self.assertEqual(s.last_round_tickers,['AAPL'])
            s.allocation={'MSFT':1.0}
            s.entry_prices={'MSFT':100.0}
            s.round_market_date=date
            s.last_reroll_market_date=date
        with patch.object(bot,'load_state',return_value=state), \
             patch.object(bot,'settle_round',side_effect=fake_settle), \
             patch.object(cloud,'previous_close_quotes',return_value={'AAPL':bot.Quote('AAPL',101,1000,'2026-09-24')}), \
             patch.object(cloud,'make_new_pick',side_effect=fake_pick) as picker, \
             patch.object(bot,'save_state'), \
             patch.object(cloud,'render_public',side_effect=lambda s,status,error='':{'status':status}), \
             patch.object(cloud,'QUOTES',Mock()):
            r=cloud.tick(now=today,client=object())
            self.assertIn('auto-reroll',r['status'])
            picker.assert_called_once()
            self.assertEqual(state.allocation,{'MSFT':1.0})

if __name__=='__main__':
    unittest.main()
