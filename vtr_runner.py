# -*- coding: utf-8 -*-

"""
Vermillion Throw Rug Runner 2026

This script automates various tasks for the Vermillion Throw Rug fantasy baseball league for the 2026 season.
It handles all of the league gimmicks, as well as the bot behavior.

Features:
- Updates stat point values randomly each matchup.
- Recalculates scores for past matchups based on updated (and past) scoring settings.
- Manages bot team transactions (drops and adds players).
- Sends Discord messages with matchup results, matchup changes based on gimmicks, and scoring setting changes.
"""

import json
import requests
from discord import Intents, Client
from random import random, shuffle, sample
from datetime import date, datetime, timezone
from copy import deepcopy
from numpy import linspace
from os import getenv
from argparse import ArgumentParser
from pathlib import Path

# ESPN API URLs
SETTINGS_UPDATE_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/settings?scoringPeriodId=0'
EMAIL_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/communication/topics'
BOX_SCORE_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?scoringPeriodId={scoring_period}&view=mBoxscore&view=mMatchupScore'
ADJUST_SCORE_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/schedule'
SCHEDULE_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=mMatchupScore&view=mTeam'
TRANSACTION_URL = 'https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512/transactions'
ROSTER_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=mRoster&{roster_for_team_id}'
PLAYERS_URL = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/700691512?view=kona_player_info'

# Position quantities for full roster
BATTER_POS_QUANTITIES = {
    0: 1,  # Catcher
    1: 1,  # First Base
    2: 1,  # Second Base
    3: 1,  # Third Base
    4: 1,  # Shortstop
    8: 1,  # Outfield
    9: 1,  # Outfield
    10: 1,  # Outfield
    11: 1,
}  # Utility
PITCHER_POS_QUANTITIES = {14: 5, 15: 5}  # Starting Pitcher  # Relief Pitcher

# Maximum probability of a bot dropping a player
MAX_DROP_PROB = 0.05

# Team IDs for bot teams
BOT_IDS = [3, 4]


def parse_args():
    """
    Parse command-line arguments for the script.

    Returns:
        args: Parsed arguments namespace containing:
            - cookie_file: Path to file containing ESPN cookies
            - swid: ESPN SWID value
            - espn_s2: ESPN S2 value
            - discord_file: Path to file containing Discord token
            - discord_token: Discord bot token
            - discord_channel: Discord channel ID for messages
            - debug: Enable debug mode (mocks API calls)
            - disable_player_transactions: Skip actual player transactions
    """

    parser = ArgumentParser(description="Vermillion Throw Rug Runner 2026")

    parser.add_argument(
        '--cookie-file', default=None, help='File containing cookie data.'
    )
    parser.add_argument(
        '--swid', default='', help='ESPN SWID. Use if not using cookie file.'
    )
    parser.add_argument(
        '--espn-s2', default='', help='ESPN-S2. Use if not using cookie file.'
    )
    parser.add_argument(
        '--discord-file', default=None, help='File containing discord token.'
    )
    parser.add_argument(
        '--discord-token',
        default='',
        help='Discord token. Use if not using discord file.',
    )
    parser.add_argument(
        '--discord-channel', type=int, default=None, help='Discord channel ID.'
    )
    parser.add_argument('-d', '--debug', action='store_true', help='Debug.')
    parser.add_argument(
        '-p',
        '--disable_player_transactions',
        action='store_true',
        help='Disable player transactions.',
    )

    args = parser.parse_args()

    # Print configuration summary.
    print('################ Arguments ################')
    print(f'Debug: {args.debug}')
    print(f'Disable Player Transactions: {args.disable_player_transactions}')
    print(f'Cookie file: {args.cookie_file}')
    if args.swid == '':
        print('SWID not provided')
    else:
        print('SWID provided')
    if args.espn_s2 == '':
        print('ESPN-S2 not provided')
    else:
        print('ESPN-S2 provided')
    print(f'Discord file: {args.discord_file}')
    if args.discord_token == '':
        print('Discord token not provided.')
    else:
        print('Discord token provided.')
    print(f'Discord channel ID: {args.discord_channel}')

    # Validate args.
    if args.cookie_file is None and (args.swid == '' or args.espn_s2 == ''):
        raise ValueError(
            'You must either provide cookie file (--cookie-file) or both SWID (--swid) and ESPN-S2 (--espn-s2).'
        )
    if args.cookie_file is not None and (args.swid != '' or args.espn_s2 != ''):
        raise ValueError(
            'You may not provide cookie file (--cookie-file) and either SWID (--swid) or ESPN-S2 (--espn-s2).'
        )
    if args.discord_file is None and args.discord_token == '':
        raise ValueError(
            'You must either provide discord file (--discord-file) or discord token (--discord-token).'
        )
    if args.discord_file is not None and args.discord_token != '':
        raise ValueError(
            'You may not provide discord file (--discord_file) and discord token (--discord-token).'
        )

    return args


def build_cookies(args):
    """
    Build cookies dictionary from arguments.

    Args:
        args: Parsed command-line arguments

    Returns:
        dict: Cookies dictionary with 'SWID' and 'espn_s2' keys
    """

    if args.cookie_file is not None:
        with open(args.cookie_file) as f:
            return json.load(f)
    return {'SWID': args.swid, 'espn_s2': args.espn_s2}


def first_digit_even(number):
    """
    Check if the first digit of a number is even.

    Args:
        number: The number to check

    Returns:
        bool: True if first digit is even, False otherwise
    """

    first_digit = int(str(abs(number))[0])
    return first_digit % 2 == 0


def point_update_email_content(default_scores, updated_scores):
    """
    Generate email content for stat point updates.

    Args:
        default_scores: Original scoring settings
        updated_scores: Updated scoring settings

    Returns:
        tuple: (email_content, removed_stats_str, doubled_stats_str)
            email_content: Formatted email content string with changes
            removed_stats_str: String listing removed stats
            doubled_stats_str: String listing stats with updated point values
    """

    removed = []
    doubled = []

    with open('data/stat_ids.json') as f:
        stat_ids = json.load(f)

    with open('data/email_content.txt') as f:
        email_content = f.read()

    default_dict = {
        str(s['statId']): s['points']
        for s in default_scores['scoringSettings']['scoringItems']
    }
    updated_dict = {
        str(s['statId']): s['points']
        for s in updated_scores['scoringSettings']['scoringItems']
    }

    for id, stat in stat_ids.items():
        if id in updated_dict:
            doubled.append((stat, default_dict[id], updated_dict[id]))
        else:
            removed.append(stat)

    removed_str = '    ' + '\n    '.join(removed)
    doubled_strs = [f'{stat}: ({old} -> {new})' for stat, old, new in doubled]
    doubled_str = '    ' + '\n    '.join(doubled_strs)
    email_content = email_content.format(
        date=date.today(), removed=removed_str, doubled=doubled_str
    )
    return email_content, removed_str, doubled_str


def point_update_message(default_scores, updated_scores, matchup):
    """
    Generate message content for stat point updates.

    Args:
        default_scores: Original scoring settings
        updated_scores: Updated scoring settings
        matchup: Matchup period ID

    Returns:
        tuple: (message_content, removed_stats_str, doubled_stats_str)
            message_content: Formatted message content string with changes
            removed_stats_str: String listing removed stats
            doubled_stats_str: String listing stats with updated point values
    """

    removed = []
    doubled = []

    with open('data/stat_ids.json') as f:
        stat_ids = json.load(f)

    with open('data/stat_updates_message_template.txt') as f:
        message_template = f.read()

    default_dict = {
        str(s['statId']): s['points']
        for s in default_scores['scoringSettings']['scoringItems']
    }
    updated_dict = {
        str(s['statId']): s['points']
        for s in updated_scores['scoringSettings']['scoringItems']
    }

    for id, stat in stat_ids.items():
        if id in updated_dict:
            doubled.append((stat, default_dict[id], updated_dict[id]))
        else:
            removed.append(stat)

    removed_str = '    ' + '\n    '.join(removed)
    doubled_strs = [f'{stat}: ({old} -> {new})' for stat, old, new in doubled]
    doubled_str = '    ' + '\n    '.join(doubled_strs)
    message = message_template.format(
        matchup=matchup, removed=removed_str, doubled=doubled_str
    )
    return message, removed_str, doubled_str


def get_point_bonus_message(team, score):
    """
    Generate message string based on if a team's score starts with an even or odd digit.

    Args:
        team: Team abbreviation
        score: Current score

    Returns:
        tuple: (adjusted_score, message)
            adjusted_score: Score after applying bonus/penalty
            message: String describing the adjustment made
    """

    if first_digit_even(score):
        score = round(score + 100, 1)
        return (
            score,
            f'{team} score starts with an even number, so receives 100-point bonus; Ends at {score}.',
        )
    else:
        score = round(score - 100, 1)
        return (
            score,
            f'{team} score starts with an odd number, so incurs 100-point penalty; Ends at {score}.',
        )


def get_score_adjustment(team_data, settings, reason):
    """
    Calculate necessary score adjustment for a team based on updated scoring settings.

    Args:
        team_data: Team matchup data from ESPN API
        settings: Scoring settings from this matchup
        reason: Template reason string for the adjustment

    Returns:
        tuple: (adjustment_dict, adjustment_changed_flag)
            adjustment_dict: Dictionary with 'adjustment', 'adjustmentReason', and 'teamId' keys for score adjustment API request
            adjustment_changed_flag: Boolean indicating if the adjustment has changed from the current adjustment
    """

    current_adjustment = team_data['adjustment']
    current_score = team_data['totalPoints'] - current_adjustment
    team_id = team_data['teamId']
    score = 0
    for player in team_data['rosterForMatchupPeriod']['entries']:
        stats = player['playerPoolEntry']['player']['stats'][0]['stats']
        score += sum(
            settings.get(id, 0) * val
            for id, val in stats.items()
            if isinstance(val, (int, float))
        )
    score = round(score, 1)
    if first_digit_even(score):
        score += 100
        reason = reason.format(adjustment="even-first-digit bonus (+100)")
    else:
        score -= 100
        reason = reason.format(adjustment="odd-first-digit penalty (-100)")
    adjustment = round(score - current_score, 1)
    adjustment_dict = {
        'adjustment': adjustment,
        'adjustmentReason': reason,
        'teamId': team_id,
    }
    return adjustment_dict, (adjustment != current_adjustment)


def player_filter(pos, num):
    """
    Create filter dictionary for querying available players by position.

    Args:
        pos: Position ID to filter for
        num: Number of players to limit results to

    Returns:
        dict: Filter configuration to use for player search API request
    """

    return {
        'players': {
            'filterStatus': {'value': ['FREEAGENT']},
            'filterInjured': {'value': False},
            'filterSlotIds': {'value': [pos]},
            'limit': num,
            'offset': 0,
            'sortAppliedStatTotal': {
                'sortAsc': False,
                'sortPriority': 1,
                'value': '002026',
            },
            'filterStatsForTopScoringPeriodIds': {
                'value': 1,
                'additionalValue': ['002026'],
            },
        }
    }


def find_necessary_transactions(
    players, pos_quantities, results=None, assignments=None, idx=0
):
    """
    Recursively find optimal player assignments to positions, minimizing drops.

    Uses backtracking to try different position assignments for each player,
    collecting all valid solutions and selecting the one with fewest drops
    and lowest ownership percentage among dropped players.

    Args:
        players: List of player dictionaries
        pos_quantities: Dict mapping position IDs to required quantities
        results: Accumulator for valid assignment combinations
        assignments: Current position assignments
        idx: Current player index being processed

    Returns:
        tuple: (pickups_dict, drops_set, assignments_dict) for optimal solution
    """

    if results is None:
        results = []
    if assignments is None:
        assignments = {}

    # Base case: all players processed. Evaluate the assignment.
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

    # General case: try assigning current player to each eligible position or choosing to skip/drop.
    player = players[idx]
    id = player['playerId']
    for position in player['playerPoolEntry']['player']['eligibleSlots']:
        if position in pos_quantities and pos_quantities[position] > 0:
            if list(assignments.values()).count(position) < pos_quantities[position]:
                assignments[id] = position
                find_necessary_transactions(
                    players, pos_quantities, results, assignments, idx + 1
                )
                del assignments[id]
    find_necessary_transactions(players, pos_quantities, results, assignments, idx + 1)

    # Terminal case: after exploring all possibilities, select optimal result.
    if idx == 0:
        percent_owned_dict = {
            p['playerId']: p['playerPoolEntry']['player']['ownership']['percentOwned']
            for p in players
        }
        min_dropped_players = min(len(r[1]) for r in results)
        acceptable_results = [r for r in results if len(r[1]) == min_dropped_players]
        best_result = min(
            acceptable_results, key=lambda x: sum(percent_owned_dict[p] for p in x[1])
        )
        return best_result

    return {}, set(), {}


def setup_directories():
    """
    Create output and debug directories if they don't already exist.
    """

    path = Path('output')
    path.mkdir(exist_ok=True)

    path = Path('debug')
    path.mkdir(exist_ok=True)


class Mock_Http_Response:
    """
    Mock HTTP response class for debugging purposes.
    Simulates necessary fields/methods of requests.Response object.
    """

    def __init__(self, ok, status_code, data):
        self.ok = ok
        self.status_code = status_code
        self.data = data

    def json(self):
        return json.loads(self.data)


class Vermillion_Throw_Rug_Runner:
    """
    Main class for running league operations.

    Handles all interactions with ESPN Fantasy API and Discord API, including:
    - Updates stat point values randomly each matchup.
    - Recalculates scores for past matchups based on updated (and past) scoring settings.
    - Manages bot team transactions (drops and adds players).
    - Sends Discord messages with matchup results, matchup changes based on gimmicks, and scoring setting changes.
    """

    def __init__(
        self,
        cookies,
        discord_token,
        discord_channel,
        debug,
        disable_player_transactions,
    ):
        """
        Initialize the runner with authentication and configuration.

        Args:
            cookies: ESPN authentication cookie dict
            discord_token: Discord bot token
            discord_channel: Discord channel ID for messages
            debug: Enable debug mode (no API POSTs)
            disable_player_transactions: Skip actual player transactions
        """

        self.cookies = cookies
        self.discord_token = discord_token
        self.discord_channel = discord_channel
        self.debug = debug
        self.disable_player_transactions = disable_player_transactions

    def http_request(self, url, headers={}, data=None, attempts=5):
        """
        Make HTTP request with retry logic.

        Args:
            url: API endpoint URL
            headers: Request headers
            data: Request body data (JSON string)
            attempts: Number of retry attempts

        Returns:
            requests.Response: API response object
        """

        response = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        for attempt in range(attempts):
            if data is None:
                response = requests.get(url, cookies=self.cookies, headers=headers)
            else:
                if self.debug:
                    response = Mock_Http_Response(False, 400, '{}')
                    break
                else:
                    response = requests.post(
                        url, cookies=self.cookies, headers=headers, data=data
                    )
            if response.ok:
                break
        return response

    def send_email(self, recipient, subject, message):
        """
        Send email notification via ESPN API.

        Args:
            recipient: Recipient user ID
            subject: Email subject
            message: Email body content

        Returns:
            requests.Response: API response
        """

        payload = {
            'content': message,
            'subject': subject,
            'type': 'EMAIL',
            'viewableBy': [recipient],
        }
        payload_str = json.dumps(payload)
        response = self.http_request(EMAIL_URL, data=payload_str)
        return response

    def update_stat_points(self, matchup_period_id):
        """
        Randomly update stat point values for the upcoming matchup period.

        Removes a random half of the stats and doubles the point values of the remaining stats.
        Saves updated settings and generates Discord message.

        Args:
            matchup_period_id: Next matchup period ID

        Returns:
            tuple: (updated_scores, message_lines, response)
                updated_scores: Updated scoring settings dict
                message_lines: List of lines for Discord message about the new scoring settings
                response: API response object from the settings update request
        """

        print('################ Updating stat point values. ################')
        # Get default scoring settings (all stats included).
        with open('data/default_stats_scores.json') as f:
            default_scores = json.load(f)

        # Generate updated scoring settings.
        updated_scores = deepcopy(default_scores)
        items = updated_scores['scoringSettings']['scoringItems']
        items = sample(items, len(items) // 2)  # Randomly select half the stats.
        for item in items:
            item['points'] = round(item['points'] * 2, 1)  # Double their point values.
        updated_scores['scoringSettings']['scoringItems'] = items
        updated_scores_str = json.dumps(
            updated_scores
        )  # Generate a JSON string for API request.

        # Scoring settings API request.
        response = self.http_request(SETTINGS_UPDATE_URL, data=updated_scores_str)

        # Generate scoring update message.
        message, removed_str, doubled_str = point_update_message(
            default_scores, updated_scores, matchup_period_id
        )

        if response.ok:
            # On successful update, save the new scoring settings.
            print('Success updating stat point values.')
            items_dict = {item['statId']: item['points'] for item in items}
            if not self.debug:
                with open('data/matchup_settings.json') as f:
                    matchup_settings = json.load(f)
                matchup_settings[str(matchup_period_id)] = items_dict
                with open('data/matchup_settings.json', 'w') as f:
                    json.dump(matchup_settings, f, indent=2)
        else:
            # ON failure, print warning, save the API call payload, and print the intended changes.
            print(
                f'WARNING: Unable to update point values (status code {response.status_code}).'
            )
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

        return updated_scores, message.split('\n\n'), response

    def get_box_scores(self, scoring_period, matchup_period):
        """
        Retrieve box scores for a specific matchup period.

        Args:
            scoring_period: Scoring period ID
            matchup_period: Matchup period ID

        Returns:
            dict: Box score data from ESPN API
        """

        url = BOX_SCORE_URL.format(scoring_period=scoring_period)
        filter = (
            '{"schedule":{"filterMatchupPeriodIds":{"value":['
            + str(matchup_period)
            + ']}}}'
        )
        headers = {'x-fantasy-filter': filter}
        response = self.http_request(url, headers)
        data = response.json()
        return data

    def recalculate_scores(self, past_periods, team_dict):
        """
        Recalculate scores for past matchups using updated and past scoring settings.

        Args:
            past_periods: Dict mapping matchup periods to their last scoring periods
            team_dict: Dict mapping team IDs to team abbreviations
        """

        print(
            '################ Recalculating scores for past matchups. ################'
        )
        print(f'Matchups: {list(past_periods)}')

        # Get scoring settings for past matchups.
        with open('data/matchup_settings.json') as f:
            matchup_settings = json.load(f)

        # Generate template for score adjustment reason.
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
        reason = f'Maintaining original matchup score as of {now_str}. Includes {{adjustment}}.'

        # Determine necessary score adjustments for each past matchup and build payload for score adjustment API request.
        adjustments = []
        adjustments_debug = {}
        for matchup_period, scoring_period in past_periods.items():
            adjustments_debug[matchup_period] = []
            data = self.get_box_scores(scoring_period, matchup_period)
            settings = matchup_settings[str(matchup_period)]
            for matchup in data['schedule']:
                id = matchup['id']
                home_adjustment, home_adjustment_changed = get_score_adjustment(
                    matchup['home'], settings, reason
                )
                away_adjustment, away_adjustment_changed = get_score_adjustment(
                    matchup['away'], settings, reason
                )
                if home_adjustment_changed or away_adjustment_changed:
                    adjustments.append(
                        {'away': away_adjustment, 'home': home_adjustment, 'id': id}
                    )
                    adjustments_debug[matchup_period].append(
                        {
                            'id': id,
                            'away': team_dict[away_adjustment['teamId']],
                            'home': team_dict[home_adjustment['teamId']],
                            'away_adjustment': away_adjustment['adjustment'],
                            'home_adjustment': home_adjustment['adjustment'],
                        }
                    )
                    print(f'Recalculated score and adjustments for matchup {id}.')
                else:
                    print(
                        f'Recalculated score for matchup {id}. No adjustments required.'
                    )

        # Make API request if any adjustments are needed.
        if len(adjustments) > 0:
            response = self.http_request(ADJUST_SCORE_URL, data=json.dumps(adjustments))
            if response.ok:
                print('Adjusted all past scores.')
            else:
                # On failure, print warning, save the API call payload, and print the intended adjustments.
                print(
                    f'WARNING: Failed to adjust past scores (status code {response.status_code}).'
                )
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
                        print(
                            f'        {matchup["away"]}: {matchup["away_adjustment"]}'
                        )
                        print(
                            f'        {matchup["home"]}: {matchup["home_adjustment"]}'
                        )
        else:
            print('No adjustments needed.')

        print('Done recalculating scores for past matchups.')

    def get_basic_info(self):
        """
        Retrieve basic league information including current scoring period and past matchups.

        Returns:
            tuple: (past_periods_dict, current_scoring_period, team_dict)
                past_periods_dict: Dict mapping past matchup periods to their last scoring periods
                current_scoring_period: Current scoring period ID
                team_dict: Dict mapping team IDs to team abbreviations
        """

        print('################ Getting basic league info. ################')

        # API request.
        response = self.http_request(SCHEDULE_URL)
        data = response.json()

        # Build dict mapping past matchup periods to their last scoring periods.
        current_scoring_period = data['scoringPeriodId']
        schedule = data['schedule']
        past_periods = {}
        for matchup in schedule:
            matchup_period = matchup['matchupPeriodId']
            if matchup_period not in past_periods:
                points_by_scoring_period = matchup.get('home', {}).get(
                    'pointsByScoringPeriod', {}
                )
                scoring_periods = [int(p) for p in points_by_scoring_period]
                if len(scoring_periods) > 0 and all(
                    p < current_scoring_period for p in scoring_periods
                ):
                    past_periods[matchup_period] = max(scoring_periods)

        # Build team dict mapping team IDs to abbreviations.
        teams = data['teams']
        team_dict = {t['id']: t['abbrev'] for t in teams}
        team_str = '  ' + '\n  '.join(f'{k}: {v}' for k, v in team_dict.items())

        # Print league info.
        print(f'Current scoring period: {current_scoring_period}')
        print(f'Current matchup: {max(past_periods) + 1}')
        print(f'Teams:\n{team_str}')
        print('Done getting basic league info.')

        return past_periods, current_scoring_period, team_dict

    def drop_best_player(self, team, current_scoring_period, team_dict):
        """
        Drop the highest-scoring player for a certain matchup from a team.

        Args:
            team: Team matchup data
            current_scoring_period: Current scoring period ID
            team_dict: Mapping of team IDs to abbreviations

        Returns:
            str: Name of the dropped player
        """

        # Determine the best player based on the matchup data.
        team_id = team['teamId']
        players = team['rosterForMatchupPeriod']['entries']
        player_scores = {
            p['playerPoolEntry']['id']: (
                p['playerPoolEntry']['player']['fullName'],
                p['playerPoolEntry']['player']['stats'][0]['appliedTotal'],
            )
            for p in players
        }
        best_player_id = max(player_scores, key=lambda x: player_scores[x][1])
        best_player_name = player_scores[best_player_id][0]

        # API request
        payload = {
            'isLeagueManager': True,
            'teamId': team_id,
            'type': 'ROSTER',
            'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
            'scoringPeriodId': current_scoring_period,
            'executionType': 'EXECUTE',
            'items': [
                {'playerId': best_player_id, 'type': 'DROP', 'fromTeamId': team_id}
            ],
        }
        payload_str = json.dumps(payload)
        if not self.disable_player_transactions:
            response = self.http_request(TRANSACTION_URL, data=payload_str)
            if response.ok:
                print(f'Dropped {best_player_name} from {team_dict[team_id]}.')
            else:
                print(
                    f'WARNING: Unable to drop {best_player_name} from {team_dict[team_id]} (status code {response.status_code}).'
                )
        else:
            print(f'Would have dropped {best_player_name} from {team_dict[team_id]}.')

        return best_player_name

    def last_week_results(
        self, scoring_period, matchup_period, current_scoring_period, team_dict
    ):
        """
        For each matchup, applies score adjustments based on first digit parity,
        and drops best players if score difference has odd first digit.

        Args:
            scoring_period: Scoring period for the matchup
            matchup_period: Matchup period ID
            current_scoring_period: Current scoring period ID
            team_dict: Mapping of team IDs to abbreviations

        Returns:
            list: Discord message strings
        """

        print(
            f'################ Checking and dropping best players from matchup period {matchup_period}. ################'
        )

        # Setup message template
        messages = [f'__**MATCHUP {matchup_period} RESULTS/UPDATES**__']
        with open('data/matchup_message_template.txt') as f:
            message_template = f.read()

        # Get box scores for the matchup period.
        data = self.get_box_scores(scoring_period, matchup_period)
        schedule = data['schedule']

        for matchup in schedule:
            # Get team IDs and initial scores.
            away_team = team_dict[matchup['away']['teamId']]
            home_team = team_dict[matchup['home']['teamId']]
            away_initial_score = matchup['away']['totalPoints']
            home_initial_score = matchup['home']['totalPoints']
            score_difference = round(abs(home_initial_score - away_initial_score), 1)

            if first_digit_even(score_difference):
                diff_parity = 'even'
                drops_message = 'So no drops.'
            else:
                # Drop players if score difference starts with odd digit.
                away_player = self.drop_best_player(
                    matchup['away'], current_scoring_period, team_dict
                )
                home_player = self.drop_best_player(
                    matchup['home'], current_scoring_period, team_dict
                )
                diff_parity = 'odd'
                drops_message = (
                    f'{away_team} drops {away_player}. {home_team} drops {home_player}.'
                )

            # Get message lines for each team.
            away_final_score, away_adjustment_line = get_point_bonus_message(
                away_team, away_initial_score
            )
            home_final_score, home_adjustment_line = get_point_bonus_message(
                home_team, home_initial_score
            )

            # Add message for this matchup to the full list of messages.
            messages.append(
                message_template.format(
                    away=away_team,
                    home=home_team,
                    away_initial=away_initial_score,
                    home_initial=home_initial_score,
                    diff=score_difference,
                    diff_parity=diff_parity,
                    drops=drops_message,
                    away_adjustment=away_adjustment_line,
                    home_adjustment=home_adjustment_line,
                    away_final=away_final_score,
                    home_final=home_final_score,
                )
            )

        print('Done checking and dropping players.')
        return messages

    def select_players(self, pos, num):
        """
        Select available player(s) for a given position.

        Finds [num] * 1.5 + 3 best available players for the position (based on season total points with current scoring system).
        Randomly selects [num] of those players and returns their IDs.

        Args:
            pos: Position ID to filter for
            num: Number of players to select

        Returns:
            list: List of selected player IDs
        """

        num_options = round(num * 1.5 + 3)
        filter = player_filter(pos, num_options)
        headers = {'x-fantasy-filter': json.dumps(filter)}
        response = self.http_request(PLAYERS_URL, headers)
        data = response.json()
        players = sample(data['players'], num)
        ids = [p['id'] for p in players]
        return ids

    def get_roster(self, team_ids):
        """
        Get roster data for specified teams.

        Args:
            team_ids: List of team IDs to get rosters for

        Returns:
            list: List of team roster data
        """

        roster_for_team_id = '&'.join(f'rosterForTeamId={id}' for id in team_ids)
        url = ROSTER_URL.format(roster_for_team_id=roster_for_team_id)
        response = self.http_request(url)
        data = response.json()
        teams = [t for t in data['teams'] if t['id'] in team_ids]
        return teams

    def bot_transactions(self, scoring_period):
        """
        Perform automated transactions for bot teams.

        Process includes:
        - Drop injured/suspended players and other random players.
            - Random drop probability scales linearly with player rank on the roster, according to total season points (with current scoring system).
            - Best player has 0 drop probability, worst player has [MAX_DROP_PROB] drop probability.
        - Optimize roster positions using backtracking algorithm.
        - Add replacement players for needed positions.
        - Adjust player lineups to match valid positions.

        Args:
            scoring_period: Current scoring period ID
        """

        print('################ Bot transactions. ################')

        # Get rosters for bot teams and randomize order to ensure fairness between bots' free agent adds.
        teams = self.get_roster(BOT_IDS)
        shuffle(teams)

        for team in teams:
            team_id = team['id']
            print(f'Team ID {team_id}')

            # Sort players by total season points with current scoring system. Create drop probabilities according to rank within roster. Best player has 0 drop probability.
            players = sorted(
                team['roster']['entries'],
                key=lambda x: x['playerPoolEntry']['player']['stats'][1][
                    'appliedTotal'
                ],
                reverse=True,
            )
            drop_probs = linspace(0, MAX_DROP_PROB, len(players))

            # Drop injured or suspended players, plus some random players.
            drops = []
            for n, p in enumerate(players):
                player = p['playerPoolEntry']['player']
                drop_prob = drop_probs[n]
                if (
                    player['injured']
                    or player['injuryStatus'] == 'SUSPENSION'
                    or random() <= drop_prob
                ):
                    drops.append(player['id'])

            # Split batters and pitchers. Shohei counts as a batter because otherwise he's too annoying to deal with.
            batters = [
                p
                for p in players
                if 12 in p['playerPoolEntry']['player']['eligibleSlots']
                and p['playerId'] not in drops
            ]
            pitchers = [
                p
                for p in players
                if 12 not in p['playerPoolEntry']['player']['eligibleSlots']
                and p['playerId'] not in drops
            ]

            # Generate position arrangements with backtracking algorithm, get necessary adds, drops, and position assignments.
            print('Arranging batters')
            batter_adds, batter_drops, batter_assignments = find_necessary_transactions(
                batters, BATTER_POS_QUANTITIES
            )
            print('Arranging pitchers')
            pitcher_adds, pitcher_drops, pitcher_assignments = (
                find_necessary_transactions(pitchers, PITCHER_POS_QUANTITIES)
            )
            drops.extend(batter_drops)
            drops.extend(pitcher_drops)
            pos_adds = batter_adds | pitcher_adds
            assignments = batter_assignments | pitcher_assignments

            # Drops (Can be done all at once).
            if len(drops) > 0:
                drop_items = [
                    {'playerId': id, 'type': 'DROP', 'fromTeamId': team_id}
                    for id in drops
                ]
                payload = {
                    'isLeagueManager': False,
                    'teamId': team_id,
                    'type': 'ROSTER',
                    'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                    'scoringPeriodId': scoring_period,
                    'executionType': 'EXECUTE',
                    'items': drop_items,
                }
                payload_str = json.dumps(payload)
                if not self.disable_player_transactions:
                    response = self.http_request(TRANSACTION_URL, data=payload_str)
                    if response.ok:
                        print(f'Completed drops for bot team ID {team_id}.')
                    else:
                        # On failure, print warning and save the API call payload.
                        print(
                            f'WARNING: Unable to perform drops for bot team ID {team_id} (status code {response.status_code}).'
                        )
                        date_str = str(date.today())
                        filename = f'debug/bot_{team_id}_drops_{date_str}.json'
                        with open(filename, 'w') as f:
                            json.dump(payload, f)
                        print(f'  Saving drops payload to file: {filename}')
            else:
                print(f'No drops for bot team ID {team_id}.')

            # Adds (Must be done one at a time).
            adds = {}
            failed_add_payloads = []
            for pos, num in pos_adds.items():
                if num > 0:
                    print(f'Finding replacement players for position {pos}.')
                    ids = self.select_players(pos, num)
                    for id in ids:
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
                                    'toLineupSlotId': pos,
                                }
                            ],
                        }
                        payload_str = json.dumps(payload)
                        if not self.disable_player_transactions:
                            response = self.http_request(
                                TRANSACTION_URL, data=payload_str
                            )
                            if response.ok:
                                # On success, add player to the list of position assignments.
                                print(f'Added player {id} to bot team ID {team_id}.')
                                assignments[id] = pos
                            else:
                                # On failure, print warning and add API call payload to list of failed adds to save later.
                                print(
                                    f'WARNING: Failed to add player {id} to bot team ID {team_id} (status code {response.status_code}).'
                                )
                                failed_add_payloads.append(payload)

            if sum(pos_adds.values()) == 0:
                print(f'No adds for bot team ID {team_id}.')

            # Save any failed add payloads to file.
            if len(failed_add_payloads) > 0:
                date_str = str(date.today())
                filename = f'debug/bot_{team_id}_adds_{date_str}.json'
                print(f'Saving failed add payloads to file: {filename}')
                with open(filename, 'w') as f:
                    json.dump(failed_add_payloads, f)

            # Re-check lineup
            team = self.get_roster([team_id])[0]
            players = team['roster']['entries']
            current_pos_dict = {p['playerId']: p['lineupSlotId'] for p in players}
            changed_assignments = {
                p: pos for p, pos in assignments.items() if pos != current_pos_dict[p]
            }

            # Lineup assignments
            if len(changed_assignments) > 0:
                move_items = [
                    {'playerId': p, 'type': 'LINEUP', 'toLineupSlotId': pos}
                    for p, pos in changed_assignments.items()
                ]
                payload = {
                    'isLeagueManager': False,
                    'teamId': team_id,
                    'type': 'ROSTER',
                    'memberId': '{0D6FBE9B-65FC-4CD8-A147-25159559E959}',
                    'scoringPeriodId': scoring_period,
                    'executionType': 'EXECUTE',
                    'items': move_items,
                }
                payload_str = json.dumps(payload)
                if not self.disable_player_transactions:
                    response = self.http_request(TRANSACTION_URL, data=payload_str)
                    if response.ok:
                        print(
                            f'Successfully arranged lineup for bot team ID {team_id}.'
                        )
                    else:
                        # On failure, print warning and save the API call payload.
                        print(
                            f'WARNING: Failed to arrange lineup for bot team ID {team_id} (status code {response.status_code}).'
                        )
                        date_str = str(date.today())
                        filename = f'debug/bot_{team_id}_lineup_{date_str}.json'
                        with open(filename, 'w') as f:
                            json.dump(payload, f)
                        print(f'  Saving lineup payload to file: {filename}')
            else:
                print(f'No lineup arrangement needed for bot team ID {team_id}.')

    def send_messages(self, messages):
        """
        Send messages to Discord channel.

        Args:
            messages: List of message strings to send
        """

        print('################ Sending Discord Messages. ################')
        print(f'Sending {len(messages)} total messages.')
        intents = Intents.default()
        client = Client(intents=intents)

        @client.event
        async def on_ready():
            channel = client.get_channel(self.discord_channel)

            for message in messages:
                await channel.send(message)

            await client.close()

        client.run(self.discord_token)
        print('Done sending discord messages')

    def run(self):
        """
        Main execution method for the Vermillion Throw Rug runner.

        Processes:
        - Sets up directories.
        - Gets league info.
        - Updates stat points (only at start of matchup).
        - Processes last week results and drops (only at start of matchup).
        - Recalculates past scores.
        - Performs bot transactions.
        - Sends Discord notifications (only at start of matchup).
        """

        setup_directories()

        # Get basic info.
        past_periods, current_scoring_period, team_dict = self.get_basic_info()
        last_matchup_period = max(past_periods)
        last_matchup_last_scoring_period = past_periods[last_matchup_period]

        messages = []
        if current_scoring_period == last_matchup_last_scoring_period + 1:
            # On first day of new matchup:
            # Update stat points.
            # Process last week results and drops.
            # Recalculate scores for all past matchups.
            # Generate messages to send to Discord.

            print('################ Start of matchup. ################')
            last_week_results_messages = self.last_week_results(
                last_matchup_last_scoring_period,
                last_matchup_period,
                current_scoring_period,
                team_dict,
            )
            _, stat_updates_messages, _ = self.update_stat_points(
                last_matchup_period + 1
            )

            date_str = str(date.today())
            filename = f'output/last_week_results_{date_str}.txt'
            with open(filename, 'w') as f:
                f.write('\n\n'.join(last_week_results_messages))
            filename = f'output/stat_updates_{date_str}.txt'
            with open(filename, 'w') as f:
                f.write('\n\n'.join(stat_updates_messages))
        else:
            # On later day of matchup, only recalculate scores for last matchup.
            # Since ESPN stat corrections can only happen up to 7 days after a game, only the last matchup scores might need to be recalculated.

            print('################ Not start of matchup. ################')
            past_periods = {
                k: v for k, v in past_periods.items() if last_matchup_period - k < 1
            }

        self.recalculate_scores(past_periods, team_dict)
        self.bot_transactions(current_scoring_period)

        messages = last_week_results_messages + stat_updates_messages
        self.send_messages(messages)


if __name__ == '__main__':
    # Parse command-line arguments
    ARGS = parse_args()

    # Build authentication cookies
    COOKIES = build_cookies(ARGS)

    # Load Discord token
    if ARGS.discord_file is not None:
        with open(ARGS.discord_file) as f:
            DISCORD_TOKEN = f.read()
    else:
        DISCORD_TOKEN = ARGS.discord_token

    # Initialize and run the main runner.
    RUNNER = Vermillion_Throw_Rug_Runner(
        COOKIES,
        DISCORD_TOKEN,
        ARGS.discord_channel,
        ARGS.debug,
        ARGS.disable_player_transactions,
    )
    RUNNER.run()
