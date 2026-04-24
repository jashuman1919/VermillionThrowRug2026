# -*- coding: utf-8 -*-

import json
import requests
from random import random, shuffle, sample
from datetime import date, datetime, timezone
from copy import deepcopy
from numpy import linspace

SETTINGS_UPDATE_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/settings?scoringPeriodId=0'
EMAIL_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/communication/topics'
BOX_SCORE_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?scoringPeriodId={scoring_period}&view=mBoxscore&view=mMatchupScore'
ADJUST_SCORE_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/schedule'
SCHEDULE_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=mMatchupScore&view=mTeam'
TRANSACTION_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/transactions'
ROSTER_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=mRoster&{roster_for_team_id}'
PLAYERS_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=kona_player_info'

DEBUG = False
PLAYER_TRANSACTIONS = True

BATTER_POS_QUANTITIES = {0: 1,
                         1: 1,
                         2: 1,
                         3: 1,
                         4: 1,
                         8: 1,
                         9: 1,
                         10: 1,
                         11: 1}

PITCHER_POS_QUANTITIES = {14: 5,
                          15: 5}

MAX_DROP_PROB = 0.05

def first_digit_even(number):
    first_digit = int(str(abs(number))[0])
    return (first_digit % 2 == 0)

def point_update_email_content(default_scores, updated_scores):
    removed = []
    doubled = []

    with open('data/stat_ids.json') as f:
        stat_ids = json.load(f)

    with open('data/email_content.txt') as f:
        email_content = f.read()

    default_dict = {str(s['statId']): s['points'] for s in default_scores['scoringSettings']['scoringItems']}
    updated_dict = {str(s['statId']): s['points'] for s in updated_scores['scoringSettings']['scoringItems']}

    for id, stat in stat_ids.items():
        if id in updated_dict:
            doubled.append((stat, default_dict[id], updated_dict[id]))
        else:
            removed.append(stat)

    removed_str = '    ' + '\n    '.join(removed)
    doubled_strs = [f'{stat}: ({old} -> {new})' for stat, old, new in doubled]
    doubled_str = '    ' + '\n    '.join(doubled_strs)
    email_content = email_content.format(date=date.today(),
                                         removed=removed_str,
                                         doubled=doubled_str)
    return email_content, removed_str, doubled_str

def get_point_bonus_message(team, score):
    if first_digit_even(score):
        score = round(score + 100, 1)
        return score, f'{team} score starts with an even number, so receives 100-point bonus; Ends at {score}.\n'
    else:
        score = round(score - 100, 1)
        return score, f'{team} score starts with an odd number, so incurs 100-point penalty; Ends at {score}.\n'
    
def get_score_adjustment(team_data, settings, reason):
    current_adjustment = team_data['adjustment']
    current_score = team_data['totalPoints'] - current_adjustment
    team_id = team_data['teamId']
    score = 0
    for player in team_data['rosterForMatchupPeriod']['entries']:
        stats = player['playerPoolEntry']['player']['stats'][0]['stats']
        score += sum(settings.get(id, 0) * val for id, val in stats.items() if isinstance(val, (int, float)))
    score = round(score, 1)
    if first_digit_even(score):
        score += 100
        reason = reason.format(adjustment="even-first-digit bonus (+100)")
    else:
        score -= 100
        reason = reason.format(adjustment="odd-first-digit penalty (-100)")
    adjustment = round(score - current_score, 1)
    adjustment_dict = {'adjustment': adjustment,
                       'adjustmentReason': reason,
                       'teamId': team_id}
    return adjustment_dict, (adjustment != current_adjustment)

def player_filter(pos, num):
    return {
        'players': {
            'filterStatus': {
                'value': [
                    'FREEAGENT'
                ]
            },
            'filterInjured': {
                'value': False
            },
            'filterSlotIds': {
                'value': [
                    pos
                ]
            },
            'limit': num,
            'offset': 0,
            'sortAppliedStatTotal': {
                'sortAsc': False,
                'sortPriority': 1,
                'value': '002026'
            },
            'filterStatsForTopScoringPeriodIds': {
                'value': 1,
                'additionalValue': [
                    '002026'
                ]
            }
        }
    }

def find_necessary_transactions(players, pos_quantities, results=None, assignments=None, idx=0):
    if results is None:
        results = []
    if assignments is None:
        assignments = {}

    if idx == len(players):
        pickups = {}
        drops = {p['playerId'] for p in players if p['playerId'] not in assignments}
        for position, quantity in pos_quantities.items():
            filled = list(assignments.values()).count(position)
            pickups[position] = quantity - filled
        result = (pickups, drops, deepcopy(assignments))
        if result not in results:
            results.append(result)
        return {}, set(), {}

    player = players[idx]
    id = player['playerId']
    for position in player['playerPoolEntry']['player']['eligibleSlots']:
        if position in pos_quantities and pos_quantities[position] > 0:
            if list(assignments.values()).count(position) < pos_quantities[position]:
                assignments[id] = position
                find_necessary_transactions(players, pos_quantities, results, assignments, idx + 1)
                del assignments[id]
    find_necessary_transactions(players, pos_quantities, results, assignments, idx + 1)
    
    if idx == 0:
        percent_owned_dict = {p['playerId']: p['playerPoolEntry']['player']['ownership']['percentOwned'] for p in players}
        min_dropped_players = min(len(r[1]) for r in results)
        acceptable_results = [r for r in results if len(r[1]) == min_dropped_players]
        best_result = min(acceptable_results, key=lambda x: sum(percent_owned_dict[p] for p in x[1]))
        return best_result
        
    return {}, set(), {}

class Mock_Http_Response:
    def __init__(self, ok, status_code, data):
        self.ok = ok
        self.status_code = status_code
        self.data = data

    def json(self):
        return json.loads(self.data)

class Vermillion_Throw_Rug_Runner:
    def __init__(self, cookies_file):
        with open(cookies_file) as f:
            self.cookies = json.load(f)

    def http_request(self, url, headers={}, data=None, attempts=5):
        response = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        for attempt in range(attempts):
            if data is None:
                response = requests.get(url, cookies=self.cookies, headers=headers)
            else:
                if DEBUG:
                    response = Mock_Http_Response(False, 400, '{}')
                    break
                else:
                    response = requests.post(url, cookies=self.cookies, headers=headers, data=data)
            if response.ok:
                break
        return response
        
    def send_email(self, recipient, subject, message):
        payload = {'content': message,
                   'subject': subject,
                   'type': 'EMAIL',
                   'viewableBy': [recipient]}
        payload_str = json.dumps(payload)
        response = self.http_request(EMAIL_URL, data=payload_str)
        return response
    
    def update_stat_points(self, matchup_period_id):
        print('################ Updating stat point values. ################')
        with open('data/default_stats_scores.json') as f:
            default_scores = json.load(f)
        updated_scores = deepcopy(default_scores)
        items = updated_scores['scoringSettings']['scoringItems']
        items = sample(items, len(items) // 2)
        for item in items:
            item['points'] = round(item['points'] * 2, 1)
        updated_scores['scoringSettings']['scoringItems'] = items
        updated_scores_str = json.dumps(updated_scores)
    
        response = self.http_request(SETTINGS_UPDATE_URL, data=updated_scores_str)
        email_content, removed_str, doubled_str = point_update_email_content(default_scores, updated_scores)
    
        if response.ok:
            print('Success updating stat point values.')
            items_dict = {item['statId']: item['points'] for item in items}
            if not DEBUG:
                with open('data/matchup_settings.json') as f:
                    matchup_settings = json.load(f) 
                matchup_settings[str(matchup_period_id)] = items_dict
                with open('data/matchup_settings.json', 'w') as f:
                    json.dump(matchup_settings, f, indent=2)
        else:
            print(f'WARNING: Unable to update point values (status code {response.status_code}).')
            date_str = str(date.today())
            filename = f'debug/stat_point_changes_{date_str}.json'
            with open(filename, 'w') as f:
                json.dump(updated_scores, f)
            print(f'  Saving stat point value update payload to file: {filename}')
            print(f'  Manual stat removal:\n{removed_str}')
            print(f'  Manual stat updates:\n{doubled_str}')
    
        # email_subject = f'Vermillion Throw Rug point changes {date.today()}'
        # if response.ok:
        #     print('Success updating points. Sending email.')
        #     response = send_email(recipient='{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
        #                           subject=email_subject,
        #                           message=email_content,
        #                           headers=headers)
        #     if not response.ok:
        #         print(f'WARNING: Unable to send email (status code {response.status_code}).')
        # else:
        #     print(f'WARNING: Unable to update points (status code {response.status_code}).')
    
        print('Done updating stat point values')
    
        return updated_scores, email_content, response
    
    def get_box_scores(self, scoring_period, matchup_period):
        url = BOX_SCORE_URL.format(scoring_period=scoring_period)
        filter = '{"schedule":{"filterMatchupPeriodIds":{"value":[' + str(matchup_period) + ']}}}'
        headers = {'x-fantasy-filter': filter}
        response = self.http_request(url, headers)
        data = response.json()
        return data
    
    def recalculate_scores(self, past_periods, team_dict):
        print('################ Recalculating scores for past matchups. ################')
        print(f'Matchups: {list(past_periods)}')
        with open('data/matchup_settings.json') as f:
            matchup_settings = json.load(f)
    
        adjustments = []
        adjustments_debug = {}
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
        reason = f'Maintaining original matchup score as of {now_str}. Includes {{adjustment}}.'
        for matchup_period, scoring_period in past_periods.items():
            adjustments_debug[matchup_period] = []
            data = self.get_box_scores(scoring_period, matchup_period)
            settings = matchup_settings[str(matchup_period)]
            for matchup in data['schedule']:
                id = matchup['id']
                home_adjustment, home_adjustment_changed = get_score_adjustment(matchup['home'], settings, reason)
                away_adjustment, away_adjustment_changed = get_score_adjustment(matchup['away'], settings, reason)
                if home_adjustment_changed or away_adjustment_changed:
                    adjustments.append({'away': away_adjustment,
                                        'home': home_adjustment,
                                        'id': id})
                    adjustments_debug[matchup_period].append({'id': id,
                                                              'away': team_dict[away_adjustment['teamId']],
                                                              'home': team_dict[home_adjustment['teamId']],
                                                              'away_adjustment': away_adjustment['adjustment'],
                                                              'home_adjustment': home_adjustment['adjustment']})
                    print(f'Recalculated score and adjustments for matchup {id}.')
                else:
                    print(f'Recalculated score for matchup {id}. No adjustments required.')
        
        if len(adjustments) > 0:
            response = self.http_request(ADJUST_SCORE_URL, data=json.dumps(adjustments))
            if response.ok:
                print('Adjusted all past scores.')
            else:
                print(f'WARNING: Failed to adjust past scores (status code {response.status_code}).')
                date_str = str(date.today())
                filename = f'debug/score_adjustments_{date_str}.json'
                with open(filename, 'w') as f:
                    json.dump(adjustments, f)
                print(f'  Saving adjustments payload to file: {filename}')
                print('  Manual adjustments:')
                for period, matchups in adjustments_debug.items():
                    print(f'    Matchup period {period}:')
                    for matchup in matchups:
                        print(f'      Matchup {matchup["id"]}:')
                        print(f'        {matchup["away"]}: {matchup["away_adjustment"]}')
                        print(f'        {matchup["home"]}: {matchup["home_adjustment"]}')
        else:
            print('No adjustments needed.')
    
        print('Done recalculating scores for past matchups.')
    
    def get_basic_info(self):
        print('################ Getting basic league info. ################')
        response = self.http_request(SCHEDULE_URL)
        data = response.json()
        current_scoring_period = data['scoringPeriodId']
        schedule = data['schedule']
        past_periods = {}
        for matchup in schedule:
            matchup_period = matchup['matchupPeriodId']
            if matchup_period not in past_periods:
                points_by_scoring_period = matchup.get('home', {}).get('pointsByScoringPeriod', {})
                scoring_periods = [int(p) for p in points_by_scoring_period]
                if len(scoring_periods) > 0 and all(p < current_scoring_period for p in scoring_periods):
                    past_periods[matchup_period] = max(scoring_periods)
    
        teams = data['teams']
        team_dict = {t['id']: t['abbrev'] for t in teams}
        team_str = '  ' + '\n  '.join(f'{k}: {v}' for k, v in team_dict.items())
        print(f'Current scoring period: {current_scoring_period}')
        print(f'Current matchup: {max(past_periods) + 1}')
        print(f'Teams:\n{team_str}')
        print('Done getting basic league info.')
        return past_periods, current_scoring_period, team_dict
    
    def drop_best_player(self, team, current_scoring_period, team_dict):
        team_id = team['teamId']
        players = team['rosterForMatchupPeriod']['entries']
        player_scores = {p['playerPoolEntry']['id']: (p['playerPoolEntry']['player']['fullName'], p['playerPoolEntry']['player']['stats'][0]['appliedTotal']) for p in players}
        best_player_id = max(player_scores, key=lambda x: player_scores[x][1])
        best_player_name = player_scores[best_player_id][0]
    
        payload = {'isLeagueManager': True,
                   'teamId': team_id,
                   'type': 'ROSTER',
                   'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                   'scoringPeriodId': current_scoring_period,
                   'executionType': 'EXECUTE',
                   'items': [{'playerId': best_player_id,
                              'type': 'DROP',
                              'fromTeamId': team_id}]}
        payload_str = json.dumps(payload)
        if PLAYER_TRANSACTIONS:
            response = self.http_request(TRANSACTION_URL, data=payload_str)
            if response.ok:
                print(f'Dropped {best_player_name} from {team_dict[team_id]}.')
            else:
                print(f'WARNING: Unable to drop {best_player_name} from {team_dict[team_id]} (status code {response.status_code}).')
        else:
            print(f'Would have dropped {best_player_name} from {team_dict[team_id]}.')
        return best_player_name
    
    def last_week_results(self, scoring_period, matchup_period, current_scoring_period, team_dict):
        print(f'################ Checking and dropping best players from matchup period {matchup_period}. ################')
        message = f'__**WEEK {matchup_period} RESULTS/UPDATES**__\n'
        data = self.get_box_scores(scoring_period, matchup_period)
        schedule = data['schedule']
        for matchup in schedule:
            away_score = matchup['away']['totalPoints']
            home_score = matchup['home']['totalPoints']
            away_team = team_dict[matchup['away']['teamId']]
            home_team = team_dict[matchup['home']['teamId']]
            message += f'**{away_team} vs. {home_team}**\n'
            message += f'Initial Score: {away_team} {away_score} - {home_team} {home_score}\n'
            score_difference = round(abs(home_score - away_score), 1)
            if first_digit_even(score_difference):
                message += f'Difference of {score_difference}, starting with even number. So no drops.\n'
            else:
                away_player = self.drop_best_player(matchup['away'], current_scoring_period, team_dict)
                home_player = self.drop_best_player(matchup['home'], current_scoring_period, team_dict)
                message += f'Difference of {score_difference}, starting with odd number. {away_team} drops {away_player}. {home_team} drops {home_player}.\n'
            away_score, message_line = get_point_bonus_message(away_team, away_score)
            message += message_line
            home_score, message_line = get_point_bonus_message(home_team, home_score)
            message += message_line
            message += f'Final score: {away_team} {away_score} - {home_team} {home_score}\n'
        print('Done checking and dropping players.')
        return message

    def select_players(self, pos, num):
        num_options = round(num * 1.5 + 3)
        filter = player_filter(pos, num_options)
        headers = {'x-fantasy-filter': json.dumps(filter)}
        response = self.http_request(PLAYERS_URL, headers)
        data = response.json()
        players = sample(data['players'], num)
        ids = [p['id'] for p in players]
        return ids
    
    def get_roster(self, team_ids):
        roster_for_team_id = '&'.join(f'rosterForTeamId={id}' for id in team_ids)
        url = ROSTER_URL.format(roster_for_team_id=roster_for_team_id)
        response = self.http_request(url)
        data = response.json()
        teams = [t for t in data['teams'] if t['id'] in team_ids]
        return teams

    def bot_transactions(self, scoring_period):
        """
        Check for injured or suspended. Add to drop list.
        Create list of players without injured/suspended players.
        Find necessary transactions using shortened player list.
        Add necessary drops to drop list.
        Find best replacement players.
        Add and drop.
        """

        print('################ Bot transactions. ################')

        bot_ids = [3, 4]

        teams = self.get_roster(bot_ids)
        shuffle(teams)
        
        for team in teams:
            team_id = team['id']
            players = sorted(team['roster']['entries'], key=lambda x: x['playerPoolEntry']['player']['stats'][1]['appliedTotal'], reverse=True)
            drop_probs = linspace(0, MAX_DROP_PROB, len(players))
            drops = []
            for n, p in enumerate(players):
                player = p['playerPoolEntry']['player']
                drop_prob = drop_probs[n]
                if player['injured'] or player['injuryStatus'] == 'SUSPENSION' or random() <= drop_prob:
                    drops.append(player['id'])
            batters = [p for p in players if 12 in p['playerPoolEntry']['player']['eligibleSlots'] and p['playerId'] not in drops]
            pitchers = [p for p in players if 12 not in p['playerPoolEntry']['player']['eligibleSlots'] and p['playerId'] not in drops]
            print('Arranging batters')
            batter_adds, batter_drops, batter_assignments = find_necessary_transactions(batters, BATTER_POS_QUANTITIES)
            print('Arranging pitchers')
            pitcher_adds, pitcher_drops, pitcher_assignments = find_necessary_transactions(pitchers, PITCHER_POS_QUANTITIES)
            drops.extend(batter_drops)
            drops.extend(pitcher_drops)
            pos_adds = batter_adds | pitcher_adds
            assignments = batter_assignments | pitcher_assignments

            # Drops
            if len(drops) > 0:
                drop_items = [{'playerId': id, 'type': 'DROP', 'fromTeamId': team_id} for id in drops]
                payload = {
                    'isLeagueManager': False,
                    'teamId': team_id,
                    'type': 'ROSTER',
                    'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                    'scoringPeriodId': scoring_period,
                    'executionType': 'EXECUTE',
                    'items': drop_items
                }
                payload_str = json.dumps(payload)
                if PLAYER_TRANSACTIONS:
                    response = self.http_request(TRANSACTION_URL, data=payload_str)
                    if response.ok:
                        print(f'Completed drops for bot team ID {team_id}.')
                    else:
                        print(f'WARNING: Unable to perform drops for bot team ID {team_id} (status code {response.status_code}).')
                        date_str = str(date.today())
                        filename = f'debug/bot_{team_id}_drops_{date_str}.json'
                        with open(filename, 'w') as f:
                            json.dump(payload, f)
                        print(f'  Saving drops payload to file: {filename}')
            else:
                print(f'No drops for bot team ID {team_id}.')

            # Adds
            adds = {}
            for pos, num in pos_adds.items():
                if num > 0:
                    print(f'Finding replacement players for position {pos}.')
                    ids = self.select_players(pos, num)
                    adds |= {id: pos for id in ids}

            if len(adds) > 0:
                failed_add_payloads = []
                for id, pos in adds.items():
                    payload = {
                        'isLeagueManager': False,
                        'teamId': team_id,
                        'type': 'FREEAGENT',
                        'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                        'scoringPeriodId': scoring_period,
                        'executionType': 'EXECUTE',
                        'items': [
                            {
                                'playerId': id,
                                'type': 'ADD',
                                'toTeamId': team_id,
                                'toLineupSlotId': pos
                            }
                        ]
                    }
                    payload_str = json.dumps(payload)
                    if PLAYER_TRANSACTIONS:
                        response = self.http_request(TRANSACTION_URL, data=payload_str)
                        if response.ok:
                            print(f'Added player {id} to bot team ID {team_id}.')
                            assignments[id] = pos
                        else:
                            print(f'WARNING: Failed to add player {id} to bot team ID {team_id} (status code {response.status_code}).')
                            failed_add_payloads.append(payload)
    
                if len(failed_add_payloads) > 0:
                    date_str = str(date.today())
                    filename = f'debug/bot_{team_id}_adds_{date_str}.json'
                    print('Saving failed add payloads to file: {filename}')
                    with open(filename, 'w') as f:
                        json.dump(failed_add_payloads, f)
            else:
                print(f'No adds for bot team ID {team_id}.')

            # Re-check lineup
            team = self.get_roster([team_id])[0]
            players = team['roster']['entries']
            current_pos_dict = {p['playerId']: p['lineupSlotId'] for p in players}
            changed_assignments = {p: pos for p, pos in assignments.items() if pos != current_pos_dict[p]}

            # Moves
            if len(changed_assignments) > 0:
                move_items = [{'playerId': p, 'type': 'LINEUP', 'toLineupSlotId': pos} for p, pos in changed_assignments.items()]
                payload = {
                    'isLeagueManager': False,
                    'teamId': team_id,
                    'type': 'ROSTER',
                    'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                    'scoringPeriodId': scoring_period,
                    'executionType': 'EXECUTE',
                    'items': move_items
                }
                payload_str = json.dumps(payload)
                if PLAYER_TRANSACTIONS or True:
                    response = self.http_request(TRANSACTION_URL, data=payload_str)
                    if response.ok:
                        print(f'Successfully arranged lineup for bot team ID {team_id}.')
                    else:
                        print(f'WARNING: Failed to arrange lineup for bot team ID {team_id} (status code {response.status_code}).')
                        date_str = str(date.today())
                        filename = f'debug/bot_{team_id}_lineup_{date_str}.json'
                        with open(filename, 'w') as f:
                            json.dump(payload, f)
                        print(f'  Saving lineup payload to file: {filename}')
            else:
                print(f'No lineup arrangement needed for bot team ID {team_id}.')

    def run(self):
        past_periods, current_scoring_period, team_dict = self.get_basic_info()
        last_matchup_period = max(past_periods)
        last_matchup_last_scoring_period = past_periods[last_matchup_period]
        if current_scoring_period == last_matchup_last_scoring_period + 1:
            print('################ Start of matchup. ################')
            last_week_results_message = self.last_week_results(last_matchup_last_scoring_period, last_matchup_period, current_scoring_period, team_dict)
            _, stat_updates_message, _ = self.update_stat_points(last_matchup_period + 1)

            date_str = str(date.today())
            filename = f'output/last_week_results_{date_str}.txt'
            with open(filename, 'w') as f:
                f.write(last_week_results_message)
            filename = f'output/stat_updates_{date_str}.txt'
            with open(filename, 'w') as f:
                f.write(stat_updates_message)
        else:
            print('################ Not start of matchup. ################')
            past_periods = {k: v for k, v in past_periods.items() if last_matchup_period - k < 1}

        self.recalculate_scores(past_periods, team_dict)
        self.bot_transactions(current_scoring_period)

if __name__ == '__main__':
    runner = Vermillion_Throw_Rug_Runner('data/cookie.json')
    #runner.run()
    
    runner.bot_transactions(31)
