import argparse
import glob
import os
import random

from tornado import ioloop, gen
import ujson as json

from diplomacy_research.players.player import Player
from diplomacy_research.utils.cluster import is_port_opened, kill_processes_using_port, stop_io_loop

from bench import generate_daide_game, generate_gym_game, \
                  get_client_channel, start_server, \
                  reset_unsync_wait, run_benchmark, PLAYER_FACTORIES, OPEN_PORTS, ClientWrapper
from stats.save_games import save_games
from stats.cross_convoy_stats import print_cross_convoy_stats
from stats.cross_support_stats import print_cross_support_stats
from stats.ranking_stats import print_ranking_stats

def callback_array(games, callbacks):
    for cb in callbacks:
        cb(games)

@gen.coroutine
def _get_benchmark_args(ai_1, ai_2, args):
    name = '1[{}]v6[{}]'.format(ai_1.name, ai_2.name)
    players = [ai_1, ai_2, ai_2, ai_2, ai_2, ai_2, ai_2]

    callbacks = []
    for stats_name in args.stats:
        if stats_name == 'save_games':
            stats_callback = lambda games: save_games(args.save_dir, games)
        elif stats_name == 'cross_convoy':
            stats_callback = lambda games: print_cross_convoy_stats(name, games)
        elif stats_name == 'cross_support':
            stats_callback = lambda games: print_cross_support_stats(name, games)
        elif stats_name == 'ranking':
            stats_callback = lambda games: print_ranking_stats(name, games)
        else:
            continue

        callbacks.append(stats_callback)

    callback = lambda games: callback_array(games, callbacks)

    if 'daide' in args.ai_1 + args.ai_2:
        players = [ClientWrapper(player, None) for player in players]

        for i, (player, _) in enumerate(players):
            if isinstance(player, Player):
                channel = yield get_client_channel()
                players[i] = ClientWrapper(player, channel)
                break

        game_generator = \
            lambda players, progress_bar: generate_daide_game(players, progress_bar, args.rules)

    else:
        game_generator = generate_gym_game

    return (game_generator, players, args.games, callback)

IO_LOOP = None

@gen.coroutine
def main():
    """ Entry point """
    global IO_LOOP

    yield gen.sleep(2)
    try:
        ai_1 = yield PLAYER_FACTORIES[args.ai_1].make()

        if args.ai_2 == args.ai_1:
            ai_2 = ai_1
        else:
            ai_2 = yield PLAYER_FACTORIES[args.ai_2].make()

        game_generator, players, games, callback = yield _get_benchmark_args(ai_1, ai_2, args)
        reset_unsync_wait()
        yield run_benchmark(game_generator, players, games, stats_callback=callback)

    except Exception as exception:
        print('Exception:', exception)
    finally:
        stop_io_loop(IO_LOOP)

if __name__ == '__main__':
    ai_names = sorted(name for name in PLAYER_FACTORIES)

    parser = argparse.ArgumentParser(description='Diplomacy ai bench',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--ai-1', default='dumbbot', choices=ai_names,
                        help='One of the following ai choices: ' + ' | '.join(ai_names))
    parser.add_argument('--ai-2', default='random', choices=ai_names,
                        help='One of the following ai choices: ' + ' | '.join(ai_names))
    parser.add_argument('--games', default=10, type=int,
                        help='number of pair of games to run')
    parser.add_argument('--stats', default='ranking',
                        help='a comma separated list of stats to get: ' +
                             ' | '.join(['cross_convoy',
                                         'cross_support',
                                         'ranking']))
    parser.add_argument('--save-dir', default=None,
                        help='the directory to save games')
    parser.add_argument('--existing-games-dir', default=None,
                        help='the directory containing the games to load instead '
                             'of running new games')
    parser.add_argument('--rules', default='NO_PRESS,IGNORE_ERRORS,POWER_CHOICE',
                        help='Game rules')
    args = parser.parse_args()

    args.stats = [stat for stat in args.stats.split(',') if stat]
    args.rules = [rule for rule in args.rules.split(',') if rule]

    if args.save_dir:
        args.stats = ['save_games'] + args.stats

    if args.existing_games_dir:
        games = []
        glob_pattern = os.path.join(args.existing_games_dir, "game_*.json")
        filenames = glob.glob(glob_pattern)
        for filename in filenames:
            with open(filename, "r") as file:
                content = file.read()
            games.append(json.loads(content.rstrip('\n')))

        args.ai_1 = None
        args.ai_2 = None
        args.games = len(games)
        try: args.stats.remove('save_games')
        except ValueError: pass
        args.save_dir = None
        args.rules = None
        print('--ai-1=[{}] --ai-2=[{}] --games=[{}] --stats=[{}] --save-dir=[{}] --existing-games-dir=[{}] --rules=[{}]'
              .format(args.ai_1, args.ai_2, args.games, args.stats, args.save_dir, args.existing_games_dir, args.rules))

        callbacks = []
        name = os.path.abspath(glob_pattern)
        for stats_name in args.stats:
            if stats_name == 'cross_convoy':
                stats_callback = lambda games: print_cross_convoy_stats(name, games)
            elif stats_name == 'cross_support':
                stats_callback = lambda games: print_cross_support_stats(name, games)
            elif stats_name == 'ranking':
                stats_callback = lambda games: print_ranking_stats(name, games)
            else:
                continue

            callbacks.append(stats_callback)

        callback_array(games, callbacks)

    else:
        print('--ai-1=[{}] --ai-2=[{}] --games=[{}] --stats=[{}] --save-dir=[{}] --existing-games-dir=[{}] --rules=[{}]'
              .format(args.ai_1, args.ai_2, args.games, args.stats, args.save_dir, args.existing_games_dir, args.rules))

        IO_LOOP = ioloop.IOLoop.instance()
        IO_LOOP.spawn_callback(main)
        try:
            start_server(IO_LOOP)
        except KeyboardInterrupt:
            pass
        finally:
            stop_io_loop(IO_LOOP)
            for port in OPEN_PORTS:
                if is_port_opened(port):
                    kill_processes_using_port(port, force=True)
